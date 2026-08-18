"""Publicação idempotente do painel de notificação por DM."""

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
from src.notificacoes.notificacoes_panel import PainelNotificacaoLayout

registrador = logging.getLogger(__name__)


async def _resolver_guilda(
    bot: discord.Client,
    interacao: discord.Interaction | None,
) -> discord.Guild | None:
    if interacao is not None and interacao.guild is not None:
        return interacao.guild
    return bot.get_guild(int(GUILD_ID))


async def garantir_painel_notificacao(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Garante o painel fixo no canal CANAL_ENVIAR_NOTIFICACAO."""
    nome_painel = "enviar_notificacao"

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == nome_painel)
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_ENVIAR_NOTIFICACAO") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            registrador.warning(
                "⚠️ Canal de enviar notificação não configurado/encontrado. "
                "Confira CANAIS['CANAL_ENVIAR_NOTIFICACAO']."
            )
            return

        guilda = await _resolver_guilda(bot, interacao)
        if guilda is None:
            registrador.error(
                "❌ Guild não encontrada ao postar painel de notificação."
            )
            return

        mensagem = await canal.send(view=PainelNotificacaoLayout(guilda=guilda))
        sessao.add(
            PainelPostado(
                nome_painel=nome_painel,
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        registrador.info(f"✅ Painel de Notificação por DM postado em #{canal.name}.")
