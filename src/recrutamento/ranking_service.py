"""Serviço de ranking de recrutadores (semanal, mensal e tempo real).

- Semanal: sábado 12h → sábado 12h | postagem sábado 11h
- Mensal: 1º dia 00h → 1º do mês seguinte 00h | postagem dia 1 às 11h
- Conta apenas status APROVADO com data_fim no período
- UI: Components V2 (Container / LayoutView) — embeds proibidos
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import (
    datetime,
    timedelta,
)
from zoneinfo import ZoneInfo

import discord
from sqlalchemy import select

from src.config import (
    MESES_ABREV,
    RANKING_HORA_INICIO_CICLO,
    TIMEZONE_LOCAL,
    VALOR_POR_RECRUTAMENTO,
)
from src.database.connection import async_session
from src.database.models import (
    RankingHistorico,
    Recrutamento,
    agora,
)
from src.utils.formatacao import formatar_reais

# ── Tempo / períodos ─────────────────────────────────────────────────────


def _tz() -> ZoneInfo:
    return ZoneInfo(TIMEZONE_LOCAL)


def _para_utc(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=_tz())
    return dt_local.astimezone(ZoneInfo("UTC"))


def obter_periodo_ciclo_semanal(
    referencia: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Ciclo em andamento: sábado 12h → próximo sábado 12h (UTC)."""
    tz = _tz()
    agora_local = (referencia or datetime.now(tz)).astimezone(tz)

    dias_desde_sabado = (agora_local.weekday() - 5) % 7
    sabado_atual = (agora_local - timedelta(days=dias_desde_sabado)).replace(
        hour=RANKING_HORA_INICIO_CICLO,
        minute=0,
        second=0,
        microsecond=0,
    )

    if agora_local >= sabado_atual:
        inicio, fim = sabado_atual, sabado_atual + timedelta(days=7)
    else:
        inicio, fim = sabado_atual - timedelta(days=7), sabado_atual

    return _para_utc(inicio), _para_utc(fim)


def obter_periodo_postagem_semanal(
    referencia: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Semana que está terminando (sáb anterior 12h → sáb atual 12h)."""
    tz = _tz()
    agora_local = (referencia or datetime.now(tz)).astimezone(tz)

    dias_desde_sabado = (agora_local.weekday() - 5) % 7
    sabado_atual_12h = (agora_local - timedelta(days=dias_desde_sabado)).replace(
        hour=RANKING_HORA_INICIO_CICLO,
        minute=0,
        second=0,
        microsecond=0,
    )
    return _para_utc(sabado_atual_12h - timedelta(days=7)), _para_utc(sabado_atual_12h)


def obter_periodo_ciclo_mensal(
    referencia: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Mês calendário em andamento (dia 1 00h → dia 1 do próximo mês 00h)."""
    tz = _tz()
    agora_local = (referencia or datetime.now(tz)).astimezone(tz)
    inicio = agora_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if agora_local.month == 12:
        fim = inicio.replace(year=inicio.year + 1, month=1)
    else:
        fim = inicio.replace(month=inicio.month + 1)
    return _para_utc(inicio), _para_utc(fim)


def obter_periodo_postagem_mensal(
    referencia: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Mês anterior completo (para postagem no dia 1 às 11h)."""
    tz = _tz()
    agora_local = (referencia or datetime.now(tz)).astimezone(tz)
    primeiro_este_mes = agora_local.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    if primeiro_este_mes.month == 1:
        primeiro_mes_anterior = primeiro_este_mes.replace(
            year=primeiro_este_mes.year - 1, month=12
        )
    else:
        primeiro_mes_anterior = primeiro_este_mes.replace(
            month=primeiro_este_mes.month - 1
        )
    return _para_utc(primeiro_mes_anterior), _para_utc(primeiro_este_mes)


# ── Consulta ─────────────────────────────────────────────────────────────


async def buscar_contagem_por_recrutador(
    inicio_utc: datetime,
    fim_utc: datetime,
) -> dict[int, int]:
    """{discord_id_recrutador: qtd} de APROVADO com data_fim em [inicio, fim)."""
    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento.discord_id_recrutador, Recrutamento.id).where(
                Recrutamento.status == "APROVADO",
                Recrutamento.data_fim.is_not(None),
                Recrutamento.data_fim >= inicio_utc,
                Recrutamento.data_fim < fim_utc,
            )
        )
        rows = resultado.all()

    contagem: dict[int, int] = defaultdict(int)
    for discord_id_recrutador, _ in rows:
        contagem[int(discord_id_recrutador)] += 1
    return dict(contagem)


# ── Formatação de texto (corpo do Container) ─────────────────────────────


def _formatar_data_curta(dt: datetime) -> str:
    local = dt.astimezone(_tz())
    return f"{local.day:02d}/{local.month:02d}"


def _formatar_mes_ano(dt: datetime) -> str:
    local = dt.astimezone(_tz())
    return f"{MESES_ABREV[local.month]}/{local.year}"


def _medalha(posicao: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(posicao, "🏅")


def _plural_recrutamento(n: int) -> str:
    return "recrutamento" if n == 1 else "recrutamentos"


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


def _linhas_ranking(
    contagem: dict[int, int],
    titulo: str,
    subtitulo_periodo: str,
) -> tuple[str, str, int, int]:
    """Retorna (cabecalho, corpo, total_recrutamentos, total_pago)."""
    cabecalho = (
        f"# {titulo}\n"
        f"# 📅 **Período**\n"
        f"{subtitulo_periodo}\n"
        f"💸 Valor por recrutamento: {formatar_reais(VALOR_POR_RECRUTAMENTO)}"
    )

    if not contagem:
        corpo = (
            "_Nenhum recrutamento válido neste período._\n\n"
            "# 📌 **TOTAL GERAL**\n"
            "👥 **Recrutamentos válidos:** 0\n"
            f"💰 **Total pago:** {formatar_reais(0)}"
        )
        return cabecalho, corpo, 0, 0

    por_qtd: dict[int, list[int]] = defaultdict(list)
    for recrutador_id, qtd in contagem.items():
        por_qtd[qtd].append(recrutador_id)

    blocos: list[str] = []
    posicao = 1
    total_recrutamentos = 0

    for qtd in sorted(por_qtd.keys(), reverse=True):
        ids = sorted(por_qtd[qtd])
        medalha = _medalha(posicao)
        mencoes = _formatar_mencoes(ids)
        valor = qtd * VALOR_POR_RECRUTAMENTO
        blocos.append(
            f"{medalha} {mencoes}\n"
            f"↳ **{qtd} {_plural_recrutamento(qtd)}** • 💰 **{formatar_reais(valor)}**"
        )
        total_recrutamentos += qtd * len(ids)
        posicao += len(ids)

    total_pago = total_recrutamentos * VALOR_POR_RECRUTAMENTO
    corpo = (
        "\n\n".join(blocos)
        + "\n\n"
        + "# 📌 **TOTAL GERAL**\n"
        + f"👥 **Recrutamentos válidos:** {total_recrutamentos}\n"
        + f"💰 **Total pago:** {formatar_reais(total_pago)}"
    )
    return cabecalho, corpo, total_recrutamentos, total_pago


# ── Components V2 ────────────────────────────────────────────────────────


def montar_view_ranking(
    contagem: dict[int, int],
    inicio_utc: datetime,
    fim_utc: datetime,
    *,
    tipo: str,
    guild: discord.Guild | None = None,
    titulo_override: str | None = None,
    cor: discord.Color | None = None,
) -> tuple[discord.ui.LayoutView, int, int]:
    """Monta LayoutView com Container. Retorna (view, total_recrutamentos, total_pago)."""

    if tipo == "mensal":
        titulo = titulo_override or "🏆 **RANKING MENSAL DE RECRUTADORES**"
        sub = f"**Mês:** **{_formatar_mes_ano(inicio_utc)}** ({_formatar_data_curta(inicio_utc)} até {_formatar_data_curta(fim_utc)})"
        cor = cor or discord.Color.gold()
    elif tipo == "tempo_real":
        titulo = titulo_override or "🏆 **RANKING EM TEMPO REAL**"
        sub = f"**Ciclo atual:** **{_formatar_data_curta(inicio_utc)} até {_formatar_data_curta(fim_utc)}** _(parcial)_"
        cor = cor or discord.Color.blurple()
    else:  # semanal
        titulo = titulo_override or "🏆 **RANKING DE RECRUTADORES**"
        sub = f"**Início:** **{_formatar_data_curta(inicio_utc)} até {_formatar_data_curta(fim_utc)}**"
        cor = cor or discord.Color.red()

    cabecalho, corpo, total_rec, total_pago = _linhas_ranking(contagem, titulo, sub)

    agora_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = (
        f"-# {guild.name} • <t:{agora_ts}:f>"
        if guild
        else f"-# Ranking • <t:{agora_ts}:f>"
    )

    container = discord.ui.Container(
        discord.ui.TextDisplay(cabecalho),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(corpo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(rodape),
        accent_color=cor,
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view, total_rec, total_pago


def montar_view_historico_item(
    registro: RankingHistorico,
    guild: discord.Guild | None = None,
) -> discord.ui.LayoutView:
    contagem = {
        int(k): int(v) for k, v in json.loads(registro.payload_json or "{}").items()
    }
    titulo = (
        "📜 **HISTÓRICO — RANKING MENSAL**"
        if registro.tipo == "mensal"
        else "📜 **HISTÓRICO — RANKING SEMANAL**"
    )
    view, _, _ = montar_view_ranking(
        contagem,
        registro.periodo_inicio,
        registro.periodo_fim,
        tipo=registro.tipo,
        guild=guild,
        titulo_override=titulo,
        cor=discord.Color.dark_grey(),
    )
    return view


def montar_view_lista_historico(
    registros: list[RankingHistorico],
    guild: discord.Guild | None = None,
) -> discord.ui.LayoutView:
    """Lista resumida dos últimos rankings salvos."""
    if not registros:
        linhas = "_Nenhum ranking histórico encontrado._"
    else:
        blocos = []
        for r in registros:
            periodo = f"{_formatar_data_curta(r.periodo_inicio)} → {_formatar_data_curta(r.periodo_fim)}"
            tipo_emoji = "📅" if r.tipo == "semanal" else "🗓️"
            link = ""
            if r.channel_id and r.message_id:
                gid = guild.id if guild else 0
                link = f" • [abrir](https://discord.com/channels/{gid}/{r.channel_id}/{r.message_id})"
            blocos.append(
                f"{tipo_emoji} **{r.tipo.upper()}** `{periodo}`\n"
                f"↳ 👥 **{r.total_recrutamentos}** • 💰 **{formatar_reais(r.total_pago)}**{link}"
            )
        linhas = "\n\n".join(blocos)

    agora_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = (
        f"-# {guild.name} • <t:{agora_ts}:f>"
        if guild
        else f"-# Histórico • <t:{agora_ts}:f>"
    )

    container = discord.ui.Container(
        discord.ui.TextDisplay("# 📜 **HISTÓRICO DE RANKINGS**"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(linhas),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(rodape),
        accent_color=discord.Color.dark_grey(),
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


# ── Geração + histórico ──────────────────────────────────────────────────


async def gerar_view_ranking(
    tipo: str,
    *,
    guild: discord.Guild | None = None,
    referencia: datetime | None = None,
    modo_postagem: bool = False,
) -> tuple[discord.ui.LayoutView, dict[int, int], datetime, datetime, int, int]:
    """
    tipo: "semanal" | "mensal" | "tempo_real"
    modo_postagem=True → período fechado (auto-post / histórico oficial)
    modo_postagem=False → ciclo em andamento até agora (tempo real)
    """
    agora_utc = datetime.now(ZoneInfo("UTC"))
    titulo_override: str | None = None
    cor: discord.Color | None = None

    if tipo == "mensal":
        if modo_postagem:
            inicio, fim = obter_periodo_postagem_mensal(referencia)
            tipo_view = "mensal"
        else:
            inicio, _ = obter_periodo_ciclo_mensal(referencia)
            fim = agora_utc
            tipo_view = "tempo_real"
            titulo_override = "🏆 **RANKING MENSAL EM TEMPO REAL**"
            cor = discord.Color.gold()
    elif tipo == "tempo_real":
        inicio, _ = obter_periodo_ciclo_semanal(referencia)
        fim = agora_utc
        tipo_view = "tempo_real"
    else:  # semanal
        if modo_postagem:
            inicio, fim = obter_periodo_postagem_semanal(referencia)
            tipo_view = "semanal"
        else:
            inicio, _ = obter_periodo_ciclo_semanal(referencia)
            fim = agora_utc
            tipo_view = "tempo_real"

    contagem = await buscar_contagem_por_recrutador(inicio, fim)
    view, total_rec, total_pago = montar_view_ranking(
        contagem,
        inicio,
        fim,
        tipo=tipo_view,
        guild=guild,
        titulo_override=titulo_override,
        cor=cor,
    )
    return view, contagem, inicio, fim, total_rec, total_pago


async def salvar_historico(
    *,
    tipo: str,
    inicio: datetime,
    fim: datetime,
    contagem: dict[int, int],
    total_recrutamentos: int,
    total_pago: int,
    channel_id: int | None = None,
    message_id: int | None = None,
) -> RankingHistorico:
    registro = RankingHistorico(
        tipo=tipo,
        periodo_inicio=inicio,
        periodo_fim=fim,
        total_recrutamentos=total_recrutamentos,
        total_pago=total_pago,
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


async def listar_historico(
    tipo: str | None = None,
    limite: int = 10,
) -> list[RankingHistorico]:
    async with async_session() as session:
        stmt = (
            select(RankingHistorico)
            .order_by(RankingHistorico.criado_em.desc())
            .limit(limite)
        )
        if tipo in ("semanal", "mensal"):
            stmt = stmt.where(RankingHistorico.tipo == tipo)
        resultado = await session.execute(stmt)
        return list(resultado.scalars().all())


async def buscar_historico_por_id(historico_id: int) -> RankingHistorico | None:
    async with async_session() as session:
        resultado = await session.execute(
            select(RankingHistorico).where(RankingHistorico.id == historico_id)
        )
        return resultado.scalar_one_or_none()
