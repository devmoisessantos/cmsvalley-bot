"""Tasks e comandos de ranking de recrutadores.

- Auto: semanal (sábado 11h) e mensal (dia 1 às 11h)
- Comandos: tempo real, postar, histórico
- UI: Components V2 (LayoutView + Container) — sem embeds
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
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
from src.recrutamento.ranking_service import (
    gerar_view_ranking,
    salvar_historico,
)

logger = logging.getLogger(__name__)


class RankingRecrutadoresTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ultima_postagem_semanal: str | None = None
        self._ultima_postagem_mensal: str | None = None
        self.loop_ranking.start()
        logger.info("🏆 RankingRecrutadoresTasks inicializado")

    def cog_unload(self):
        """Cancela o agendamento para impedir execuções após descarregar o cog."""
        self.loop_ranking.cancel()

    # ── Loop automático ──────────────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def loop_ranking(self):
        """Dispara os fechamentos semanais e mensais no minuto configurado.

        Mantém uma chave do último período publicado para que o loop, executado
        a cada minuto, não envie o mesmo ranking duas vezes. Quando chega o
        horário, a rotina publica no Discord, salva o histórico e aciona o
        fechamento financeiro correspondente.
        """
        fuso_horario = ZoneInfo(TIMEZONE_LOCAL)
        agora = datetime.now(fuso_horario)

        if agora.hour != RANKING_HORA_POST or agora.minute != 0:
            return

        # Semanal: sábado
        if agora.weekday() == 5:
            chave = agora.strftime("%Y-%m-%d")
            if self._ultima_postagem_semanal != chave:
                ok = await self._postar_automatico("semanal", agora)
                if ok:
                    self._ultima_postagem_semanal = chave

        # Mensal: dia 1
        if agora.day == RANKING_DIA_POST_MENSAL:
            chave = agora.strftime("%Y-%m")
            if self._ultima_postagem_mensal != chave:
                ok = await self._postar_automatico("mensal", agora)
                if ok:
                    self._ultima_postagem_mensal = chave

    @loop_ranking.before_loop
    async def before_loop_ranking(self):
        """Espera o bot conectar antes de permitir a tarefa automática."""
        await self.bot.wait_until_ready()
        logger.info("✅ Bot pronto — loop de ranking (semanal + mensal) ativo")

    async def _postar_automatico(self, tipo: str, referencia: datetime) -> bool:
        guild = self.bot.get_guild(int(GUILD_ID))
        if guild is None:
            logger.error("❌ Guild não encontrada para postar ranking")
            return False

        canal_id = CANAIS.get("RANKING_RECRUTADORES") or 0
        if not canal_id:
            logger.warning("⚠️ CANAIS['RANKING_RECRUTADORES'] não configurado")
            return False

        canal = guild.get_channel(canal_id)
        if canal is None:
            logger.error(f"❌ Canal de ranking {canal_id} não encontrado")
            return False

        try:
            (
                view,
                contagem,
                inicio,
                fim,
                total_rec,
                total_pago,
            ) = await gerar_view_ranking(
                tipo,
                guild=guild,
                referencia=referencia,
                modo_postagem=True,
            )
            mensagem = await canal.send(view=view)
            await salvar_historico(
                tipo=tipo,
                inicio=inicio,
                fim=fim,
                contagem=contagem,
                total_recrutamentos=total_rec,
                total_pago=total_pago,
                channel_id=canal.id,
                message_id=mensagem.id,
            )
            # Fechamento financeiro (finanças + DM controle)
            try:
                from src.financas.financas_service import processar_fechamento_ranking

                await processar_fechamento_ranking(
                    self.bot,
                    guild,
                    chave_area="recrutamento",
                    contagem=contagem,
                    inicio=inicio,
                    fim=fim,
                    total_unidades=total_rec,
                    total_pago=total_pago,
                )
            except Exception as erro_fin:
                logger.exception("Fechamento financeiro recrutamento: %s", erro_fin)

            logger.info(
                f"✅ Ranking {tipo} postado em #{canal.name} e salvo no histórico"
            )
            return True
        except Exception as erro:
            logger.exception(f"❌ Falha ao postar ranking {tipo}: {erro}")
            canal_erros = guild.get_channel(CANAIS.get("LOG_ERROS", 0) or 0)
            if canal_erros:
                # log de erro também em Components V2
                erro_view = discord.ui.LayoutView(timeout=None)
                erro_view.add_item(
                    discord.ui.Container(
                        discord.ui.TextDisplay(
                            f"# ⚠️ Erro no ranking {tipo}\n```py\n{erro}\n```"
                        ),
                        accent_color=discord.Color.red(),
                    )
                )
                await canal_erros.send(view=erro_view)
            return False


def montar_view_lista_historico_com_ids(
    registros: list,
    guild: discord.Guild | None = None,
) -> discord.ui.LayoutView:
    """Lista com ID visível para reabrir via /ranking historico id:N."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.recrutamento.ranking_service import _formatar_data_curta
    from src.utils.formatacao import formatar_reais

    if not registros:
        linhas = "_Nenhum ranking histórico encontrado._"
    else:
        blocos = []
        for registro in registros:
            periodo = (
                f"{_formatar_data_curta(registro.periodo_inicio)} → "
                f"{_formatar_data_curta(registro.periodo_fim)}"
            )
            tipo_emoji = "📅" if registro.tipo == "semanal" else "🗓️"
            link = ""
            if registro.channel_id and registro.message_id and guild:
                link = (
                    f" • "
                    f"[abrir](https://discord.com/channels/{guild.id}/{registro.channel_id}/{registro.message_id})"
                )
            blocos.append(
                f"`#{registro.id}` {tipo_emoji} **{registro.tipo.upper()}** "
                f"`{periodo}`\n"
                f"↳ 👥 **{registro.total_recrutamentos}** • 💰 "
                f"**{formatar_reais(registro.total_pago)}**{link}"
            )
        linhas = "\n\n".join(blocos)

    agora_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = (
        f"-# {guild.name} • <t:{agora_ts}:f>"
        if guild
        else f"-# Histórico • <t:{agora_ts}:f>"
    )
    rodape += "\n-# Use `/ranking historico id:<nº>` para reabrir o ranking completo."

    container = discord.ui.Container(
        discord.ui.TextDisplay("# 📜 **HISTÓRICO DE RANKINGS**"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(linhas),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(rodape),
        accent_color=discord.Color.dark_grey(),
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


async def setup(bot: commands.Bot):
    """Adiciona ao bot o cog que agenda e expõe os rankings de recrutamento."""
    await bot.add_cog(RankingRecrutadoresTasks(bot))
