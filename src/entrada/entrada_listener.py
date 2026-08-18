"""
Ouvinte de entrada e saída de membros no servidor.

Escuta ``on_member_join`` e ``on_member_remove`` e delega o trabalho
para o serviço do domínio, sem misturar regra de negócio com o cog.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from src.entrada.entrada_service import (
    processar_entrada_do_membro,
    processar_saida_do_membro,
)

registrador = logging.getLogger(__name__)


class EntradaListener(commands.Cog):
    """Publica cards de boas-vindas e adeus quando membros entram ou saem."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, membro: discord.Member):
        """
        Dispara o fluxo de boas-vindas e registro na tabela usuarios.
        """
        try:
            await processar_entrada_do_membro(membro)
        except Exception as erro_capturado:
            registrador.exception(
                "Falha no on_member_join de entrada para %s: %s",
                membro.id,
                erro_capturado,
            )

    @commands.Cog.listener()
    async def on_member_remove(self, membro: discord.Member):
        """
        Dispara o fluxo de adeus no canal configurado.
        """
        try:
            await processar_saida_do_membro(membro)
        except Exception as erro_capturado:
            registrador.exception(
                "Falha no on_member_remove de entrada para %s: %s",
                membro.id,
                erro_capturado,
            )


async def setup(bot: commands.Bot):
    """Registra o ouvinte de entrada no bot."""
    await bot.add_cog(EntradaListener(bot))
