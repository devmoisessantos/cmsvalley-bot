"""
Tarefas periódicas do domínio dashboard.

- Heartbeat a cada 1 minuto (status do bot + snapshot de guild)
- Consumo da fila de comandos do site a cada 15 segundos
"""

from __future__ import annotations

import logging

from discord.ext import commands, tasks

from src.dashboard.dashboard_cliente import ClienteDashboard
from src.dashboard.dashboard_logger import log_sinal_do_bot_falhou
from src.dashboard.dashboard_service import (
    executar_comando_da_fila,
    montar_corpo_do_sinal,
    sincronizar_metas_por_cargo_do_dashboard,
)

registrador = logging.getLogger(__name__)


class DashboardTasks(commands.Cog):
    """Loops de sinal do bot e fila do dashboard web."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cliente = ClienteDashboard()

    async def cog_load(self) -> None:
        await self.cliente.abrir()
        if not self.cliente.ativo:
            return
        await sincronizar_metas_por_cargo_do_dashboard(self.cliente)
        self.tarefa_sinal_do_bot.start()
        self.tarefa_fila.start()
        registrador.info("Tasks do dashboard iniciadas.")

    async def cog_unload(self) -> None:
        if self.tarefa_sinal_do_bot.is_running():
            self.tarefa_sinal_do_bot.cancel()
        if self.tarefa_fila.is_running():
            self.tarefa_fila.cancel()
        await self.cliente.fechar()

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def tarefa_sinal_do_bot(self) -> None:
        """Avisa a API que o bot está vivo e manda o snapshot da guild."""
        corpo = montar_corpo_do_sinal(self.bot)
        resposta = await self.cliente.enviar_sinal_do_bot(corpo)
        if resposta is None:
            registrador.warning(
                "Heartbeat do dashboard falhou (URL/token ou API fora). "
                "Confira DASHBOARD_API_URL e DASHBOARD_API_TOKEN = JWT_SECRET."
            )
            if self.bot.guilds:
                await log_sinal_do_bot_falhou(
                    self.bot.guilds[0],
                    "POST /internal/sinal-do-bot sem resposta válida",
                )

    @tarefa_sinal_do_bot.before_loop
    async def _esperar_pronto_sinal(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Fila de comandos
    # ------------------------------------------------------------------

    @tasks.loop(seconds=15)
    async def tarefa_fila(self) -> None:
        """Puxa e executa comandos enfileirados pelo site."""
        comandos = await self.cliente.buscar_comandos(limite=5)
        for comando in comandos:
            await executar_comando_da_fila(self.bot, self.cliente, comando)

    @tarefa_fila.before_loop
    async def _esperar_pronto_fila(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DashboardTasks(bot))
