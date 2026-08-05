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
from discord import app_commands
from discord.ext import commands, tasks

from src.config import (
    CANAIS,
    GUILD_ID,
    RANKING_DIA_POST_MENSAL,
    RANKING_HORA_POST,
    TIMEZONE_LOCAL,
)
from src.recrutamento.ranking_service import (
    buscar_historico_por_id,
    gerar_view_ranking,
    listar_historico,
    montar_view_historico_item,
    salvar_historico,
)
from src.utils.permissions import is_authorized

logger = logging.getLogger(__name__)


class RankingRecrutadoresTasks(commands.Cog):
    ranking_group = app_commands.Group(
        name="ranking",
        description="Ranking de recrutadores (tempo real, semanal, mensal, histórico)",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ultima_postagem_semanal: str | None = None
        self._ultima_postagem_mensal: str | None = None
        self.loop_ranking.start()
        logger.info("🏆 RankingRecrutadoresTasks inicializado")

    def cog_unload(self):
        self.loop_ranking.cancel()

    # ── Loop automático ──────────────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def loop_ranking(self):
        tz = ZoneInfo(TIMEZONE_LOCAL)
        agora = datetime.now(tz)

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
            view, contagem, inicio, fim, total_rec, total_pago = await gerar_view_ranking(
                tipo, guild=guild, referencia=referencia, modo_postagem=True,
            )
            msg = await canal.send(view=view)
            await salvar_historico(
                tipo=tipo,
                inicio=inicio,
                fim=fim,
                contagem=contagem,
                total_recrutamentos=total_rec,
                total_pago=total_pago,
                channel_id=canal.id,
                message_id=msg.id,
            )
            logger.info(f"✅ Ranking {tipo} postado em #{canal.name} e salvo no histórico")
            return True
        except Exception as e:
            logger.exception(f"❌ Falha ao postar ranking {tipo}: {e}")
            canal_erros = guild.get_channel(CANAIS.get("LOG_ERROS", 0) or 0)
            if canal_erros:
                # log de erro também em Components V2
                erro_view = discord.ui.LayoutView(timeout=None)
                erro_view.add_item(
                    discord.ui.Container(
                        discord.ui.TextDisplay(
                            f"# ⚠️ Erro no ranking {tipo}\n```py\n{e}\n```"
                        ),
                        accent_color=discord.Color.red(),
                    )
                )
                await canal_erros.send(view=erro_view)
            return False

    # ── /ranking tempo-real ──────────────────────────────────────────────

    @ranking_group.command(
        name="tempo-real",
        description="Ranking da semana atual em tempo real (parcial)",
    )
    @app_commands.describe(
        escopo="Semanal (ciclo atual) ou mensal (mês atual)",
    )
    @app_commands.choices(escopo=[
        app_commands.Choice(name="Semanal (ciclo atual)", value="semanal"),
        app_commands.Choice(name="Mensal (mês atual)", value="mensal"),
    ])
    async def ranking_tempo_real(
        self,
        interaction: discord.Interaction,
        escopo: app_commands.Choice[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        tipo_consulta = "tempo_real" if (escopo is None or escopo.value == "semanal") else "mensal"

        try:
            if tipo_consulta == "mensal":
                view, *_ = await gerar_view_ranking(
                    "mensal", guild=interaction.guild, modo_postagem=False,
                )
            else:
                view, *_ = await gerar_view_ranking(
                    "tempo_real", guild=interaction.guild, modo_postagem=False,
                )
            await interaction.followup.send(view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                f"❌ Erro ao gerar ranking: `{e}`", ephemeral=True
            )

    # ── /ranking postar ──────────────────────────────────────────────────

    @ranking_group.command(
        name="postar",
        description="Gera e posta ranking oficial (salva no histórico)",
    )
    @app_commands.describe(
        tipo="Semanal (semana que fecha) ou mensal (mês anterior)",
        no_canal="Se True, posta no canal oficial. Se False, só mostra pra você.",
    )
    @app_commands.choices(tipo=[
        app_commands.Choice(name="Semanal", value="semanal"),
        app_commands.Choice(name="Mensal", value="mensal"),
    ])
    @is_authorized()
    async def ranking_postar(
        self,
        interaction: discord.Interaction,
        tipo: app_commands.Choice[str],
        no_canal: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)
        tipo_val = tipo.value

        try:
            view, contagem, inicio, fim, total_rec, total_pago = await gerar_view_ranking(
                tipo_val, guild=interaction.guild, modo_postagem=True,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: `{e}`", ephemeral=True)
            return

        if not no_canal:
            await interaction.followup.send(view=view, ephemeral=True)
            return

        canal_id = CANAIS.get("RANKING_RECRUTADORES") or 0
        if not canal_id:
            await interaction.followup.send(
                "❌ `CANAIS['RANKING_RECRUTADORES']` não configurado no config.",
                ephemeral=True,
            )
            return

        canal = interaction.guild.get_channel(canal_id) if interaction.guild else None
        if canal is None:
            await interaction.followup.send(
                f"❌ Canal `{canal_id}` não encontrado.", ephemeral=True
            )
            return

        msg = await canal.send(view=view)
        await salvar_historico(
            tipo=tipo_val,
            inicio=inicio,
            fim=fim,
            contagem=contagem,
            total_recrutamentos=total_rec,
            total_pago=total_pago,
            channel_id=canal.id,
            message_id=msg.id,
        )
        await interaction.followup.send(
            f"✅ Ranking **{tipo_val}** postado em {canal.mention} e salvo no histórico.",
            ephemeral=True,
        )

    # ── /ranking historico ───────────────────────────────────────────────

    @ranking_group.command(
        name="historico",
        description="Lista os últimos rankings salvos ou reabre um por ID",
    )
    @app_commands.describe(
        tipo="Filtrar por tipo (opcional)",
        limite="Quantidade de registros na lista (1–25)",
        id="ID do histórico para reabrir o ranking completo",
    )
    @app_commands.choices(tipo=[
        app_commands.Choice(name="Todos", value="todos"),
        app_commands.Choice(name="Semanal", value="semanal"),
        app_commands.Choice(name="Mensal", value="mensal"),
    ])
    async def ranking_historico(
        self,
        interaction: discord.Interaction,
        tipo: app_commands.Choice[str] = None,
        limite: app_commands.Range[int, 1, 25] = 10,
        id: int | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            if id is not None:
                registro = await buscar_historico_por_id(id)
                if registro is None:
                    await interaction.followup.send(
                        f"❌ Histórico `#{id}` não encontrado.", ephemeral=True
                    )
                    return
                view = montar_view_historico_item(registro, guild=interaction.guild)
                await interaction.followup.send(view=view, ephemeral=True)
                return

            filtro = None if (tipo is None or tipo.value == "todos") else tipo.value
            registros = await listar_historico(tipo=filtro, limite=limite)

            view = montar_view_lista_historico_com_ids(registros, interaction.guild)
            await interaction.followup.send(view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: `{e}`", ephemeral=True)


def montar_view_lista_historico_com_ids(
    registros: list,
    guild: discord.Guild | None = None,
) -> discord.ui.LayoutView:
    """Lista com ID visível para reabrir via /ranking historico id:N."""
    from src.recrutamento.ranking_service import _formatar_data_curta
    from src.utils.formatacao import formatar_reais
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if not registros:
        linhas = "_Nenhum ranking histórico encontrado._"
    else:
        blocos = []
        for r in registros:
            periodo = f"{_formatar_data_curta(r.periodo_inicio)} → {_formatar_data_curta(r.periodo_fim)}"
            tipo_emoji = "📅" if r.tipo == "semanal" else "🗓️"
            link = ""
            if r.channel_id and r.message_id and guild:
                link = f" • [abrir](https://discord.com/channels/{guild.id}/{r.channel_id}/{r.message_id})"
            blocos.append(
                f"`#{r.id}` {tipo_emoji} **{r.tipo.upper()}** `{periodo}`\n"
                f"↳ 👥 **{r.total_recrutamentos}** • 💰 **{formatar_reais(r.total_pago)}**{link}"
            )
        linhas = "\n\n".join(blocos)

    agora_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = f"-# {guild.name} • <t:{agora_ts}:f>" if guild else f"-# Histórico • <t:{agora_ts}:f>"
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
    await bot.add_cog(RankingRecrutadoresTasks(bot))
