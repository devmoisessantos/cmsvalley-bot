"""
Tarefas periódicas do domínio dashboard.

- Heartbeat a cada 1 minuto (status do bot + snapshot de guild)
- Consumo da fila de comandos do site a cada 15 segundos
"""

from __future__ import annotations

import logging

from discord.ext import commands, tasks

from src.dashboard import dashboard_repositorio as repositorio
from src.dashboard.dashboard_cliente import obter_cliente
from src.dashboard.dashboard_contador import (
    dia_de_hoje,
    limpar_antigos,
    montar_itens,
    semear,
)
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
        self.cliente = obter_cliente()

    async def cog_load(self) -> None:
        await self.cliente.abrir()
        if not self.cliente.ativo:
            return
        carregados = await sincronizar_metas_por_cargo_do_dashboard(self.cliente)
        if carregados == 0:
            # API fora do ar na subida: tenta a tabela do banco antes de
            # desistir e ficar com o src/config.py.
            carregados = await repositorio.sincronizar_metas_do_banco()
            if carregados:
                registrador.info(
                    "Metas por cargo carregadas do banco (%s cargos).", carregados
                )

        # Continua a contagem de mensagens de onde o banco parou hoje.
        contagens = await self.cliente.buscar_estatisticas(dia_de_hoje())
        semear(contagens)

        self.tarefa_sinal_do_bot.start()
        self.tarefa_fila.start()
        self.tarefa_estatisticas.start()
        registrador.info("Tasks do dashboard iniciadas.")

    async def cog_unload(self) -> None:
        if self.tarefa_sinal_do_bot.is_running():
            self.tarefa_sinal_do_bot.cancel()
        if self.tarefa_fila.is_running():
            self.tarefa_fila.cancel()
        if self.tarefa_estatisticas.is_running():
            self.tarefa_estatisticas.cancel()
        # Última tentativa de mandar o que foi contado desde o último envio.
        try:
            await self._enviar_estatisticas()
        except Exception as falha:  # noqa: BLE001
            registrador.warning(
                "Não consegui enviar as estatísticas ao desligar: %s", falha
            )
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

    # ------------------------------------------------------------------
    # Estatísticas de mensagens por canal
    # ------------------------------------------------------------------

    async def _enviar_estatisticas(self) -> None:
        """Manda os totais de mensagens por canal/dia para a API."""
        itens = montar_itens(self.bot)
        if not itens:
            return
        resultado = await self.cliente.enviar_estatisticas(itens)
        if resultado is None:
            registrador.warning(
                "Envio de estatísticas de canal falhou (será repetido em 10 min)."
            )
            return
        limpar_antigos()

    @tasks.loop(minutes=10)
    async def tarefa_estatisticas(self) -> None:
        """Envia de 10 em 10 minutos as mensagens contadas por canal."""
        await self._enviar_estatisticas()

    @tarefa_estatisticas.before_loop
    async def _esperar_pronto_estatisticas(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DashboardTasks(bot))
