"""
Comando /avaliar-atendimento e registro do painel persistente.

O caminho principal é o painel fixo em CANAL_AVALIAR_ATENDIMENTO.
O slash command continua disponível como atalho.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.avaliacao.avaliacao_atendimento_panel import (
    PainelAvaliarAtendimentoLayout,
    iniciar_fluxo_avaliacao,
)


class AvaliarAtendimentoCog(commands.Cog):
    """Avaliação de atendimento médico/hospitalar."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Botão do painel fixo precisa ser reconhecido após reinício
        self.bot.add_view(PainelAvaliarAtendimentoLayout())

    @app_commands.command(
        name="avaliar-atendimento",
        description="Registra uma avaliação de atendimento (1 a 5 estrelas)",
    )
    async def avaliar_atendimento(self, interacao: discord.Interaction):
        """Abre o painel interativo de avaliação de atendimento.

        O fluxo pede o membro, a nota e o comentário em etapas, mostra um
        rascunho e só publica no mural depois da confirmação.
        """
        await iniciar_fluxo_avaliacao(interacao)


async def setup(bot: commands.Bot):
    """Registra o comando e a view persistente de avaliação de atendimento."""
    await bot.add_cog(AvaliarAtendimentoCog(bot))
