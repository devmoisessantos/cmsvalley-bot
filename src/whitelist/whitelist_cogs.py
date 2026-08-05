import discord
from discord.ext import commands
from discord import app_commands
from sqlalchemy import select

from src.config import CANAIS
from src.database.connection import async_session
from src.database.models import PainelPostado


class Whitelist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="painel-whitelist",
        description="Criar o painel de Whitelist no canal configurado",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def painel_whitelist(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        canal = interaction.guild.get_channel(CANAIS["WHITELIST_CANAL_ID"])
        if canal is None:
            await interaction.followup.send("❌ Canal de Whitelist não encontrado.", ephemeral=True)
            return

        # Reaproveita a MESMA instância registrada no setup_hook — nunca cria uma nova aqui
        view = self.bot.painel_whitelist_view
        mensagem = await canal.send(view=view)

        async with async_session() as session:
            resultado = await session.execute(
                select(PainelPostado).where(PainelPostado.nome_painel == "whitelist")
            )
            registro = resultado.scalar_one_or_none()

            if registro is not None:
                registro.canal_id = canal.id
                registro.message_id = mensagem.id
            else:
                session.add(PainelPostado(
                    nome_painel="whitelist", 
                    canal_id=canal.id, 
                    message_id=mensagem.id
                ))

            await session.commit()

        await interaction.followup.send(
            "✅ Painel de Whitelist criado com sucesso.", 
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Whitelist(bot))