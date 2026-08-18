"""Cog finanças — registra views persistentes do botão Pagamento realizado."""

from __future__ import annotations

import logging

from discord.ext import commands

from src.financas.financas_views import view_persistente_financas

logger = logging.getLogger(__name__)


class FinancasCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.loop.create_task(self._registrar_views())

    async def _registrar_views(self):
        await self.bot.wait_until_ready()
        self.bot.add_view(view_persistente_financas())
        logger.info("View persistente de finanças (Pagamento realizado) registrada")


async def setup(bot: commands.Bot):
    """Registra o cog que restaura o botão persistente de pagamentos."""
    await bot.add_cog(FinancasCog(bot))
