"""Publicacao idempotente do painel de compartilhamento de tela."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    GUILD_ID,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.screenshare.screenshare_panel import PainelScreenshareLayout

registrador = logging.getLogger(__name__)


async def garantir_painel_screenshare(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """
    Garante o painel em CANAL_PAINEL_SCREENSHARE.

    Se o canal for 0 (ainda nao criado no deploy), apenas registra e sai
    sem erro fatal.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(
                PainelPostado.nome_painel == "screenshare"
            )
        )
        registro = resultado.scalar_one_or_none()
        if registro is not None:
            return

        canal_id = CANAIS.get("CANAL_PAINEL_SCREENSHARE") or 0
        if not canal_id:
            registrador.warning(
                "CANAL_PAINEL_SCREENSHARE esta 0 — painel nao publicado. "
                "Crie o canal e atualize o config."
            )
            return

        canal = bot.get_channel(canal_id)
        if canal is None:
            registrador.error(
                "Canal CANAL_PAINEL_SCREENSHARE=%s nao encontrado.",
                canal_id,
            )
            return

        if interacao and interacao.guild:
            guilda = interacao.guild
        else:
            guilda = bot.get_guild(int(GUILD_ID))
        if guilda is None:
            registrador.error(
                "Guild nao encontrada ao publicar painel screenshare."
            )
            return

        view = PainelScreenshareLayout(guild=guilda)
        mensagem = await canal.send(view=view)
        sessao.add(
            PainelPostado(
                nome_painel="screenshare",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        registrador.info(
            "Painel de compartilhamento postado em #%s.",
            getattr(canal, "name", canal_id),
        )
