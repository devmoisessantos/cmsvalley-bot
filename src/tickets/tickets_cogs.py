"""
Cog de tickets: registra views persistentes e trata botões.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from src.tickets.tickets_panel import (
    PainelTicketDenunciasLayout,
    PainelTicketSuporteLayout,
    processar_clique_abrir_ticket,
)
from src.tickets.tickets_views import (
    CUSTOM_IDS_STAFF,
    CardBotoesStaffView,
    processar_clique_botao_ticket,
)


class TicketsCog(commands.Cog):
    """Listeners e registro de views do domínio de tickets."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Registra interfaces persistentes para que botões sobrevivam a reinícios."""
        # Views persistentes dos painéis e dos botões dentro do canal
        self.bot.add_view(PainelTicketSuporteLayout())
        self.bot.add_view(PainelTicketDenunciasLayout())
        self.bot.add_view(CardBotoesStaffView())

    @commands.Cog.listener()
    async def on_interaction(self, interacao: discord.Interaction) -> None:
        """
        Captura:
        - botões do painel (ticket:abrir:...)
        - botões de staff no canal (ticket:assumir, ticket:finalizar, etc.)
        """
        if interacao.type != discord.InteractionType.component:
            return

        custom_id = ""
        if interacao.data:
            custom_id = interacao.data.get("custom_id") or ""

        if custom_id.startswith("ticket:abrir:"):
            await processar_clique_abrir_ticket(interacao)
            return

        if custom_id in CUSTOM_IDS_STAFF:
            await processar_clique_botao_ticket(interacao)
            return


async def setup(bot: commands.Bot) -> None:
    """Adiciona ao bot os listeners e as interfaces do domínio de tickets."""
    await bot.add_cog(TicketsCog(bot))
