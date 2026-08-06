"""Cog — garante painel e (opcional) comandos futuros de punição."""

from __future__ import annotations

import discord
from discord.ext import commands
from sqlalchemy import select

from src.config import CANAIS, GUILD_ID
from src.database.connection import async_session
from src.database.models import PainelPostado
from src.punicoes.panel import PainelPunicoesLayout


async def garantir_painel_punicoes(bot: discord.Client):
    async with async_session() as session:
        r = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "punicoes")
        )
        if r.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("PAINEL_PUNICOES") or CANAIS.get("CANAL_ADVERTENCIAS") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            print("⚠️ Canal do painel de punições não configurado.")
            return

        guild = bot.get_guild(int(GUILD_ID))
        if guild is None:
            return

        msg = await canal.send(view=PainelPunicoesLayout(guild))
        session.add(
            PainelPostado(
                nome_painel="punicoes",
                canal_id=canal.id,
                message_id=msg.id,
            )
        )
        await session.commit()
        print(f"✅ Painel de Punições postado em #{canal.name}.")


class PunicoesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(PunicoesCog(bot))
