"""Rankings de plantão:

- Chamadas: quantas chamadas cada doutor realizou (tabela chamadas)
- Horas: tempo em call por membro (soma duracao_segundos em log_plantao) — só relatório

Mesmos ciclos do ranking de recrutadores (semanal sáb 12h / mensal calendário).
UI: Components V2.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from sqlalchemy import func, select

from src.config import MESES_ABREV, TIMEZONE_LOCAL
from src.database.connection import async_session
from src.database.models import Chamada, LogPlantao, RankingHistorico, agora
from src.recrutamento.ranking_service import (
    obter_periodo_ciclo_mensal,
    obter_periodo_ciclo_semanal,
    obter_periodo_postagem_mensal,
    obter_periodo_postagem_semanal,
)
from src.utils.formatacao import formatar_hms


def _tz() -> ZoneInfo:
    return ZoneInfo(TIMEZONE_LOCAL)


def _formatar_data_curta(dt: datetime) -> str:
    local = dt.astimezone(_tz())
    return f"{local.day:02d}/{local.month:02d}"


def _formatar_mes_ano(dt: datetime) -> str:
    local = dt.astimezone(_tz())
    return f"{MESES_ABREV[local.month]}/{local.year}"


def _medalha(posicao: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(posicao, "🏅")


def _formatar_mencoes(ids: list[int]) -> str:
    tags = [f"<@{uid}>" for uid in ids]
    if len(tags) == 1:
        return tags[0]
    if len(tags) == 2:
        return f"{tags[0]} e {tags[1]}"
    if len(tags) <= 4:
        return ", ".join(tags[:-1]) + f" e {tags[-1]}"
    meio = (len(tags) + 1) // 2
    return ", ".join(tags[:meio]) + ",\n" + ", ".join(tags[meio:-1]) + f" e {tags[-1]}"


# ── Consultas ────────────────────────────────────────────────────────────


async def buscar_chamadas_por_doutor(
    inicio_utc: datetime,
    fim_utc: datetime,
) -> dict[int, int]:
    """{doutor_id: qtd_chamadas} no período [inicio, fim)."""
    async with async_session() as session:
        resultado = await session.execute(
            select(Chamada.doutor_id, func.count())
            .where(
                Chamada.criada_em >= inicio_utc,
                Chamada.criada_em < fim_utc,
            )
            .group_by(Chamada.doutor_id)
        )
        return {int(did): int(qtd) for did, qtd in resultado.all()}


async def buscar_horas_por_membro(
    inicio_utc: datetime,
    fim_utc: datetime,
) -> dict[int, int]:
    """{discord_id: segundos_totais} a partir de log_plantao.duracao_segundos."""
    async with async_session() as session:
        resultado = await session.execute(
            select(
                LogPlantao.discord_id,
                func.coalesce(func.sum(LogPlantao.duracao_segundos), 0),
            )
            .where(
                LogPlantao.criado_em >= inicio_utc,
                LogPlantao.criado_em < fim_utc,
                LogPlantao.duracao_segundos.is_not(None),
                LogPlantao.duracao_segundos > 0,
            )
            .group_by(LogPlantao.discord_id)
        )
        return {int(did): int(segs) for did, segs in resultado.all() if int(segs) > 0}


# ── Montagem de ranking genérico (valor numérico decrescente) ─────────────


def _montar_corpo_por_valor(
    contagem: dict[int, int],
    *,
    formatar_valor,
    label_singular: str,
    label_plural: str,
    vazio_msg: str,
    total_label: str,
) -> tuple[str, int]:
    """Retorna (corpo_texto, total_agregado). total = soma dos valores * pessoas?
    Para contagens: soma de qtd*pessoas; para segundos: soma dos segundos."""
    if not contagem:
        return (
            f"{vazio_msg}\n\n# 📌 **TOTAL GERAL**\n{total_label}: **0**",
            0,
        )

    por_valor: dict[int, list[int]] = defaultdict(list)
    for uid, val in contagem.items():
        por_valor[val].append(uid)

    blocos: list[str] = []
    posicao = 1
    total = 0

    for val in sorted(por_valor.keys(), reverse=True):
        ids = sorted(por_valor[val])
        medalha = _medalha(posicao)
        mencoes = _formatar_mencoes(ids)
        label = label_singular if val == 1 else label_plural
        # horas: val é segundos; chamadas: val é quantidade
        blocos.append(f"{medalha} {mencoes}\n↳ **{formatar_valor(val)}** {label}")
        total += val * len(ids)
        posicao += len(ids)

    corpo = (
        "\n\n".join(blocos)
        + "\n\n"
        + "# 📌 **TOTAL GERAL**\n"
        + f"{total_label}: **{formatar_valor(total)}**"
    )
    return corpo, total


def montar_view_ranking_chamadas(
    contagem: dict[int, int],
    inicio_utc: datetime,
    fim_utc: datetime,
    *,
    periodo: str,  # semanal | mensal | tempo_real
    guild: discord.Guild | None = None,
    titulo_override: str | None = None,
) -> tuple[discord.ui.LayoutView, int]:
    if periodo == "mensal":
        titulo = titulo_override or "🩺 **RANKING MENSAL DE CHAMADAS**"
        sub = (
            f"**Mês:** **{_formatar_mes_ano(inicio_utc)}** "
            f"({_formatar_data_curta(inicio_utc)} até {_formatar_data_curta(fim_utc)})"
        )
        cor = discord.Color.blue()
    elif periodo == "tempo_real":
        titulo = titulo_override or "🩺 **RANKING DE CHAMADAS — TEMPO REAL**"
        sub = (
            f"**Ciclo atual:** **{_formatar_data_curta(inicio_utc)} até "
            f"{_formatar_data_curta(fim_utc)}** _(parcial)_"
        )
        cor = discord.Color.blurple()
    else:
        titulo = titulo_override or "🩺 **RANKING SEMANAL DE CHAMADAS**"
        sub = f"**Início:** **{_formatar_data_curta(inicio_utc)} até {_formatar_data_curta(fim_utc)}**"
        cor = discord.Color.dark_blue()

    cabecalho = (
        f"# {titulo}\n"
        f"# 📅 **Período**\n"
        f"{sub}\n"
        f"📋 Contagem de chamadas realizadas (tabela `chamadas`)"
    )
    corpo, total = _montar_corpo_por_valor(
        contagem,
        formatar_valor=lambda n: str(n),
        label_singular="chamada",
        label_plural="chamadas",
        vazio_msg="_Nenhuma chamada neste período._",
        total_label="🩺 **Chamadas realizadas**",
    )

    agora_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = (
        f"-# {guild.name} • <t:{agora_ts}:f>"
        if guild
        else f"-# Ranking • <t:{agora_ts}:f>"
    )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(cabecalho),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(corpo),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(rodape),
            accent_color=cor,
        )
    )
    return view, total


def montar_view_ranking_horas(
    contagem_segundos: dict[int, int],
    inicio_utc: datetime,
    fim_utc: datetime,
    *,
    periodo: str,
    guild: discord.Guild | None = None,
    titulo_override: str | None = None,
) -> tuple[discord.ui.LayoutView, int]:
    if periodo == "mensal":
        titulo = titulo_override or "⏱️ **RANKING MENSAL DE HORAS — PLANTÃO**"
        sub = (
            f"**Mês:** **{_formatar_mes_ano(inicio_utc)}** "
            f"({_formatar_data_curta(inicio_utc)} até {_formatar_data_curta(fim_utc)})"
        )
        cor = discord.Color.green()
    elif periodo == "tempo_real":
        titulo = titulo_override or "⏱️ **RANKING DE HORAS — TEMPO REAL**"
        sub = (
            f"**Ciclo atual:** **{_formatar_data_curta(inicio_utc)} até "
            f"{_formatar_data_curta(fim_utc)}** _(parcial)_"
        )
        cor = discord.Color.dark_green()
    else:
        titulo = titulo_override or "⏱️ **RANKING SEMANAL DE HORAS — PLANTÃO**"
        sub = f"**Início:** **{_formatar_data_curta(inicio_utc)} até {_formatar_data_curta(fim_utc)}**"
        cor = discord.Color.brand_green()

    cabecalho = (
        f"# {titulo}\n"
        f"# 📅 **Período**\n"
        f"{sub}\n"
        f"📊 Relatório de tempo em call · **sem pagamento / premiação**"
    )
    corpo, total = _montar_corpo_por_valor(
        contagem_segundos,
        formatar_valor=formatar_hms,
        label_singular="",
        label_plural="",
        vazio_msg="_Nenhum tempo de plantão registrado neste período._",
        total_label="⏱️ **Tempo total da equipe**",
    )
    # remove espaços duplos deixados pelo label vazio
    corpo = corpo.replace("  ", " ").replace(" \n", "\n")

    agora_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = (
        f"-# Relatório · {guild.name} • <t:{agora_ts}:f>"
        if guild
        else f"-# Relatório • <t:{agora_ts}:f>"
    )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(cabecalho),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(corpo),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(rodape),
            accent_color=cor,
        )
    )
    return view, total


# ── Geração por tipo ─────────────────────────────────────────────────────


def _periodos(
    categoria: str,  # chamada | horas
    periodo: str,  # semanal | mensal | tempo_real
    referencia: datetime | None,
    modo_postagem: bool,
) -> tuple[datetime, datetime, str]:
    """Retorna inicio, fim, periodo_view."""
    agora_utc = datetime.now(ZoneInfo("UTC"))
    if periodo == "mensal":
        if modo_postagem:
            return (*obter_periodo_postagem_mensal(referencia), "mensal")
        inicio, _ = obter_periodo_ciclo_mensal(referencia)
        return inicio, agora_utc, "tempo_real"
    if periodo == "tempo_real":
        inicio, _ = obter_periodo_ciclo_semanal(referencia)
        return inicio, agora_utc, "tempo_real"
    # semanal
    if modo_postagem:
        return (*obter_periodo_postagem_semanal(referencia), "semanal")
    inicio, _ = obter_periodo_ciclo_semanal(referencia)
    return inicio, agora_utc, "tempo_real"


async def gerar_view_ranking_chamadas(
    periodo: str,
    *,
    guild: discord.Guild | None = None,
    referencia: datetime | None = None,
    modo_postagem: bool = False,
) -> tuple[discord.ui.LayoutView, dict[int, int], datetime, datetime, int]:
    inicio, fim, periodo_view = _periodos("chamada", periodo, referencia, modo_postagem)
    contagem = await buscar_chamadas_por_doutor(inicio, fim)
    view, total = montar_view_ranking_chamadas(
        contagem, inicio, fim, periodo=periodo_view, guild=guild
    )
    return view, contagem, inicio, fim, total


async def gerar_view_ranking_horas(
    periodo: str,
    *,
    guild: discord.Guild | None = None,
    referencia: datetime | None = None,
    modo_postagem: bool = False,
) -> tuple[discord.ui.LayoutView, dict[int, int], datetime, datetime, int]:
    inicio, fim, periodo_view = _periodos("horas", periodo, referencia, modo_postagem)
    contagem = await buscar_horas_por_membro(inicio, fim)
    view, total = montar_view_ranking_horas(
        contagem, inicio, fim, periodo=periodo_view, guild=guild
    )
    return view, contagem, inicio, fim, total


async def salvar_historico_plantao(
    *,
    tipo: str,
    inicio: datetime,
    fim: datetime,
    contagem: dict[int, int],
    total: int,
    channel_id: int | None = None,
    message_id: int | None = None,
) -> RankingHistorico:
    registro = RankingHistorico(
        tipo=tipo,
        periodo_inicio=inicio,
        periodo_fim=fim,
        total_recrutamentos=total,  # reutilizado: total chamadas ou total segundos
        total_pago=0,  # horas não têm pagamento; chamadas também 0 aqui
        payload_json=json.dumps({str(k): v for k, v in contagem.items()}),
        channel_id=channel_id,
        message_id=message_id,
        criado_em=agora(),
    )
    async with async_session() as session:
        session.add(registro)
        await session.commit()
        await session.refresh(registro)
    return registro


async def listar_historico_plantao(
    prefixo: str,
    limite: int = 10,
) -> list[RankingHistorico]:
    """prefixo: 'chamada' | 'horas'"""
    async with async_session() as session:
        resultado = await session.execute(
            select(RankingHistorico)
            .where(RankingHistorico.tipo.startswith(prefixo))
            .order_by(RankingHistorico.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())
