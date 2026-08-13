# src/plantao/carteira_ranking.py
"""Ranking de moedas em tempo real (edita a mesma mensagem no canal)."""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    GUILD_ID,
)
from src.database.connection import async_session
from src.database.models import PainelPostado
from src.plantao.carteira_service import (
    equivalente_em_reais,
    ranking_top_moedas,
)
from src.recrutamento.recrutamento_service import resolver_id_fivem

logger = logging.getLogger(__name__)

NOME_PAINEL_RANKING_MOEDAS = "ranking_moedas_tempo_real"


async def _buscar_registro() -> PainelPostado | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(
                PainelPostado.nome_painel == NOME_PAINEL_RANKING_MOEDAS
            )
        )
        return resultado.scalar_one_or_none()


async def _salvar_registro(canal_id: int, message_id: int) -> None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(
                PainelPostado.nome_painel == NOME_PAINEL_RANKING_MOEDAS
            )
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            sessao.add(
                PainelPostado(
                    nome_painel=NOME_PAINEL_RANKING_MOEDAS,
                    canal_id=canal_id,
                    message_id=message_id,
                )
            )
        else:
            registro.canal_id = canal_id
            registro.message_id = message_id
        await sessao.commit()


async def montar_view_ranking_moedas(guilda: discord.Guild) -> discord.ui.LayoutView:
    top = await ranking_top_moedas(15)
    medalhas = {1: "🥇", 2: "🥈", 3: "🥉"}
    linhas_rank: list[str] = []

    for posicao, (discord_id, saldo) in enumerate(top, start=1):
        membro = guilda.get_member(discord_id)
        mencao = membro.mention if membro else f"`{discord_id}`"
        fid = await resolver_id_fivem(discord_id) or "—"
        prefixo = medalhas.get(posicao, f"`{posicao}.`")
        if posicao <= 3:
            linhas_rank.append(
                f"{prefixo} **{posicao}.** {mencao} | FID `{fid}` · "
                f"**{saldo}** · `{equivalente_em_reais(saldo)}`"
            )
        else:
            linhas_rank.append(
                f"`{posicao}.` {mencao} | FID `{fid}` · "
                f"**{saldo}** · `{equivalente_em_reais(saldo)}`"
            )

    if not linhas_rank:
        corpo_rank = "_Ninguém com saldo de moedas ainda._"
    else:
        corpo_rank = "\n".join(linhas_rank)

    momento = int(datetime.now(timezone.utc).timestamp())
    url_icone = guilda.icon.url if guilda.icon else None

    titulo = "# 🏆 Ranking de Moedas — Top 15"
    intro = (
        "> Classificação por **saldo atual** · sem premiação · sem reset\n\n"
        f"## Ranking\n{corpo_rank}"
    )
    rodape = f"-# {guilda.name} • atualizado em tempo real · <t:{momento}:R>"

    componentes: list = []
    if url_icone:
        componentes.append(
            discord.ui.Section(
                titulo,
                intro,
                accessory=discord.ui.Thumbnail(url_icone),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(f"{titulo}\n{intro}"))
    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(discord.ui.TextDisplay(rodape))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*componentes, accent_color=discord.Color.gold()))
    return view


async def atualizar_ranking_moedas(bot: discord.Client) -> None:
    """Cria ou edita a mensagem única do ranking de moedas."""
    canal_id = CANAIS.get("RANKING_MOEDAS") or 0
    if not canal_id:
        return

    guilda = bot.get_guild(int(GUILD_ID))
    if guilda is None:
        return
    canal = guilda.get_channel(int(canal_id))
    if not isinstance(canal, discord.TextChannel):
        return

    view = await montar_view_ranking_moedas(guilda)
    registro = await _buscar_registro()

    if registro is not None:
        try:
            mensagem = await canal.fetch_message(int(registro.message_id))
            await mensagem.edit(view=view)
            return
        except (discord.NotFound, discord.HTTPException) as erro:
            logger.warning("Ranking moedas: recriando mensagem (%s)", erro)

    try:
        mensagem = await canal.send(view=view)
        await _salvar_registro(canal.id, mensagem.id)
    except discord.HTTPException as erro:
        logger.warning("Ranking moedas: falha ao postar (%s)", erro)
