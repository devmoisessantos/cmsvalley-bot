# src/cogs/gerenciar_cargos.py
"""
Grupo /cargos — painel interativo de adicionar/remover cargos.

O painel em si continua em panels/gerenciar_cargos_panel.py.
Aqui só fica o comando de barra que abre o painel.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.panels.gerenciar_cargos_panel import GerenciarCargosView
from src.services.gerenciar_cargos_service import determinar_escopos
from src.utils.mensagens import responder_erro


class CargosCog(commands.Cog):
    """Comandos do grupo /cargos."""

    grupo_cargos = app_commands.Group(
        name="cargos",
        description="Gerenciamento de cargos de membros",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_cargos.command(
        name="painel",
        description="Abre o painel para adicionar ou remover cargos de um membro",
    )
    async def painel(self, interacao: discord.Interaction):
        membro_executor = interacao.user
        escopos_do_executor = determinar_escopos(membro_executor)

        if not escopos_do_executor:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Você não possui permissão para usar este comando."],
            )
            return

        view_do_painel = GerenciarCargosView(membro_executor)
        await interacao.response.send_message(view=view_do_painel, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CargosCog(bot))
