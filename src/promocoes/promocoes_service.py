"""Validação de trilhas, advertências e registro de promoção."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.config import (
    CARGO_DOUTOR,
    CARGO_INSTRUTOR,
    CARGO_PARAMEDICO,
    CARGO_PSICOLOGO,
    CARGO_RECRUTADOR,
    CARGOS,
    CARGOS_PUNICOES,
    META_PROMOCAO_MARGEM,
    TRILHAS_PROMOCAO,
)
from src.cursos.cursos_service import (
    listar_cursos_que_faltam,
    menção_cargo_curso,
)
from src.database.conexao import async_session
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
    """
    Encontra o id de um cargo a partir do nome escrito.

    Devolve None quando o nome nao existe em CARGOS. Antes de desistir, tenta de
    novo ignorando espacos e maiusculas, porque os nomes dos cargos deste servidor
    tem emoji e espacos especiais, e uma diferenca invisivel de digitacao fazia a
    promocao falhar sem explicacao.
    """
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


def _nomes_cargo_equivalentes(primeiro_nome: str, segundo_nome: str) -> bool:
    return (
        "".join(str(primeiro_nome).split()).lower()
        == "".join(str(segundo_nome).split()).lower()
    )


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
    """
    Diz se o membro tem o cargo com aquele nome.

    Devolve False tambem quando o nome do cargo nao existe na configuracao. Assim,
    um nome errado nunca vira "sim, tem o cargo" por acidente.
    """
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


CARGOS_DE_AREA = (
    CARGO_DOUTOR,
    CARGO_PSICOLOGO,
    CARGO_RECRUTADOR,
    CARGO_INSTRUTOR,
)


def membro_e_paramedico(membro: discord.Member) -> bool:
    """True se o membro tem o cargo de Paramédico."""
    return membro_tem_cargo_nome(membro, CARGO_PARAMEDICO)


def membro_ja_tem_area(membro: discord.Member) -> bool:
    """True se o membro já possui algum cargo de área médica."""
    for nome_cargo in CARGOS_DE_AREA:
        if membro_tem_cargo_nome(membro, nome_cargo):
            return True
    return False


def montar_checklist_trilha(
    membro: discord.Member,
    trilha: dict,
    *,
    segundos_plantao: int | None = None,
    contagens_extras: dict | None = None,
    exigir_cargo_origem: bool = True,
    exigir_plantao: bool = True,
    exigir_metas: bool = True,
    modo: str = "trilha",
) -> dict:
    """
    Avalia requisitos e monta o corpo do CardView em seções.

    Modos:
    - ``trilha`` (padrão): cargo de origem + cursos + plantão + metas.
    - ``primeira_area_paramedico``: cursos + plantão da área escolhida,
      sem metas de produção. Usado quando o Paramédico ainda não tem área.
    """
    pode_enviar = True
    pendencias: list[str] = []
    modo_primeira_area = modo == "primeira_area_paramedico"

    # ── Situação atual ─────────────────────────────────────────────
    bloco_situacao: list[str] = ["## 📌 Situação Atual"]
    if modo_primeira_area:
        bloco_situacao.append(
            "- ℹ️ **Modo Paramédico — primeira área:** cursos práticos + "
            "curso da área + horas de plantão da área (sem metas de "
            "laudos/chamadas/recrutamentos)"
        )

    advertencias = membro_tem_advertencia_bloqueante(membro)
    if advertencias:
        pode_enviar = False
        bloco_situacao.append(
            f"- ❌ **Advertência ativa:** "
            f"{', '.join(f'`{advertencia}`' for advertencia in advertencias)}"
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
    elif exigir_cargo_origem:
        if tem_de:
            bloco_situacao.append(f"- ✅ Cargo atual: `{cargo_de}`")
        else:
            pode_enviar = False
            bloco_situacao.append(
                f"- ❌ Cargo atual exigido: `{cargo_de}` (você não possui)"
            )
            pendencias.append(f"Obter o cargo `{cargo_de}`")
    else:
        # Primeira área: não exige o cargo intermediário da trilha
        if membro_e_paramedico(membro):
            bloco_situacao.append(
                f"- ✅ Paramédico pedindo área `{cargo_para}` "
                f"(origem da trilha `{cargo_de}` dispensada neste modo)"
            )
        else:
            bloco_situacao.append(
                f"- ℹ️ Cargo de origem da trilha (`{cargo_de}`) não exigido neste modo"
            )

    # ── Cursos ─────────────────────────────────────────────────────
    bloco_cursos: list[str] = ["## 📚 Cursos Obrigatórios"]
    cursos = list(trilha.get("cursos_obrigatorios") or [])
    faltando = listar_cursos_que_faltam(membro, cursos)

    if not cursos:
        bloco_cursos.append("- ✅ Nenhum curso obrigatório nesta trilha")
    elif not faltando:
        # Uma menção por linha (padrão cursos: "> <@&id>")
        lista_concluidos = "\n".join(
            f"> {menção_cargo_curso(chave_curso)}" for chave_curso in cursos
        )
        bloco_cursos.append(f"- ✅ **Cursos concluídos:**\n{lista_concluidos}")
    else:
        pode_enviar = False
        # Uma menção por linha — o prefixo "> " evita o Discord colar tudo
        lista_pendentes = "\n".join(
            f"> {menção_cargo_curso(chave_curso)}" for chave_curso in faltando
        )
        bloco_cursos.append(f"- ❌ **Cursos pendentes:**\n{lista_pendentes}")
        bloco_cursos.append(
            "> 💡 Acesse o painel **Solicitar Cursos** para adquiri-los."
        )
        bloco_cursos.append(
            "> Cada curso possui um custo próprio (moedas ou dinheiro in-game)."
        )
        quantidade_faltando = len(faltando)
        pendencias.append(
            f"Concluir os {quantidade_faltando} "
            f"curso{'s' if quantidade_faltando != 1 else ''} "
            f"listado{'s' if quantidade_faltando != 1 else ''}"
        )

    # ── Plantão ────────────────────────────────────────────────────
    bloco_plantao: list[str] = ["## ⏱️ Horas de Plantão"]
    segundos_minimos = int(trilha.get("segundos_minimos_plantao") or 0)
    total_seg = int(segundos_plantao or 0)
    margem = float(META_PROMOCAO_MARGEM or 1.0)
    if margem <= 0 or margem > 1:
        margem = 1.0
    minimo_aceitavel = int(segundos_minimos * margem)

    if not exigir_plantao:
        bloco_plantao.append(
            f"- ℹ️ Plantão **não exigido** neste modo "
            f"(registrado: `{formatar_hms(total_seg)}`)"
        )
    elif segundos_minimos > 0:
        if total_seg >= segundos_minimos:
            bloco_plantao.append(
                f"- ✅ **Plantão completo:** `{formatar_hms(total_seg)}` "
                f"(mínimo `{formatar_hms(segundos_minimos)}`)"
            )
        elif total_seg >= minimo_aceitavel:
            bloco_plantao.append(
                f"- ✅ **Plantão próximo da meta:** `{formatar_hms(total_seg)}` "
                f"(meta `{formatar_hms(segundos_minimos)}`, "
                f"aceito a partir de `{formatar_hms(minimo_aceitavel)}`)"
            )
        else:
            pode_enviar = False
            falta = max(0, minimo_aceitavel - total_seg)
            bloco_plantao.append(
                f"- ❌ **Plantão incompleto:** `{formatar_hms(total_seg)}` de "
                f"`{formatar_hms(segundos_minimos)}` exigidas"
            )
            bloco_plantao.append(
                f"- ⚠️ Faltam **{_formatar_falta_legivel(falta)}** "
                f"para atingir a margem de {int(margem * 100)}%"
            )
            pendencias.append(
                f"Completar o tempo de plantão (faltam "
                f"**{_formatar_falta_legivel(falta)}**)"
            )
    else:
        bloco_plantao.append(
            f"- ℹ️ Banco de horas registrado: `{formatar_hms(total_seg)}` "
            "(sem mínimo nesta trilha)"
        )

    # ── Outras metas (config por trilha) ────────────────────────────
    bloco_metas: list[str] = ["## 🎯 Metas da Trilha"]
    contagens = contagens_extras or {}
    teve_meta_extra = False
    if not exigir_metas:
        bloco_metas.append("- ℹ️ Metas **dispensadas** neste modo (só cursos + conduta)")
    else:
        for chave_meta, rotulo_meta in (
            ("meta_laudos", "Laudos"),
            ("meta_recrutamentos", "Recrutamentos"),
            ("meta_chamadas", "Chamadas"),
            ("meta_cursos_aplicados", "Cursos aplicados"),
        ):
            exigido = int(trilha.get(chave_meta) or 0)
            if exigido <= 0:
                continue
            teve_meta_extra = True
            atual = int(contagens.get(chave_meta, 0) or 0)
            minimo_meta = int(exigido * margem)
            if atual >= exigido:
                bloco_metas.append(
                    f"- ✅ **{rotulo_meta}:** `{atual}` (meta `{exigido}`)"
                )
            elif atual >= minimo_meta:
                bloco_metas.append(
                    f"- ✅ **{rotulo_meta} (próximo):** `{atual}` "
                    f"(meta `{exigido}`, aceito `{minimo_meta}`+)"
                )
            else:
                pode_enviar = False
                bloco_metas.append(f"- ❌ **{rotulo_meta}:** `{atual}` de `{exigido}`")
                pendencias.append(
                    f"Atingir meta de {rotulo_meta.lower()} ({atual}/{exigido})"
                )
        if not teve_meta_extra:
            bloco_metas.append("- ℹ️ Nenhuma meta extra configurada nesta trilha")

    observacao = (trilha.get("observacao") or "").strip()
    if observacao:
        bloco_plantao.append(f"> 📌 *{observacao}*")

    if trilha.get("exige_avaliacao_hp"):
        bloco_metas.append(
            "- ℹ️ **Avaliação do Responsável HP obrigatória** nesta trilha "
            "(a diretoria confere com `/avaliacao-membro`)."
        )

    # ── Resumo ─────────────────────────────────────────────────────
    bloco_resumo: list[str] = ["## 🎯 Resumo"]
    if pode_enviar:
        bloco_resumo.append("- ✅ **Todos os pré-requisitos foram atendidos.**")
    else:
        bloco_resumo = ["## 🎯 Resumo das Pendências"]
        for indice, item in enumerate(pendencias, start=1):
            bloco_resumo.append(f"{indice}. {item}")
        if len(pendencias) > 1:
            bloco_resumo.append(
                f"{len(pendencias) + 0}. Todos os pré-requisitos devem ser "
                "atendidos para avançar"
            )

    linhas: list[str] = []
    for bloco in (
        bloco_situacao,
        bloco_cursos,
        bloco_plantao,
        bloco_metas,
        bloco_resumo,
    ):
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
        "modo": modo,
        "titulo_card": (
            "📋 Requisitos completos" if pode_enviar else "📋 Requisitos incompletos"
        ),
    }


async def _contar_metas_do_membro(discord_id: int) -> dict[str, int]:
    """
    Conta laudos, recrutamentos, chamadas (como doutor) e cursos aplicados.

    Usa os models reais do projeto:
    - Laudo.discord_id_psicologo
    - Recrutamento (recrutador + APROVADO)
    - Chamada.doutor_id
    - SolicitacaoCurso.instrutor_id
    """
    from sqlalchemy import func

    from src.database.models import (
        Chamada,
        Laudo,
        Recrutamento,
        SolicitacaoCurso,
    )

    contagens = {
        "meta_laudos": 0,
        "meta_recrutamentos": 0,
        "meta_chamadas": 0,
        "meta_cursos_aplicados": 0,
    }

    async with async_session() as sessao:
        # Laudos emitidos como psicólogo
        resultado = await sessao.execute(
            select(func.count())
            .select_from(Laudo)
            .where(Laudo.discord_id_psicologo == discord_id)
        )
        contagens["meta_laudos"] = int(resultado.scalar_one() or 0)

        # Recrutamentos em que a pessoa foi o recrutador e aprovou
        resultado = await sessao.execute(
            select(func.count())
            .select_from(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
            )
        )
        contagens["meta_recrutamentos"] = int(resultado.scalar_one() or 0)

        # Chamadas em que o membro foi o doutor responsável
        resultado = await sessao.execute(
            select(func.count())
            .select_from(Chamada)
            .where(Chamada.doutor_id == discord_id)
        )
        contagens["meta_chamadas"] = int(resultado.scalar_one() or 0)

        # Cursos em que atuou como instrutor e concluiu
        resultado = await sessao.execute(
            select(func.count())
            .select_from(SolicitacaoCurso)
            .where(
                SolicitacaoCurso.instrutor_id == discord_id,
                SolicitacaoCurso.status.in_(["CONCLUIDO", "APROVADO", "FINALIZADO"]),
            )
        )
        contagens["meta_cursos_aplicados"] = int(resultado.scalar_one() or 0)

    return contagens


async def montar_checklist_trilha_async(
    membro: discord.Member,
    trilha: dict,
    *,
    modo: str = "trilha",
) -> dict:
    """
    Checklist completo.

    - modo ``trilha``: cargo origem + cursos + plantão + metas.
    - modo ``primeira_area_paramedico``: cursos + plantão da área, sem
      metas de produção (Paramédico ainda sem área).
    """
    segundos = await obter_segundos_plantao_totais(membro.id)
    contagens = await _contar_metas_do_membro(membro.id)
    if modo == "primeira_area_paramedico":
        return montar_checklist_trilha(
            membro,
            trilha,
            segundos_plantao=segundos,
            contagens_extras=contagens,
            exigir_cargo_origem=False,
            exigir_plantao=True,
            exigir_metas=False,
            modo=modo,
        )
    return montar_checklist_trilha(
        membro,
        trilha,
        segundos_plantao=segundos,
        contagens_extras=contagens,
        exigir_cargo_origem=True,
        exigir_plantao=True,
        exigir_metas=True,
        modo="trilha",
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
    """
    Guarda no banco onde ficou a mensagem do pedido de promocao.

    Grava o canal e a mensagem na solicitacao, para o bot conseguir voltar e editar
    esse card depois que a diretoria decidir. Sem esse endereco, a decisao seria
    gravada mas o card no canal continuaria mostrando "pendente".

    Se a solicitacao nao existir mais, sai sem gravar nada.
    """
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
    """
    Busca uma solicitacao de promocao pelo seu numero.

    Devolve None quando nao existe, em vez de levantar erro. Quem chama decide o que
    dizer ao membro nesse caso.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoPromocao).where(SolicitacaoPromocao.id == solicitacao_id)
        )
        return resultado.scalar_one_or_none()


async def obter_solicitacao_pendente(
    discord_id: int,
) -> SolicitacaoPromocao | None:
    """
    Retorna a solicitação ainda PENDENTE do membro, se existir.
    Usado para bloquear pedido duplicado enquanto a diretoria não decide.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoPromocao)
            .where(
                SolicitacaoPromocao.discord_id == int(discord_id),
                SolicitacaoPromocao.status == "PENDENTE",
            )
            .order_by(SolicitacaoPromocao.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def decidir_solicitacao(
    *,
    solicitacao_id: int,
    aprovada: bool,
    analisado_por: int,
    motivo: str | None = None,
) -> tuple[SolicitacaoPromocao | None, bool]:
    """
    Marca a solicitação como APROVADA ou REPROVADA.

    Retorna (registro, foi_decidido_agora).
    - registro None → pedido não existe
    - foi_decidido_agora False → já tinha sido decidido (evita card duplicado)
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoPromocao).where(SolicitacaoPromocao.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None, False
        if registro.status != "PENDENTE":
            return registro, False
        registro.status = "APROVADA" if aprovada else "REPROVADA"
        registro.analisado_por = analisado_por
        registro.motivo_reprovacao = (motivo or "")[:500] or None
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro, True


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
    """
    Grava no banco uma linha do historico de promocoes do membro.

    Guarda de qual cargo para qual cargo, o motivo, quem executou e o numero da
    solicitacao quando houver. Esse historico e o que permite conferir depois por
    que alguem subiu de cargo — e por isso ele so cresce, nunca e apagado nem
    reescrito.
    """
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
    Adiciona o cargo de destino da promoção (não remove o cargo anterior).
    Atualiza o prefixo do nick e registra log de cargos.

    O parâmetro cargo_de_nome fica só para contexto/histórico — a regra
    de toda a trilha é manter os cargos anteriores e só somar o novo.
    """
    guilda = membro.guild
    cargo_para = resolver_cargo_na_guilda(guilda, cargo_para_nome)
    if cargo_para is None:
        return (
            False,
            f"Cargo destino `{cargo_para_nome}` não encontrado no config/guilda.",
        )

    adicionados: list[str] = []

    try:
        if cargo_para not in membro.roles:
            await membro.add_roles(
                cargo_para,
                reason="Promoção aprovada — adicionado novo cargo",
            )
            adicionados.append(cargo_para_nome)
    except discord.Forbidden:
        return False, (
            "Sem permissão para alterar cargos deste membro "
            "(hierarquia do bot abaixo do cargo?)."
        )
    except discord.HTTPException as erro:
        return False, f"Erro Discord ao alterar cargos: {erro}"

    # Prefixo do nickname conforme PREFIXOS_NICKNAME (cargo novo)
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

    # Log de mudança de cargo (somente adições)
    try:
        await log_mudanca_cargo(
            guilda,
            candidato=membro,
            executor=executor or membro,
            cargos_adicionados=adicionados or None,
            cargos_removidos=None,
        )
    except Exception as erro:
        logger.warning("Falha ao logar mudança de cargo na promoção: %s", erro)

    if adicionados:
        return (
            True,
            f"Cargo `{cargo_para_nome}` adicionado (origem `{cargo_de_nome}` mantida).",
        )
    return True, f"Membro já possuía `{cargo_para_nome}`; nenhum cargo removido."
