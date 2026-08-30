"""Keepalive da API de compartilhamento a cada 3 minutos."""

from __future__ import annotations

import logging

from discord.ext import commands, tasks

from src.screenshare.screenshare_service import (
    SCREENSHARE_KEEPALIVE_MINUTOS,
    checar_saude,
)

registrador = logging.getLogger(__name__)


class ScreenshareTasks(commands.Cog):
    """Mantem a API de salas acordada enquanto o bot estiver online."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        intervalo = max(1, SCREENSHARE_KEEPALIVE_MINUTOS)
        self.ping_keepalive.change_interval(minutes=intervalo)
        self.ping_keepalive.start()

    def cog_unload(self):
        self.ping_keepalive.cancel()

    @tasks.loop(minutes=3)
    async def ping_keepalive(self):
        ok, detalhe = await checar_saude()
        if ok:
            registrador.debug("Keepalive screenshare ok: %s", detalhe)
        else:
            registrador.warning("Keepalive screenshare falhou: %s", detalhe)

    @ping_keepalive.before_loop
    async def antes_do_keepalive(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ScreenshareTasks(bot))
