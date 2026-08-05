import discord
from discord import app_commands
from discord.ext import commands

from src.hierarquia.hierarquia_service import atualizar_hierarquia


class Hierarquia(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
            name="atualizar-hierarquia", 
            description="Força a atualização do painel de hierarquia")
    async def atualizar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await atualizar_hierarquia(interaction.guild)
        await interaction.followup.send("✅ Hierarquia atualizada.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Hierarquia(bot))