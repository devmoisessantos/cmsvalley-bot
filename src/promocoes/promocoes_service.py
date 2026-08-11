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
    return CARGOS.get(nome)


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


def montar_checklist_trilha(
    membro: discord.Member,
    trilha: dict,
    *,
    segundos_plantao: int | None = None,
) -> dict:
    """
    Avalia requisitos da trilha: adv, cargo, cursos e horas de plantão.

    segundos_plantao: total do banco log_plantao (passe via montar_checklist_trilha_async).
    """
    linhas: list[str] = []
    pode_enviar = True

    advs = membro_tem_advertencia_bloqueante(membro)
    if advs:
        pode_enviar = False
        linhas.append(
            f"❌ **Advertência ativa:** {', '.join(f'`{a}`' for a in advs)} — "
            "regularize antes de solicitar promoção."
        )
    else:
        linhas.append("✅ Sem Adv 01 / Adv 02 ativa")

    cargo_de = trilha["de_cargo"]
    cargo_para = trilha["para_cargo"]
    tem_de = membro_tem_cargo_nome(membro, cargo_de)
    tem_para = membro_tem_cargo_nome(membro, cargo_para)

    if tem_para:
        pode_enviar = False
        linhas.append(f"⚠️ Você **já possui** o cargo `{cargo_para}`.")
    elif tem_de:
        linhas.append(f"✅ Cargo atual exigido: `{cargo_de}`")
    else:
        pode_enviar = False
        linhas.append(
            f"❌ É necessário ter o cargo `{cargo_de}` para solicitar `{cargo_para}`."
        )

    cursos = list(trilha.get("cursos_obrigatorios") or [])
    faltando = listar_cursos_que_faltam(membro, cursos)
    if not cursos:
        linhas.append("✅ Nenhum curso obrigatório nesta trilha")
    elif not faltando:
        linhas.append(
            "✅ Cursos obrigatórios: " + ", ".join(rotulo_curso(c) for c in cursos)
        )
    else:
        pode_enviar = False
        linhas.append(
            "❌ **Cursos faltando:** " + ", ".join(rotulo_curso(c) for c in faltando)
        )
        linhas.append(
            "Use o painel de **Solicitar Cursos** para adquirir o que falta. "
            "Cada curso tem valor próprio (moedas ou in-game)."
        )

    # Banco de horas do plantão (interligado com src/plantao)
    segundos_minimos = int(trilha.get("segundos_minimos_plantao") or 0)
    if segundos_minimos > 0:
        total_seg = int(segundos_plantao or 0)
        if total_seg >= segundos_minimos:
            linhas.append(
                f"✅ **Horas de plantão:** {formatar_hms(total_seg)} "
                f"(mínimo {formatar_hms(segundos_minimos)}) — "
                "você já possui as horas necessárias."
            )
        else:
            pode_enviar = False
            falta = max(0, segundos_minimos - total_seg)
            linhas.append(
                f"❌ **Horas de plantão insuficientes:** {formatar_hms(total_seg)} "
                f"de {formatar_hms(segundos_minimos)} necessárias."
            )
            linhas.append(
                f"Faltam **{formatar_hms(falta)}** em call com o plantão ligado. "
                "Fique em call válida até completar o tempo."
            )
    elif segundos_plantao is not None:
        linhas.append(
            f"ℹ️ **Banco de horas (plantão):** {formatar_hms(int(segundos_plantao))} registradas."
        )

    if pode_enviar:
        linhas.append(
            "✅ **Todos os requisitos checados.** Você pode seguir com a solicitação."
        )

    observacao = trilha.get("observacao") or ""
    if observacao:
        linhas.append(f"-# {observacao}")

    return {
        "ok": pode_enviar,
        "pode_enviar": pode_enviar,
        "linhas": linhas,
        "cursos_faltando": faltando,
        "cargo_de": cargo_de,
        "cargo_para": cargo_para,
        "chave": trilha["chave"],
        "rotulo": trilha.get("rotulo") or trilha["chave"],
        "segundos_plantao": int(segundos_plantao or 0),
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
    id_de = id_cargo_por_nome(cargo_de_nome)
    id_para = id_cargo_por_nome(cargo_para_nome)
    if id_para is None:
        return False, f"Cargo destino `{cargo_para_nome}` não encontrado no config."
    cargo_para = guilda.get_role(id_para)
    if cargo_para is None:
        return False, f"Cargo destino id `{id_para}` não existe na guilda."

    removidos: list[str] = []
    adicionados: list[str] = []

    try:
        if id_de:
            cargo_de = guilda.get_role(id_de)
            if cargo_de is not None and cargo_de in membro.roles:
                await membro.remove_roles(cargo_de, reason="Promoção aprovada")
                removidos.append(cargo_de_nome)
        if cargo_para not in membro.roles:
            await membro.add_roles(cargo_para, reason="Promoção aprovada")
            adicionados.append(cargo_para_nome)
    except discord.Forbidden:
        return False, "Sem permissão para alterar cargos deste membro."
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
