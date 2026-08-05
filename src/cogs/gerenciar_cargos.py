import discord
from discord import app_commands
from discord.ext import commands

from src.panels.gerenciar_cargos_panel import GerenciarCargosView
from src.services.gerenciar_cargos_service import determinar_escopos


class GerenciarCargos(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="gerenciar_cargos", description="Adiciona ou remove cargos de um membro"
    )
    async def gerenciar_cargos(self, interaction: discord.Interaction):
        escopos = determinar_escopos(interaction.user)

        if not escopos:
            await interaction.response.send_message(
                "❌ Você não possui permissão para usar este comando.", ephemeral=True
            )
            return

        view = GerenciarCargosView(interaction.user)
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GerenciarCargos(bot))
