"""Tasks: expiração de prazo e (opcional) limpeza informativa de ciclo."""

from __future__ import annotations

import logging

from discord.ext import (
    commands,
    tasks,
)

from src.bau.bau_logger import (
    log_verbal_aplicada,
    publicar_alerta_caso,
)
from src.bau.bau_service import (
    aplicar_verbal_automatica,
    listar_casos_expirados,
)
from src.config import GUILD_ID

logger = logging.getLogger(__name__)


class BauTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.varrer_prazos.start()
        logger.info("BauTasks iniciado — varredura de prazos a cada 1 min")

    def cog_unload(self):
        """Cancela a varredura para impedir tarefa órfã ao descarregar o cog."""
        self.varrer_prazos.cancel()

    @tasks.loop(minutes=1)
    async def varrer_prazos(self):
        """Aplica medidas aos casos cujo prazo de devolução se esgotou.

        Busca somente o servidor configurado e os casos vencidos, registra a
        verbal automática no banco e publica ou atualiza o alerta no Discord.
        Trata falhas por caso para que uma ocorrência com problema não impeça a
        análise dos demais na próxima execução.
        """
        guilda = self.bot.get_guild(int(GUILD_ID))
        if guilda is None:
            return
        try:
            casos = await listar_casos_expirados()
        except Exception as erro:
            logger.warning("varrer_prazos DB: %s", erro)
            return

        for caso in casos:
            try:
                tipo, _registro = await aplicar_verbal_automatica(caso)
                await log_verbal_aplicada(guilda, caso=caso, tipo=tipo)

                # Releitura: status no banco já é PRAZO_ESTOURADO — Valley liberado
                from src.database.conexao import async_session
                from src.database.models import CasoBau

                async with async_session() as sessao:
                    caso_atualizado = await sessao.get(CasoBau, caso.id)
                if caso_atualizado is None:
                    continue

                if caso_atualizado.canal_alerta_message_id:
                    await publicar_alerta_caso(
                        guilda,
                        caso_atualizado,
                        limite_1=0,
                        limite_2=None,
                        atualizar_mensagem_id=caso_atualizado.canal_alerta_message_id,
                    )
            except Exception as erro_caso:
                logger.exception("prazo caso %s: %s", caso.id, erro_caso)

    @varrer_prazos.before_loop
    async def antes_varrer(self):
        """Espera o bot conectar antes de iniciar a varredura periódica."""
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    """Registra as tarefas de prazo do baú no bot."""
    await bot.add_cog(BauTasks(bot))
