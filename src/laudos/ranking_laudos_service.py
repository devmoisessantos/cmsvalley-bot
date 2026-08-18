"""Ranking de laudos psicológicos emitidos.

Conta registros em `laudos` por psicólogo no ciclo semanal/mensal
(mesmos períodos do ranking de recrutadores / plantão).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from sqlalchemy import (
    func,
    select,
)

from src.config import (
    TIMEZONE_LOCAL,
)
from src.database.conexao import async_session
from src.database.models import (
    Laudo,
    RankingHistorico,
    agora,
)
from src.recrutamento.ranking_service import (
    obter_periodo_ciclo_mensal,
    obter_periodo_ciclo_semanal,
    obter_periodo_postagem_mensal,
    obter_periodo_postagem_semanal,
)
from src.utils.formatacao import (
    formatar_data_curta,
    formatar_mes_e_ano,
)


def _fuso() -> ZoneInfo:
    return ZoneInfo(TIMEZONE_LOCAL)


def _formatar_data_curta(momento: datetime) -> str:
    """Data curta DD/MM. Delega para o formatador comum do projeto."""
    return formatar_data_curta(momento)


def _formatar_mes_ano(momento: datetime) -> str:
    """Mes abreviado com ano. Delega para o formatador comum do projeto."""
    return formatar_mes_e_ano(momento)


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


async def contar_laudos_por_psicologo(
    inicio_utc: datetime,
    fim_utc: datetime,
) -> dict[int, int]:
    """{discord_id_psicologo: quantidade_de_laudos} no período [inicio, fim)."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Laudo.discord_id_psicologo, func.count())
            .where(
                Laudo.criado_em >= inicio_utc,
                Laudo.criado_em < fim_utc,
            )
            .group_by(Laudo.discord_id_psicologo)
        )
        return {
            int(discord_id): int(quantidade)
            for discord_id, quantidade in resultado.all()
        }


def _periodos(
    periodo: str,
    referencia: datetime | None,
    modo_postagem: bool,
) -> tuple[datetime, datetime, str]:
    agora_utc = datetime.now(ZoneInfo("UTC"))
    if periodo == "mensal":
        if modo_postagem:
            return (*obter_periodo_postagem_mensal(referencia), "mensal")
        inicio, _ = obter_periodo_ciclo_mensal(referencia)
        return inicio, agora_utc, "tempo_real"
    if periodo == "tempo_real":
        inicio, _ = obter_periodo_ciclo_semanal(referencia)
        return inicio, agora_utc, "tempo_real"
    if modo_postagem:
        return (*obter_periodo_postagem_semanal(referencia), "semanal")
    inicio, _ = obter_periodo_ciclo_semanal(referencia)
    return inicio, agora_utc, "tempo_real"


def montar_view_ranking_laudos(
    contagem: dict[int, int],
    inicio_utc: datetime,
    fim_utc: datetime,
    *,
    periodo: str,
    guild: discord.Guild | None = None,
) -> tuple[discord.ui.LayoutView, int]:
    """Constrói o card de ranking e calcula seu total de laudos.

    Agrupa empates na mesma posição para que pessoas com a mesma produção
    recebam a mesma medalha. O período muda título, cor e subtítulo, enquanto a
    guilda é opcional para permitir gerar a visualização fora do Discord.
    """
    if periodo == "mensal":
        titulo = "🧠 **RANKING MENSAL DE LAUDOS**"
        subtitulo = (
            f"**Mês:** **{_formatar_mes_ano(inicio_utc)}** "
            f"({_formatar_data_curta(inicio_utc)} até {_formatar_data_curta(fim_utc)})"
        )
        cor = discord.Color.dark_teal()
    elif periodo == "tempo_real":
        titulo = "🧠 **RANKING DE LAUDOS — TEMPO REAL**"
        subtitulo = (
            f"**Ciclo atual:** **{_formatar_data_curta(inicio_utc)} até "
            f"{_formatar_data_curta(fim_utc)}** _(parcial)_"
        )
        cor = discord.Color.blurple()
    else:
        titulo = "🧠 **RANKING SEMANAL DE LAUDOS**"
        subtitulo = (
            f"**Semana:** **{_formatar_data_curta(inicio_utc)} até "
            f"{_formatar_data_curta(fim_utc)}**"
        )
        cor = discord.Color.green()

    if not contagem:
        corpo = (
            "_Nenhum laudo emitido neste período._\n\n"
            "# 📌 **TOTAL GERAL**\n"
            "📋 **Laudos:** **0**"
        )
        total = 0
    else:
        por_quantidade: dict[int, list[int]] = defaultdict(list)
        for discord_id, quantidade in contagem.items():
            por_quantidade[quantidade].append(discord_id)

        blocos: list[str] = []
        posicao = 1
        total = 0
        for quantidade in sorted(por_quantidade.keys(), reverse=True):
            ids = sorted(por_quantidade[quantidade])
            medalha = _medalha(posicao)
            mencoes = _formatar_mencoes(ids)
            rotulo = "laudo" if quantidade == 1 else "laudos"
            blocos.append(f"{medalha} {mencoes}\n↳ **{quantidade}** {rotulo}")
            total += quantidade * len(ids)
            posicao += len(ids)

        corpo = (
            "\n\n".join(blocos)
            + "\n\n"
            + "# 📌 **TOTAL GERAL**\n"
            + f"📋 **Laudos:** **{total}**"
        )

    momento_unix = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = (
        f"-# Psicologia · {guild.name} • <t:{momento_unix}:f>"
        if guild
        else f"-# Psicologia • <t:{momento_unix}:f>"
    )
    url_icone = guild.icon.url if guild and guild.icon else None
    cabecalho = f"{titulo}\n{subtitulo}"

    view = discord.ui.LayoutView(timeout=None)
    if url_icone:
        bloco_topo = discord.ui.Section(
            cabecalho,
            accessory=discord.ui.Thumbnail(url_icone),
        )
    else:
        bloco_topo = discord.ui.TextDisplay(cabecalho)

    view.add_item(
        discord.ui.Container(
            bloco_topo,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(corpo),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(rodape),
            accent_color=cor,
        )
    )
    return view, total


async def gerar_view_ranking_laudos(
    periodo: str,
    *,
    guild: discord.Guild | None = None,
    referencia: datetime | None = None,
    modo_postagem: bool = False,
) -> tuple[discord.ui.LayoutView, dict[int, int], datetime, datetime, int]:
    """Obtém a contagem do período e devolve todos os dados para publicação.

    O modo de postagem escolhe um intervalo fechado para registrar o ranking,
    enquanto o padrão mostra o ciclo em andamento. Retorna a view, contagem,
    início, fim e total para evitar recalcular dados em quem chama.
    """
    inicio, fim, periodo_view = _periodos(periodo, referencia, modo_postagem)
    contagem = await contar_laudos_por_psicologo(inicio, fim)
    view, total = montar_view_ranking_laudos(
        contagem,
        inicio,
        fim,
        periodo=periodo_view,
        guild=guild,
    )
    return view, contagem, inicio, fim, total


async def salvar_historico_laudos(
    *,
    tipo: str,
    inicio: datetime,
    fim: datetime,
    contagem: dict[int, int],
    total: int,
    channel_id: int | None = None,
    message_id: int | None = None,
) -> RankingHistorico:
    """tipo: laudos_semanal | laudos_mensal"""
    registro = RankingHistorico(
        tipo=tipo,
        periodo_inicio=inicio,
        periodo_fim=fim,
        total_recrutamentos=total,
        total_pago=0,
        payload_json=json.dumps(
            {str(chave): valor for chave, valor in contagem.items()}
        ),
        channel_id=channel_id,
        message_id=message_id,
        criado_em=agora(),
    )
    async with async_session() as sessao:
        sessao.add(registro)
        await sessao.commit()
        await sessao.refresh(registro)
    return registro


async def listar_historico_laudos(limite: int = 10) -> list[RankingHistorico]:
    """Lista os rankings de laudos mais recentes sem misturar outros domínios.

    O limite evita carregar todo o histórico administrativo e a ordenação
    descendente faz o primeiro resultado corresponder à publicação mais nova.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(RankingHistorico)
            .where(RankingHistorico.tipo.startswith("laudos"))
            .order_by(RankingHistorico.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())
