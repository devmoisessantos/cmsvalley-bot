"""Tasks e comandos de ranking de laudos psicológicos.

- Auto: semanal (sábado 11h) e mensal (dia 1 às 11h)
- Canal: CANAL_RANKING_LAUDOS
- Comandos: /ranking-laudos tempo-real | postar | historico
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from discord.ext import (
    commands,
    tasks,
)

from src.config import (
    CANAIS,
    GUILD_ID,
    RANKING_DIA_POST_MENSAL,
    RANKING_HORA_POST,
    TIMEZONE_LOCAL,
)
from src.laudos.ranking_laudos_service import (
    gerar_view_ranking_laudos,
    salvar_historico_laudos,
)

logger = logging.getLogger(__name__)


class RankingLaudosTasks(commands.Cog):
    """Loops automáticos de ranking de laudos (comandos slash em src/ranking/)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ultima_postagem_semanal: str | None = None
        self._ultima_postagem_mensal: str | None = None
        self.loop_ranking_laudos.start()
        logger.info("🧠 RankingLaudosTasks inicializado")

    def cog_unload(self):
        """Cancela o agendamento para não deixar loop ativo após descarregar o cog."""
        self.loop_ranking_laudos.cancel()

    @tasks.loop(minutes=1)
    async def loop_ranking_laudos(self):
        """Dispara rankings semanais e mensais uma única vez no horário definido.

        Compara chaves de data e mês para não publicar repetidamente durante o
        minuto de execução. A publicação automática também salva o histórico e
        tenta acionar o fechamento financeiro sem bloquear o ranking se falhar.
        """
        fuso = ZoneInfo(TIMEZONE_LOCAL)
        agora = datetime.now(fuso)

        if agora.hour != RANKING_HORA_POST or agora.minute != 0:
            return

        if agora.weekday() == 5:
            chave = agora.strftime("%Y-%m-%d")
            if self._ultima_postagem_semanal != chave:
                ok = await self._postar_automatico("semanal", agora)
                if ok:
                    self._ultima_postagem_semanal = chave

        if agora.day == RANKING_DIA_POST_MENSAL:
            chave = agora.strftime("%Y-%m")
            if self._ultima_postagem_mensal != chave:
                ok = await self._postar_automatico("mensal", agora)
                if ok:
                    self._ultima_postagem_mensal = chave

    @loop_ranking_laudos.before_loop
    async def antes_loop_ranking_laudos(self):
        """Espera a conexão do bot antes de verificar horários de publicação."""
        await self.bot.wait_until_ready()
        logger.info("Bot pronto — loop ranking de laudos ativo")

    async def _postar_automatico(
        self,
        periodo: str,
        referencia: datetime,
    ) -> bool:
        guilda = self.bot.get_guild(int(GUILD_ID))
        if guilda is None:
            logger.error("Guild não encontrada para ranking de laudos")
            return False

        canal_id = CANAIS.get("CANAL_RANKING_LAUDOS") or 0
        canal = guilda.get_channel(canal_id) if canal_id else None
        if canal is None:
            logger.error("CANAL_RANKING_LAUDOS não encontrado")
            return False

        try:
            view, contagem, inicio, fim, total = await gerar_view_ranking_laudos(
                periodo,
                guild=guilda,
                referencia=referencia,
                modo_postagem=True,
            )
            mensagem = await canal.send(view=view)
            await salvar_historico_laudos(
                tipo=f"laudos_{periodo}",
                inicio=inicio,
                fim=fim,
                contagem=contagem,
                total=total,
                channel_id=canal.id,
                message_id=mensagem.id,
            )
            try:
                from src.config import VALOR_UNITARIO_RANKING
                from src.financas.financas_service import processar_fechamento_ranking

                await processar_fechamento_ranking(
                    self.bot,
                    guilda,
                    chave_area="laudos",
                    contagem=contagem,
                    inicio=inicio,
                    fim=fim,
                    total_unidades=total,
                    total_pago=total * VALOR_UNITARIO_RANKING,
                )
            except Exception as erro_fin:
                logger.exception("Fechamento financeiro laudos: %s", erro_fin)

            logger.info(
                "Ranking laudos %s postado em #%s (total=%s)",
                periodo,
                canal.name,
                total,
            )
            return True
        except Exception as erro:
            logger.exception("Falha ao postar ranking laudos %s: %s", periodo, erro)
            return False


async def setup(bot: commands.Bot):
    """Registra o cog responsável pelos rankings e suas tarefas agendadas."""
    await bot.add_cog(RankingLaudosTasks(bot))
