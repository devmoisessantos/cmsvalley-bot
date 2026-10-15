"""Tarefas periódicas do domínio de cursos (prazo de 48h)."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from src.config import GUILD_ID
from src.cursos.cursos_service import (
    PRAZO_HORAS_CURSO,
    anular_e_apagar_pedido,
    creditar_moedas_instrutor,
    listar_pedidos_expirados,
)
from src.utils.error_handling import ignorar_falha_cosmetica

logger = logging.getLogger(__name__)


async def _apagar_mensagem_do_canal(
    guilda: discord.Guild,
    canal_id: int | None,
    mensagem_id: int | None,
) -> None:
    """Apaga a mensagem do card se canal e id ainda existirem."""
    if not canal_id or not mensagem_id:
        return
    canal = guilda.get_channel(int(canal_id))
    if canal is None:
        return
    try:
        mensagem = await canal.fetch_message(int(mensagem_id))
        await mensagem.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as erro:
        ignorar_falha_cosmetica(
            erro,
            o_que_falhou=(
                f"Apagar mensagem de pedido expirado "
                f"(canal={canal_id}, msg={mensagem_id})"
            ),
        )


class CursosTasks(commands.Cog):
    """Varre pedidos AGENDADO/ACEITO e anula os que passaram de 48h."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.varrer_prazos_cursos.start()
        logger.info(
            "CursosTasks iniciado — prazo de %sh a cada 15 min",
            PRAZO_HORAS_CURSO,
        )

    def cog_unload(self):
        """Cancela a varredura ao descarregar o cog."""
        self.varrer_prazos_cursos.cancel()

    @tasks.loop(minutes=15)
    async def varrer_prazos_cursos(self):
        """
        Anula pedidos sem aceite, sem decisão ou sem pagamento em 48h.

        - AGENDADO: sem aceite em 48h desde a criação
        - ACEITO: sem decisão (e sem pagamento, se exigido) em 48h
          desde o aceite

        Apaga a mensagem do canal (ids gravados no pedido) e remove a
        linha do banco. Devolve moedas de desconto ao aluno, se houver.
        """
        guilda = self.bot.get_guild(int(GUILD_ID))
        if guilda is None:
            return
        try:
            expirados = await listar_pedidos_expirados(
                prazo_horas=PRAZO_HORAS_CURSO,
            )
        except Exception as erro:
            logger.warning("varrer_prazos_cursos DB: %s", erro)
            return

        for registro in expirados:
            try:
                canal_id = getattr(registro, "mensagem_canal_id", None)
                mensagem_id = getattr(registro, "mensagem_id", None)
                aluno_id = int(registro.discord_id)
                pedido_id = int(registro.id)
                moedas = int(getattr(registro, "moedas_debitadas", 0) or 0)
                status = registro.status

                await _apagar_mensagem_do_canal(guilda, canal_id, mensagem_id)
                apagado = await anular_e_apagar_pedido(pedido_id)
                if apagado is None:
                    continue

                if moedas > 0:
                    try:
                        await creditar_moedas_instrutor(aluno_id, moedas)
                    except Exception as erro_moedas:
                        logger.exception(
                            "Falha ao devolver %s moedas do pedido %s: %s",
                            moedas,
                            pedido_id,
                            erro_moedas,
                        )

                aluno = guilda.get_member(aluno_id)
                if aluno is not None:
                    try:
                        await aluno.send(
                            f"Seu pedido de curso `#{pedido_id}` "
                            f"(status **{status}**) foi **anulado** por "
                            f"passar de **{PRAZO_HORAS_CURSO}h** sem "
                            f"aceite, decisão ou registro de pagamento. "
                            f"Solicite de novo pelo painel se ainda "
                            f"precisar."
                        )
                    except (discord.Forbidden, discord.HTTPException) as erro_dm:
                        ignorar_falha_cosmetica(
                            erro_dm,
                            o_que_falhou=(
                                f"DM de anulação do pedido #{pedido_id}"
                            ),
                        )

                logger.info(
                    "Pedido de curso #%s anulado por prazo (%s)",
                    pedido_id,
                    status,
                )
            except Exception as erro_pedido:
                logger.exception(
                    "Falha ao anular pedido de curso #%s: %s",
                    getattr(registro, "id", "?"),
                    erro_pedido,
                )

    @varrer_prazos_cursos.before_loop
    async def antes_varrer_prazos_cursos(self):
        """Espera o bot conectar antes de iniciar a varredura."""
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    """Registra as tarefas de prazo de cursos no bot."""
    await bot.add_cog(CursosTasks(bot))
