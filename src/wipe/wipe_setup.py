"""Publicação idempotente do painel de wipe."""

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
from src.wipe.wipe_panel import PainelWipeLayout

registrador = logging.getLogger(__name__)

NOME_PAINEL_WIPE = "wipe"


async def garantir_painel_wipe(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """
    Garante o painel de wipe no canal LOGS_WIPE / CANAL_PAINEL_WIPE.

    Se já existe registro em paineis_postados, não duplica.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(
                PainelPostado.nome_painel == NOME_PAINEL_WIPE
            )
        )
        registro = resultado.scalar_one_or_none()

        id_canal = CANAIS.get("CANAL_PAINEL_WIPE") or CANAIS.get("LOGS_WIPE")
        if not id_canal:
            registrador.error(
                "Canal do painel wipe não configurado "
                "(CANAL_PAINEL_WIPE / LOGS_WIPE)."
            )
            return

        canal = bot.get_channel(id_canal)
        if canal is None:
            registrador.error(
                "Canal do painel wipe não encontrado (id %s).", id_canal
            )
            return

        if registro is not None:
            return

        if interacao is not None and interacao.guild is not None:
            guilda = interacao.guild
        else:
            guilda = bot.get_guild(int(GUILD_ID))

        if guilda is None:
            registrador.error("Guild não encontrada para o painel wipe.")
            return

        view_do_painel = PainelWipeLayout(guild=guilda)
        mensagem = await canal.send(view=view_do_painel)

        sessao.add(
            PainelPostado(
                nome_painel=NOME_PAINEL_WIPE,
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        registrador.info(
            "Painel de wipe postado no canal #%s.", canal.name
        )
