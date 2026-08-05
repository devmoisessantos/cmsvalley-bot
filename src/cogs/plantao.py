import discord
from discord import app_commands
from discord.ext import commands

from src.plantao.plantao_panel import (
    InformacoesPlantaoView,
    _buscar_estado,
)


class Plantao(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="informacoes_plantao", description="Veja seu status atual de plantão"
    )
    async def informacoes_plantao(self, interaction: discord.Interaction):
        estado = await _buscar_estado(interaction.user.id)
        view = InformacoesPlantaoView(interaction.user, estado)
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Plantao(bot))
