"""
Comando de barra para atualizar o quadro de hierarquia na mao.

O quadro se atualiza sozinho quando alguem muda de cargo. Este comando existe
para os casos em que a atualizacao automatica falhou ou alguem mexeu nos cargos
com o bot desligado.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.hierarquia.hierarquia_service import atualizar_hierarquia
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
)
from src.utils.permissions import esta_autorizado

registrador = logging.getLogger(__name__)


class Hierarquia(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="atualizar-hierarquia",
        description="Força a atualização do painel de hierarquia",
    )
    @esta_autorizado()
    async def atualizar(self, interaction: discord.Interaction):
        """
        Reescreve o quadro de hierarquia agora, a pedido da equipe.

        Edita as mensagens do canal de hierarquia e responde a quem usou o comando com
        uma confirmacao que so ela ve. Use quando os cargos foram mexidos com o bot
        desligado e o quadro ficou desatualizado.
        """
        if interaction.guild is None:
            await responder_erro(
                interaction,
                titulo="Contexto inválido",
                linhas=["Use este comando dentro do servidor."],
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await atualizar_hierarquia(interaction.guild)
        except Exception as erro:
            registrador.exception("Falha ao forçar atualização da hierarquia: %s", erro)
            await responder_erro(
                interaction,
                titulo="Falha ao atualizar",
                linhas=[
                    "Não foi possível reescrever o quadro completo.",
                    f"Detalhe: `{str(erro)[:180]}`",
                ],
            )
            return

        await responder_sucesso(
            interaction,
            titulo="Hierarquia atualizada",
            linhas=[
                "Hierarquia atualizada.",
            ],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Hierarquia(bot))
