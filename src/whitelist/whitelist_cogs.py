"""
Comandos de barra da whitelist, para a equipe usar quando precisa.

O caminho normal do membro e o painel. Estes comandos existem para os casos
manuais: conferir, refazer ou corrigir uma whitelist na mao.
"""

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from src.config import CANAIS
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
)


class Whitelist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="painel-whitelist",
        description="Criar o painel de Whitelist no canal configurado",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def painel_whitelist(self, interaction: discord.Interaction):
        """Publica o painel configurado e atualiza sua referência persistida.

        Reutiliza a mesma interface registrada no início do bot para preservar
        os callbacks persistentes. O banco recebe a nova mensagem, permitindo
        manutenção posterior sem depender de procurar o canal manualmente.
        """
        await interaction.response.defer(ephemeral=True)

        canal = interaction.guild.get_channel(CANAIS["WHITELIST_CANAL_ID"])
        if canal is None:
            await responder_erro(
                interaction,
                titulo="Não encontrado",
                linhas=[
                    "Canal de Whitelist não encontrado.",
                ],
            )
            return

        # Reaproveita a MESMA instância registrada no setup_hook — nunca cria uma nova
        # aqui
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
                session.add(
                    PainelPostado(
                        nome_painel="whitelist",
                        canal_id=canal.id,
                        message_id=mensagem.id,
                    )
                )

            await session.commit()

        await responder_sucesso(
            interaction,
            titulo="Painel criado",
            linhas=[
                "Painel de Whitelist criado com sucesso.",
            ],
        )


async def setup(bot: commands.Bot):
    """Adiciona ao bot o comando administrativo do painel de whitelist."""
    await bot.add_cog(Whitelist(bot))
