"""Tasks e comandos de ranking de laudos psicológicos.

- Auto: semanal (sábado 11h) e mensal (dia 1 às 11h)
- Canal: CANAL_RANKING_LAUDOS
- Comandos: /ranking-laudos tempo-real | postar | historico
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
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
    listar_historico_laudos,
    salvar_historico_laudos,
)
from src.utils.formatacao import formatar_data_hora_local
from src.utils.permissions import is_authorized

logger = logging.getLogger(__name__)


class RankingLaudosTasks(commands.Cog):
    grupo_ranking_laudos = app_commands.Group(
        name="ranking-laudos",
        description="Ranking de laudos psicológicos",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ultima_postagem_semanal: str | None = None
        self._ultima_postagem_mensal: str | None = None
        self.loop_ranking_laudos.start()
        logger.info("🧠 RankingLaudosTasks inicializado")

    def cog_unload(self):
        self.loop_ranking_laudos.cancel()

    @tasks.loop(minutes=1)
    async def loop_ranking_laudos(self):
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

    @grupo_ranking_laudos.command(
        name="tempo-real",
        description="Ranking de laudos do ciclo atual (parcial)",
    )
    @app_commands.describe(escopo="Semanal (ciclo atual) ou mensal (mês atual)")
    @app_commands.choices(
        escopo=[
            app_commands.Choice(name="Semanal (ciclo atual)", value="semanal"),
            app_commands.Choice(name="Mensal (mês atual)", value="mensal"),
        ]
    )
    async def tempo_real(
        self,
        interacao: discord.Interaction,
        escopo: app_commands.Choice[str] | None = None,
    ):
        if not interacao.response.is_done():
            try:
                await interacao.response.defer(ephemeral=True)
            except discord.NotFound:
                return

        periodo = "tempo_real"
        if escopo is not None and escopo.value == "mensal":
            periodo = "mensal"

        try:
            view, *_ = await gerar_view_ranking_laudos(
                periodo,
                guild=interacao.guild,
                modo_postagem=False,
            )
            await interacao.followup.send(view=view, ephemeral=True)
        except Exception as erro:
            await interacao.followup.send(
                f"❌ Erro ao gerar ranking: `{type(erro).__name__}: {erro}`",
                ephemeral=True,
            )

    @grupo_ranking_laudos.command(
        name="postar",
        description="Gera ranking oficial de laudos (salva no histórico)",
    )
    @app_commands.describe(
        periodo="Semanal ou mensal",
        no_canal="Postar no canal oficial de ranking de laudos",
    )
    @app_commands.choices(
        periodo=[
            app_commands.Choice(name="Semanal", value="semanal"),
            app_commands.Choice(name="Mensal", value="mensal"),
        ]
    )
    @is_authorized()
    async def postar(
        self,
        interacao: discord.Interaction,
        periodo: app_commands.Choice[str],
        no_canal: bool = False,
    ):
        if not interacao.response.is_done():
            try:
                await interacao.response.defer(ephemeral=True)
            except discord.NotFound:
                return

        try:
            view, contagem, inicio, fim, total = await gerar_view_ranking_laudos(
                periodo.value,
                guild=interacao.guild,
                modo_postagem=True,
            )
        except Exception as erro:
            await interacao.followup.send(
                f"❌ Erro: `{type(erro).__name__}: {erro}`",
                ephemeral=True,
            )
            return

        if not no_canal:
            await interacao.followup.send(view=view, ephemeral=True)
            return

        canal_id = CANAIS.get("CANAL_RANKING_LAUDOS") or 0
        canal = (
            interacao.guild.get_channel(canal_id)
            if interacao.guild and canal_id
            else None
        )
        if canal is None:
            await interacao.followup.send(
                "❌ `CANAL_RANKING_LAUDOS` não configurado ou canal inválido.",
                ephemeral=True,
            )
            return

        mensagem = await canal.send(view=view)
        await salvar_historico_laudos(
            tipo=f"laudos_{periodo.value}",
            inicio=inicio,
            fim=fim,
            contagem=contagem,
            total=total,
            channel_id=canal.id,
            message_id=mensagem.id,
        )
        await interacao.followup.send(
            f"✅ Ranking de laudos **{periodo.value}** postado em {canal.mention} "
            f"(total **{total}** laudos).",
            ephemeral=True,
        )

    @grupo_ranking_laudos.command(
        name="historico",
        description="Últimos rankings de laudos salvos",
    )
    @app_commands.describe(limite="Quantidade (1–25)")
    async def historico(
        self,
        interacao: discord.Interaction,
        limite: app_commands.Range[int, 1, 25] = 10,
    ):
        if not interacao.response.is_done():
            try:
                await interacao.response.defer(ephemeral=True)
            except discord.NotFound:
                return

        try:
            registros = await listar_historico_laudos(limite=limite)
        except Exception as erro:
            await interacao.followup.send(
                f"❌ Banco indisponível (`{type(erro).__name__}`).",
                ephemeral=True,
            )
            return

        if not registros:
            await interacao.followup.send(
                "_Nenhum ranking de laudos salvo ainda._",
                ephemeral=True,
            )
            return

        linhas = []
        for registro in registros:
            data = formatar_data_hora_local(registro.criado_em)
            linhas.append(
                f"• **#{registro.id}** · `{registro.tipo}` · "
                f"**{registro.total_recrutamentos}** laudos · {data}"
            )

        layout = discord.ui.LayoutView(timeout=120)
        layout.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# 🧠 HISTÓRICO — LAUDOS\n" + "\n".join(linhas)),
                accent_color=discord.Color.blurple(),
            )
        )
        await interacao.followup.send(view=layout, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RankingLaudosTasks(bot))
