"""
Comando de barra para atualizar o quadro de hierarquia na mao.

O quadro se atualiza sozinho quando alguem muda de cargo. Este comando existe
para os casos em que a atualizacao automatica falhou ou alguem mexeu nos cargos
com o bot desligado.
"""

import discord
from discord import app_commands
from discord.ext import commands

from src.hierarquia.hierarquia_service import atualizar_hierarquia
from src.utils.mensagens import (
    responder_sucesso,
)


class Hierarquia(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="atualizar-hierarquia",
        description="Força a atualização do painel de hierarquia",
    )
    async def atualizar(self, interaction: discord.Interaction):
        """
        Reescreve o quadro de hierarquia agora, a pedido da equipe.

        Edita as mensagens do canal de hierarquia e responde a quem usou o comando com
        uma confirmacao que so ela ve. Use quando os cargos foram mexidos com o bot
        desligado e o quadro ficou desatualizado.
        """
        await interaction.response.defer(ephemeral=True)
        await atualizar_hierarquia(interaction.guild)
        await responder_sucesso(
            interaction,
            titulo="Hierarquia atualizada",
            linhas=[
                "Hierarquia atualizada.",
            ],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Hierarquia(bot))
