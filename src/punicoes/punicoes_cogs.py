"""Cog — garante painel e (opcional) comandos futuros de punição."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands
from sqlalchemy import select

from src.config import (
    CANAIS,
    GUILD_ID,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.punicoes.punicoes_panel import PainelPunicoesLayout

registrador = logging.getLogger(__name__)


async def garantir_painel_punicoes(bot: discord.Client):
    """Publica o painel uma única vez e grava sua mensagem como referência no banco.

    Verifica primeiro o registro persistido para que reinícios não criem painéis
    duplicados. Se o canal ou a guilda estiverem indisponíveis, apenas registra o
    problema e não cria uma referência inválida para uma mensagem inexistente.
    """
    async with async_session() as session:
        resultado_da_consulta = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "punicoes")
        )
        if resultado_da_consulta.scalar_one_or_none() is not None:
            return

        canal_id = (
            CANAIS.get("PAINEL_PUNICOES") or CANAIS.get("CANAL_ADVERTENCIAS") or 0
        )
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            registrador.warning("⚠️ Canal do painel de punições não configurado.")
            return

        guild = bot.get_guild(int(GUILD_ID))
        if guild is None:
            return

        mensagem = await canal.send(view=PainelPunicoesLayout(guild))
        session.add(
            PainelPostado(
                nome_painel="punicoes",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await session.commit()
        registrador.info(f"✅ Painel de Punições postado em #{canal.name}.")


class PunicoesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    """Registra o cog mínimo que mantém o domínio de punições carregado."""
    await bot.add_cog(PunicoesCog(bot))
