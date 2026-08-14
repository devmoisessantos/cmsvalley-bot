"""
Cog de tickets: registra views persistentes e trata botões de staff.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from src.tickets.tickets_panel import (
    PainelTicketDenunciasLayout,
    PainelTicketSuporteLayout,
)
from src.tickets.tickets_views import (
    BotoesTicketView,
    processar_clique_botao_ticket,
)


class TicketsCog(commands.Cog):
    """Listeners e registro de views do domínio de tickets."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Views persistentes dos painéis e dos botões dentro do canal
        self.bot.add_view(PainelTicketSuporteLayout())
        self.bot.add_view(PainelTicketDenunciasLayout())
        self.bot.add_view(BotoesTicketView())

    @commands.Cog.listener()
    async def on_interaction(self, interacao: discord.Interaction) -> None:
        """
        Captura cliques nos botões ticket:assumir / ticket:finalizar.

        Os selects dos painéis já têm callback próprio na classe Select.
        """
        if interacao.type != discord.InteractionType.component:
            return

        custom_id = ""
        if interacao.data:
            custom_id = interacao.data.get("custom_id") or ""

        if custom_id in ("ticket:assumir", "ticket:finalizar"):
            await processar_clique_botao_ticket(interacao)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketsCog(bot))
