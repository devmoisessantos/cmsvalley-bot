"""Validação de trilhas, advertências e registro de promoção."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.config import (
    CARGOS,
    CARGOS_PUNICOES,
    TRILHAS_PROMOCAO,
)
from src.cursos.cursos_service import (
    listar_cursos_que_faltam,
    rotulo_curso,
)
from src.database.connection import async_session
from src.database.models import (
    HistoricoPromocao,
    SolicitacaoPromocao,
    agora,
)
from src.plantao.ranking_plantao_service import obter_segundos_plantao_totais
from src.utils.formatacao import formatar_hms
from src.utils.logger import log_mudanca_cargo
from src.utils.nickname import aplicar_prefixo

logger = logging.getLogger(__name__)

CARGOS_ADV_BLOQUEIAM = ("🚫┇Adv 01", "🚫┇Adv 02")


def listar_trilhas() -> list[dict]:
    """Todas as trilhas cadastradas em TRILHAS_PROMOCAO."""
    return list(TRILHAS_PROMOCAO)


def obter_trilha(chave: str) -> dict | None:
    """Busca uma trilha pela chave (ex.: enfermeiro_paramedico)."""
    for trilha in TRILHAS_PROMOCAO:
        if trilha["chave"] == chave:
            return trilha
    return None


def listar_cargos_destino() -> list[str]:
    """
    Cargos-alvo únicos das trilhas (ordem de cadastro).
    Usado no select de “cargo pretendido” do painel.
    """
    vistos: set[str] = set()
    lista: list[str] = []
    for trilha in TRILHAS_PROMOCAO:
        destino = trilha.get("para_cargo") or ""
        if destino and destino not in vistos:
            vistos.add(destino)
            lista.append(destino)
    return lista


def trilhas_para_cargo_destino(nome_cargo: str) -> list[dict]:
    """Trilhas que terminam no cargo informado."""
    return [
        trilha for trilha in TRILHAS_PROMOCAO if trilha.get("para_cargo") == nome_cargo
    ]


def trilhas_a_partir_do_membro(membro: discord.Member) -> list[dict]:
    """Trilhas cujo cargo de origem o membro possui agora (botão Seguir trilha)."""
    disponiveis: list[dict] = []
    for trilha in TRILHAS_PROMOCAO:
        if membro_tem_cargo_nome(membro, trilha["de_cargo"]):
            disponiveis.append(trilha)
    return disponiveis


def obter_trilha_por_destino_e_origem(
    cargo_para: str,
    membro: discord.Member,
) -> dict | None:
    """
    Escolhe a trilha que leva ao cargo pretendido,
    preferindo a que combina com o cargo atual do membro.
    """
    candidatas = trilhas_para_cargo_destino(cargo_para)
    if not candidatas:
        return None
    for trilha in candidatas:
        if membro_tem_cargo_nome(membro, trilha["de_cargo"]):
            return trilha
    return candidatas[0]


def id_cargo_por_nome(nome: str) -> int | None:
    if not nome:
        return None
    if nome in CARGOS:
        return CARGOS.get(nome)
    # tolerância a espaços / variação de grafia
    alvo = "".join(str(nome).split()).lower()
    for chave, valor in CARGOS.items():
        if "".join(str(chave).split()).lower() == alvo:
            return valor
    return None


def _nomes_cargo_equivalentes(a: str, b: str) -> bool:
    return "".join(str(a).split()).lower() == "".join(str(b).split()).lower()


def resolver_cargo_na_guilda(
    guilda: discord.Guild,
    nome_cargo: str,
) -> discord.Role | None:
    """Resolve Role pelo config CARGOS ou pelo nome na guilda."""
    cargo_id = id_cargo_por_nome(nome_cargo)
    if cargo_id is not None:
        cargo = guilda.get_role(int(cargo_id))
        if cargo is not None:
            return cargo
    for cargo in guilda.roles:
        if _nomes_cargo_equivalentes(cargo.name, nome_cargo):
            return cargo
    return None


def membro_tem_cargo_nome(membro: discord.Member, nome_cargo: str) -> bool:
    cargo_id = id_cargo_por_nome(nome_cargo)
    if cargo_id is None:
        return False
    return any(cargo.id == cargo_id for cargo in membro.roles)


def membro_tem_advertencia_bloqueante(membro: discord.Member) -> list[str]:
    """Retorna nomes das adv ativas que bloqueiam promoção."""
    bloqueios: list[str] = []
    for nome in CARGOS_ADV_BLOQUEIAM:
        cargo_id = CARGOS_PUNICOES.get(nome)
        if cargo_id is None:
            continue
        if any(cargo.id == cargo_id for cargo in membro.roles):
            bloqueios.append(nome)
    return bloqueios


def _formatar_falta_legivel(segundos: int) -> str:
    """Ex.: 1h14min20s"""
    segundos = max(0, int(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, segs = divmod(resto, 60)
    partes: list[str] = []
    if horas:
        partes.append(f"{horas}h")
    if minutos or horas:
        partes.append(f"{minutos:02d}min" if horas else f"{minutos}min")
    partes.append(f"{segs:02d}s")
    return "".join(partes) if horas else f"{minutos}min{segs:02d}s"


def montar_checklist_trilha(
    membro: discord.Member,
    trilha: dict,
    *,
    segundos_plantao: int | None = None,
) -> dict:
    """
    Avalia requisitos e monta o corpo do CardView em seções.
    (Situação atual → Cursos → Plantão → Resumo)
    """
    pode_enviar = True
    pendencias: list[str] = []

    # ── Situação atual ─────────────────────────────────────────────
    bloco_situacao: list[str] = ["## 📌 Situação Atual"]

    advs = membro_tem_advertencia_bloqueante(membro)
    if advs:
        pode_enviar = False
        bloco_situacao.append(
            f"- ❌ **Advertência ativa:** {', '.join(f'`{a}`' for a in advs)}"
        )
        pendencias.append("Regularizar advertência (Adv 01 / Adv 02)")
    else:
        bloco_situacao.append("- ✅ Sem advertências (Adv 01 / Adv 02) ativas")

    cargo_de = trilha["de_cargo"]
    cargo_para = trilha["para_cargo"]
    tem_de = membro_tem_cargo_nome(membro, cargo_de)
    tem_para = membro_tem_cargo_nome(membro, cargo_para)

    if tem_para:
        pode_enviar = False
        bloco_situacao.append(f"- ⚠️ Você **já possui** o cargo `{cargo_para}`")
        pendencias.append(f"Cargo destino `{cargo_para}` já atribuído")
    elif tem_de:
        bloco_situacao.append(f"- ✅ Cargo atual: `{cargo_de}`")
    else:
        pode_enviar = False
        bloco_situacao.append(
            f"- ❌ Cargo atual exigido: `{cargo_de}` (você não possui)"
        )
        pendencias.append(f"Obter o cargo `{cargo_de}`")

    # ── Cursos ─────────────────────────────────────────────────────
    bloco_cursos: list[str] = ["## 📚 Cursos Obrigatórios"]
    cursos = list(trilha.get("cursos_obrigatorios") or [])
    faltando = listar_cursos_que_faltam(membro, cursos)

    if not cursos:
        bloco_cursos.append("- ✅ Nenhum curso obrigatório nesta trilha")
    elif not faltando:
        lista_ok = "\n".join(f"  {rotulo_curso(c)}" for c in cursos)
        bloco_cursos.append("- ✅ **Cursos concluídos:**")
        bloco_cursos.append(lista_ok)
    else:
        pode_enviar = False
        lista_falta = "\n".join(f"  {rotulo_curso(c)}" for c in faltando)
        bloco_cursos.append("- ❌ **Cursos pendentes:**")
        bloco_cursos.append(lista_falta)
        bloco_cursos.append(
            "> 💡 Acesse o painel **Solicitar Cursos** para adquiri-los."
        )
        bloco_cursos.append(
            "> Cada curso possui um custo próprio (moedas ou itens in-game)."
        )
        n = len(faltando)
        pendencias.append(
            f"Concluir os {n} curso{'s' if n != 1 else ''} listado{'s' if n != 1 else ''}"
        )

    # ── Plantão ────────────────────────────────────────────────────
    bloco_plantao: list[str] = ["## ⏱️ Horas de Plantão"]
    segundos_minimos = int(trilha.get("segundos_minimos_plantao") or 0)
    total_seg = int(segundos_plantao or 0)

    if segundos_minimos > 0:
        if total_seg >= segundos_minimos:
            bloco_plantao.append(
                f"- ✅ **Plantão completo:** `{formatar_hms(total_seg)}` "
                f"(mínimo `{formatar_hms(segundos_minimos)}`)"
            )
        else:
            pode_enviar = False
            falta = max(0, segundos_minimos - total_seg)
            bloco_plantao.append(
                f"- ❌ **Plantão incompleto:** `{formatar_hms(total_seg)}` de "
                f"`{formatar_hms(segundos_minimos)}` exigidas"
            )
            bloco_plantao.append(
                f"- ⚠️ Tempo restante: **{_formatar_falta_legivel(falta)}** "
                "em chamada com plantão ativo"
            )
            bloco_plantao.append(
                "> 🔔 Permaneça em call válida até atingir o tempo mínimo."
            )
            pendencias.append(
                f"Completar o tempo de plantão (faltam **{_formatar_falta_legivel(falta)}**)"
            )
    else:
        bloco_plantao.append(
            f"- ℹ️ Banco de horas registrado: `{formatar_hms(total_seg)}` "
            "(sem mínimo nesta trilha)"
        )

    observacao = (trilha.get("observacao") or "").strip()
    if observacao:
        bloco_plantao.append(f"> 📌 *{observacao}*")

    # ── Resumo ─────────────────────────────────────────────────────
    bloco_resumo: list[str] = ["## 🎯 Resumo"]
    if pode_enviar:
        bloco_resumo.append("- ✅ **Todos os pré-requisitos foram atendidos.**")
        bloco_resumo.append("- Você pode **enviar a solicitação** de promoção.")
    else:
        bloco_resumo.append("## 🎯 Resumo das Pendências")
        # remove header duplicate - rebuild
        bloco_resumo = ["## 🎯 Resumo das Pendências"]
        for indice, item in enumerate(pendencias, start=1):
            bloco_resumo.append(f"{indice}. {item}")
        if len(pendencias) > 1:
            bloco_resumo.append(
                f"{len(pendencias) + 0}. Todos os pré-requisitos devem ser "
                "atendidos para avançar"
            )

    linhas: list[str] = []
    for bloco in (bloco_situacao, bloco_cursos, bloco_plantao, bloco_resumo):
        if linhas:
            linhas.append("")  # espaço entre seções
        linhas.extend(bloco)

    return {
        "ok": pode_enviar,
        "pode_enviar": pode_enviar,
        "linhas": linhas,
        "cursos_faltando": faltando,
        "cargo_de": cargo_de,
        "cargo_para": cargo_para,
        "chave": trilha["chave"],
        "rotulo": trilha.get("rotulo") or trilha["chave"],
        "segundos_plantao": total_seg,
        "titulo_card": (
            "Requisitos completos" if pode_enviar else "Requisitos incompletos"
        ),
    }


async def montar_checklist_trilha_async(
    membro: discord.Member,
    trilha: dict,
) -> dict:
    """Checklist completo consultando o banco de horas do plantão."""
    segundos = await obter_segundos_plantao_totais(membro.id)
    return montar_checklist_trilha(
        membro,
        trilha,
        segundos_plantao=segundos,
    )


async def criar_solicitacao_promocao(
    *,
    discord_id: int,
    resumo_checklist: str,
    trilha: dict | None = None,
    chave_trilha: str | None = None,
    cargo_de: str | None = None,
    cargo_para: str | None = None,
) -> SolicitacaoPromocao:
    """Aceita trilha completa ou campos soltos (compatível com o painel)."""
    if trilha is not None:
        chave = trilha["chave"]
        de = trilha["de_cargo"]
        para = trilha["para_cargo"]
    else:
        chave = chave_trilha or ""
        de = cargo_de or ""
        para = cargo_para or ""

    async with async_session() as sessao:
        registro = SolicitacaoPromocao(
            discord_id=discord_id,
            chave_trilha=chave,
            cargo_de=de,
            cargo_para=para,
            status="PENDENTE",
            resumo_checklist=resumo_checklist[:4000],
            criado_em=agora(),
            atualizado_em=agora(),
        )
        sessao.add(registro)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def atualizar_mensagem_solicitacao(
    solicitacao_id: int,
    canal_id: int,
    mensagem_id: int,
) -> None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoPromocao).where(SolicitacaoPromocao.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return
        registro.mensagem_canal_id = canal_id
        registro.mensagem_id = mensagem_id
        registro.atualizado_em = agora()
        await sessao.commit()


async def obter_solicitacao(solicitacao_id: int) -> SolicitacaoPromocao | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoPromocao).where(SolicitacaoPromocao.id == solicitacao_id)
        )
        return resultado.scalar_one_or_none()


async def decidir_solicitacao(
    *,
    solicitacao_id: int,
    aprovada: bool,
    analisado_por: int,
    motivo: str | None = None,
) -> SolicitacaoPromocao | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoPromocao).where(SolicitacaoPromocao.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        if registro.status != "PENDENTE":
            return registro
        registro.status = "APROVADA" if aprovada else "REPROVADA"
        registro.analisado_por = analisado_por
        registro.motivo_reprovacao = (motivo or "")[:500] or None
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def registrar_historico(
    *,
    discord_id: int,
    tipo: str,
    cargo_de: str | None,
    cargo_para: str | None,
    motivo: str | None,
    executado_por: int | None,
    solicitacao_id: int | None = None,
) -> None:
    async with async_session() as sessao:
        sessao.add(
            HistoricoPromocao(
                discord_id=discord_id,
                tipo=tipo,
                cargo_de=cargo_de,
                cargo_para=cargo_para,
                motivo=(motivo or "")[:500] or None,
                executado_por=executado_por,
                solicitacao_id=solicitacao_id,
                criado_em=agora(),
            )
        )
        await sessao.commit()


async def aplicar_promocao_cargos(
    membro: discord.Member,
    cargo_de_nome: str,
    cargo_para_nome: str,
    *,
    executor: discord.abc.User | None = None,
) -> tuple[bool, str]:
    """
    Troca cargo de → para, atualiza prefixo do nick e registra log de cargos.
    """
    guilda = membro.guild
    cargo_para = resolver_cargo_na_guilda(guilda, cargo_para_nome)
    if cargo_para is None:
        return (
            False,
            f"Cargo destino `{cargo_para_nome}` não encontrado no config/guilda.",
        )

    cargo_de = resolver_cargo_na_guilda(guilda, cargo_de_nome)
    removidos: list[str] = []
    adicionados: list[str] = []

    try:
        # Remove o cargo de origem (obrigatório na promoção)
        cargos_para_remover: list[discord.Role] = []
        if cargo_de is not None and cargo_de in membro.roles:
            cargos_para_remover.append(cargo_de)
        else:
            # fallback: qualquer role do membro com o mesmo nome
            for cargo in membro.roles:
                if _nomes_cargo_equivalentes(cargo.name, cargo_de_nome):
                    cargos_para_remover.append(cargo)

        if cargos_para_remover:
            await membro.remove_roles(
                *cargos_para_remover,
                reason="Promoção aprovada — remove cargo anterior",
            )
            removidos.extend(c.name for c in cargos_para_remover)

        if cargo_para not in membro.roles:
            await membro.add_roles(
                cargo_para,
                reason="Promoção aprovada — novo cargo",
            )
            adicionados.append(cargo_para_nome)
    except discord.Forbidden:
        return False, (
            "Sem permissão para alterar cargos deste membro "
            "(hierarquia do bot abaixo do cargo?)."
        )
    except discord.HTTPException as erro:
        return False, f"Erro Discord ao alterar cargos: {erro}"

    # Prefixo do nickname conforme PREFIXOS_NICKNAME
    try:
        nick_atual = membro.nick or membro.display_name or membro.name
        novo_nick = aplicar_prefixo(nick_atual, cargo_para_nome)
        if novo_nick and novo_nick != membro.nick:
            await membro.edit(nick=novo_nick[:32], reason="Prefixo após promoção")
    except discord.Forbidden:
        logger.warning(
            "Promoção OK mas sem permissão para editar nick de %s", membro.id
        )
    except discord.HTTPException as erro:
        logger.warning("Falha ao editar nick na promoção de %s: %s", membro.id, erro)

    # Log de mudança de cargo
    try:
        await log_mudanca_cargo(
            guilda,
            candidato=membro,
            executor=executor or membro,
            cargos_adicionados=adicionados or None,
            cargos_removidos=removidos or None,
        )
    except Exception as erro:
        logger.warning("Falha ao logar mudança de cargo na promoção: %s", erro)

    return True, "Cargos e prefixo atualizados."
