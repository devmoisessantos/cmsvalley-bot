"""
Único comando de barra do wipe: /wipe abre o painel efêmero.

Não há outros subcomandos. O controle fica nos botões do painel.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.mensagens import responder_erro
from src.utils.permissions import apenas_administrador
from src.wipe.wipe_panel import abrir_painel_wipe

registrador = logging.getLogger(__name__)


class WipeCog(commands.Cog):
    """Abre o painel efêmero de wipe."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="wipe",
        description="Abre o painel de wipe (só administradores, efêmero)",
    )
    @apenas_administrador()
    async def wipe(self, interacao: discord.Interaction) -> None:
        """Mostra o painel de controle só para quem executou o comando."""
        if interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Servidor necessário",
                linhas=["Use /wipe dentro do servidor."],
            )
            return
        await abrir_painel_wipe(interacao)


async def setup(bot: commands.Bot) -> None:
    """Registra o comando /wipe."""
    await bot.add_cog(WipeCog(bot))
    registrador.info("WipeCog registrado (comando /wipe → painel efêmero).")
