"""Publicação idempotente dos painéis de ticket."""

from __future__ import annotations

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    GUILD_ID,
)
from src.database.connection import async_session
from src.database.models import PainelPostado
from src.tickets.tickets_panel import (
    PainelTicketDenunciasLayout,
    PainelTicketSuporteLayout,
)


async def _resolver_guilda(
    bot: discord.Client,
    interacao: discord.Interaction | None,
) -> discord.Guild | None:
    if interacao is not None and interacao.guild is not None:
        return interacao.guild
    return bot.get_guild(int(GUILD_ID))


async def garantir_painel_ticket_suporte(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Garante o painel fixo de suporte/dúvidas/revogações."""
    nome_painel = "ticket_suporte"

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == nome_painel)
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_ABRIR_SUPORTE_DUVIDAS") or 0
        canal = bot.get_channel(int(canal_id)) if canal_id else None
        if canal is None:
            print("⚠️ Canal CANAL_ABRIR_SUPORTE_DUVIDAS não configurado/encontrado.")
            return

        guilda = await _resolver_guilda(bot, interacao)
        if guilda is None:
            print("❌ Guild não encontrada ao postar painel de ticket suporte.")
            return

        mensagem = await canal.send(view=PainelTicketSuporteLayout(guilda=guilda))
        sessao.add(
            PainelPostado(
                nome_painel=nome_painel,
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        print(f"✅ Painel de Ticket (Suporte) postado em #{canal.name}.")


async def garantir_painel_ticket_denuncias(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Garante o painel fixo de denúncias."""
    nome_painel = "ticket_denuncias"

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == nome_painel)
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_ABRIR_TICKET_DENUNCIAS") or 0
        canal = bot.get_channel(int(canal_id)) if canal_id else None
        if canal is None:
            print("⚠️ Canal CANAL_ABRIR_TICKET_DENUNCIAS não configurado/encontrado.")
            return

        guilda = await _resolver_guilda(bot, interacao)
        if guilda is None:
            print("❌ Guild não encontrada ao postar painel de ticket denúncias.")
            return

        mensagem = await canal.send(view=PainelTicketDenunciasLayout(guilda=guilda))
        sessao.add(
            PainelPostado(
                nome_painel=nome_painel,
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        print(f"✅ Painel de Ticket (Denúncias) postado em #{canal.name}.")
