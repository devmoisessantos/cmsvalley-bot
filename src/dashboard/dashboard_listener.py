"""
Listener de mensagens do dashboard.

Conta uma mensagem por canal/dia para o gráfico de canais do painel. A conta
em si vive em `dashboard_contador.py` (sem Discord, testável); aqui só existe
o gancho do evento.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from src.dashboard.dashboard_contador import registrar_mensagem

registrador = logging.getLogger(__name__)


class DashboardListener(commands.Cog):
    """Soma as mensagens do servidor no contador por canal/dia."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, mensagem: discord.Message) -> None:
        """Conta mensagem de membro humano em servidor."""
        if mensagem.guild is None:
            return
        if mensagem.author.bot:
            return
        registrar_mensagem(mensagem.channel.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DashboardListener(bot))
