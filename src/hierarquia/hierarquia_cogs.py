"""
Comando de barra para atualizar o quadro de hierarquia na mao.

O quadro se atualiza sozinho quando alguem muda de cargo. Este comando existe
para os casos em que a atualizacao automatica falhou ou alguem mexeu nos cargos
com o bot desligado.

Subcomandos:
- /atualizar-hierarquia hospital — quadro hospitalar
- /atualizar-hierarquia gate — quadro GATE
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.hierarquia.hierarquia_service import (
    atualizar_hierarquia,
    atualizar_hierarquia_gate,
)
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
)
from src.utils.permissions import esta_autorizado

registrador = logging.getLogger(__name__)


class Hierarquia(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    grupo_hierarquia = app_commands.Group(
        name="atualizar-hierarquia",
        description="Força a atualização dos painéis de hierarquia",
    )

    @grupo_hierarquia.command(
        name="hospital",
        description="Atualiza o quadro de hierarquia hospitalar",
    )
    @esta_autorizado()
    async def atualizar_hospital(self, interaction: discord.Interaction):
        """Reescreve o quadro hospitalar no canal HIERARQUIA_SUL."""
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
            registrador.exception(
                "Falha ao forçar atualização da hierarquia hospitalar: %s",
                erro,
            )
            await responder_erro(
                interaction,
                titulo="Falha ao atualizar",
                linhas=[
                    "Não foi possível reescrever o quadro hospitalar.",
                    f"Detalhe: `{str(erro)[:180]}`",
                ],
            )
            return

        await responder_sucesso(
            interaction,
            titulo="Hierarquia hospitalar atualizada",
            linhas=["Quadro de hierarquia **hospitalar** atualizado."],
        )

    @grupo_hierarquia.command(
        name="gate",
        description="Atualiza o quadro de hierarquia GATE",
    )
    @esta_autorizado()
    async def atualizar_gate(self, interaction: discord.Interaction):
        """Reescreve o quadro GATE no canal HIERARQUIA_GATE."""
        if interaction.guild is None:
            await responder_erro(
                interaction,
                titulo="Contexto inválido",
                linhas=["Use este comando dentro do servidor."],
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await atualizar_hierarquia_gate(interaction.guild)
        except Exception as erro:
            registrador.exception(
                "Falha ao forçar atualização da hierarquia GATE: %s",
                erro,
            )
            await responder_erro(
                interaction,
                titulo="Falha ao atualizar",
                linhas=[
                    "Não foi possível reescrever o quadro GATE.",
                    f"Detalhe: `{str(erro)[:180]}`",
                ],
            )
            return

        await responder_sucesso(
            interaction,
            titulo="Hierarquia GATE atualizada",
            linhas=["Quadro de hierarquia **GATE** atualizado."],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Hierarquia(bot))
