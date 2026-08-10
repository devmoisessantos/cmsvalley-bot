"""Publicação idempotente dos painéis do domínio membros.

- gerenciar_cargos  → CANAIS['MANAGE_ROLE_CHANNEL_ID']
- gerenciar_membros → CANAIS['CANAL_GERENCIAR_MEMBROS']
"""

from __future__ import annotations

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    GUILD_ID,
)
from src.database.connection import async_session
from src.database.models import PainelPostado
from src.membros.cargos_panel import PainelGerenciarCargoLayout
from src.membros.membros_panel import PainelGerenciarMembrosLayout


async def _resolver_guilda(
    bot: discord.Client,
    interacao: discord.Interaction | None,
) -> discord.Guild | None:
    if interacao is not None and interacao.guild is not None:
        return interacao.guild
    return bot.get_guild(int(GUILD_ID))


async def garantir_painel_gerenciar_cargos(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Garante o painel de gerenciamento de cargos no canal configurado."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "gerenciar_cargos")
        )
        registro = resultado.scalar_one_or_none()

        canal = bot.get_channel(CANAIS["MANAGE_ROLE_CHANNEL_ID"])
        if canal is None:
            print(
                "❌ Canal de Gerenciamento de Cargos não encontrado. "
                "Confira CANAIS['MANAGE_ROLE_CHANNEL_ID']."
            )
            return

        if registro is not None:
            return

        guilda = await _resolver_guilda(bot, interacao)
        if guilda is None:
            print("❌ Guild não encontrada!")
            return

        view_do_painel = PainelGerenciarCargoLayout(guild=guilda)
        mensagem = await canal.send(view=view_do_painel)

        sessao.add(
            PainelPostado(
                nome_painel="gerenciar_cargos",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        print(f"✅ Painel de Gerenciamento de Cargos postado no canal #{canal.name}.")


async def garantir_painel_gerenciar_membros(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Garante o painel de gerenciar membros no canal configurado."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(
                PainelPostado.nome_painel == "gerenciar_membros"
            )
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_GERENCIAR_MEMBROS") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            print("⚠️ Canal #gerenciar-membros não configurado/encontrado.")
            return

        guilda = await _resolver_guilda(bot, interacao)
        if guilda is None:
            return

        mensagem = await canal.send(view=PainelGerenciarMembrosLayout(guild=guilda))
        sessao.add(
            PainelPostado(
                nome_painel="gerenciar_membros",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        print(f"✅ Painel Gerenciar Membros postado em #{canal.name}.")
