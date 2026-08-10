"""Tasks: expiração de prazo e (opcional) limpeza informativa de ciclo."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from src.bau.bau_logger import log_verbal_aplicada, publicar_alerta_caso
from src.bau.bau_service import aplicar_verbal_automatica, listar_casos_expirados
from src.config import GUILD_ID, LIMITES_BAU_CAMADA_1, LIMITES_BAU_CAMADA_2

logger = logging.getLogger(__name__)


class BauTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.varrer_prazos.start()
        logger.info("BauTasks iniciado — varredura de prazos a cada 1 min")

    def cog_unload(self):
        self.varrer_prazos.cancel()

    @tasks.loop(minutes=1)
    async def varrer_prazos(self):
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
                limite_1 = LIMITES_BAU_CAMADA_1.get(caso.item_canonico, 0)
                limite_2 = LIMITES_BAU_CAMADA_2.get(caso.item_canonico)
                if caso.canal_alerta_message_id:
                    await publicar_alerta_caso(
                        guilda,
                        caso,
                        limite_1=limite_1,
                        limite_2=limite_2,
                        atualizar_mensagem_id=caso.canal_alerta_message_id,
                    )
            except Exception as erro_caso:
                logger.exception("prazo caso %s: %s", caso.id, erro_caso)

    @varrer_prazos.before_loop
    async def antes_varrer(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(BauTasks(bot))
