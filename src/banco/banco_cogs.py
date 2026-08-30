"""Comando /banco — painel administrativo das tabelas."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.banco.banco_panel import PainelBancoView
from src.utils.mensagens import (
    responder_erro,
    responder_view,
)
from src.utils.permissions import apenas_administrador


class BancoCog(commands.Cog):
    """Gerenciamento direto das tabelas do PostgreSQL (admin)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="banco",
        description="[Admin] Painel para listar, editar e apagar linhas do banco",
    )
    @apenas_administrador()
    async def banco(self, interacao: discord.Interaction):
        """
        Abre o painel ephemeral sem timeout.

        Lista todas as tabelas do metadata do bot. Permite paginar linhas,
        editar campos, apagar e inserir. Só administradores do Discord.
        """
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use este comando dentro do servidor."],
            )
            return
        if not interacao.user.guild_permissions.administrator:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas administradores podem usar `/banco`."],
            )
            return

        view = await PainelBancoView.criar(modo="tabelas")
        await responder_view(interacao, view, ephemeral=True)


async def setup(bot: commands.Bot):
    """Registra o cog de administração do banco."""
    await bot.add_cog(BancoCog(bot))
