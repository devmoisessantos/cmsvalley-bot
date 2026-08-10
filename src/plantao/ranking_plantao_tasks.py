"""Tasks e comandos: ranking de chamadas + ranking de horas de plantão.

Auto: sábado 11h (semanal) e dia 1 às 11h (mensal) — mesmos horários dos demais rankings.
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
from src.plantao.ranking_plantao_service import (
    gerar_view_ranking_chamadas,
    gerar_view_ranking_horas,
    listar_historico_plantao,
    salvar_historico_plantao,
)
from src.utils.formatacao import formatar_hms
from src.utils.permissions import is_authorized

logger = logging.getLogger(__name__)


class RankingPlantaoTasks(commands.Cog):
    ranking_chamadas = app_commands.Group(
        name="ranking-chamadas",
        description="Ranking de chamadas realizadas pelos doutores",
    )
    ranking_horas = app_commands.Group(
        name="ranking-horas",
        description="Ranking de horas de plantão (relatório, sem premiação)",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._post_semanal: set[str] = set()
        self._post_mensal: set[str] = set()
        self.loop_rankings.start()
        logger.info("🏆 RankingPlantaoTasks (chamadas + horas) inicializado")

    def cog_unload(self):
        self.loop_rankings.cancel()

    # ── Auto post ────────────────────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def loop_rankings(self):
        tz = ZoneInfo(TIMEZONE_LOCAL)
        agora = datetime.now(tz)
        if agora.hour != RANKING_HORA_POST or agora.minute != 0:
            return

        if agora.weekday() == 5:
            chave = f"semanal:{agora.strftime('%Y-%m-%d')}"
            if chave not in self._post_semanal:
                ok_c = await self._postar("chamada", "semanal", agora)
                ok_h = await self._postar("horas", "semanal", agora)
                if ok_c or ok_h:
                    self._post_semanal.add(chave)

        if agora.day == RANKING_DIA_POST_MENSAL:
            chave = f"mensal:{agora.strftime('%Y-%m')}"
            if chave not in self._post_mensal:
                ok_c = await self._postar("chamada", "mensal", agora)
                ok_h = await self._postar("horas", "mensal", agora)
                if ok_c or ok_h:
                    self._post_mensal.add(chave)

    @loop_rankings.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()
        logger.info("✅ Loop ranking chamadas/horas ativo")

    async def _postar(
        self,
        categoria: str,
        periodo: str,
        referencia: datetime,
    ) -> bool:
        guild = self.bot.get_guild(int(GUILD_ID))
        if guild is None:
            return False

        if categoria == "chamada":
            canal_key = "RANKING_CHAMADAS"
            tipo_hist = f"chamada_{periodo}"
            gerador = gerar_view_ranking_chamadas
        else:
            canal_key = "RANKING_HORAS_PLANTAO"
            tipo_hist = f"horas_{periodo}"
            gerador = gerar_view_ranking_horas

        canal_id = CANAIS.get(canal_key) or 0
        if not canal_id:
            logger.warning(f"⚠️ CANAIS['{canal_key}'] não configurado")
            return False
        canal = guild.get_channel(canal_id)
        if canal is None:
            logger.error(f"❌ Canal {canal_key}={canal_id} não encontrado")
            return False

        try:
            view, contagem, inicio, fim, total = await gerador(
                periodo, guild=guild, referencia=referencia, modo_postagem=True
            )
            msg = await canal.send(view=view)
            await salvar_historico_plantao(
                tipo=tipo_hist,
                inicio=inicio,
                fim=fim,
                contagem=contagem,
                total=total,
                channel_id=canal.id,
                message_id=msg.id,
            )
            # Só chamadas geram pagamento unitário (horas = moedas no bot)
            if categoria == "chamada":
                try:
                    from src.config import VALOR_UNITARIO_RANKING
                    from src.financas.financas_service import (
                        processar_fechamento_ranking,
                    )

                    await processar_fechamento_ranking(
                        self.bot,
                        guild,
                        chave_area="chamadas",
                        contagem=contagem,
                        inicio=inicio,
                        fim=fim,
                        total_unidades=total,
                        total_pago=total * VALOR_UNITARIO_RANKING,
                    )
                except Exception as erro_fin:
                    logger.exception("Fechamento financeiro chamadas: %s", erro_fin)

            logger.info(f"✅ Ranking {tipo_hist} postado em #{canal.name}")
            return True
        except Exception as e:
            logger.exception(f"❌ Ranking {categoria}/{periodo}: {e}")
            return False

    # ── /ranking-chamadas ────────────────────────────────────────────────

    @ranking_chamadas.command(
        name="tempo-real", description="Ranking parcial do ciclo atual"
    )
    async def chamadas_tempo_real(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            view, *_ = await gerar_view_ranking_chamadas(
                "tempo_real", guild=interaction.guild, modo_postagem=False
            )
            await interaction.followup.send(view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: `{e}`", ephemeral=True)

    @ranking_chamadas.command(
        name="postar", description="Gera ranking oficial e opcionalmente posta"
    )
    @app_commands.describe(
        periodo="Semanal (semana que fecha) ou mensal (mês anterior)",
        no_canal="Postar no canal oficial de ranking de chamadas",
    )
    @app_commands.choices(
        periodo=[
            app_commands.Choice(name="Semanal", value="semanal"),
            app_commands.Choice(name="Mensal", value="mensal"),
        ]
    )
    @is_authorized()
    async def chamadas_postar(
        self,
        interaction: discord.Interaction,
        periodo: app_commands.Choice[str],
        no_canal: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            view, contagem, inicio, fim, total = await gerar_view_ranking_chamadas(
                periodo.value, guild=interaction.guild, modo_postagem=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: `{e}`", ephemeral=True)
            return

        if not no_canal:
            await interaction.followup.send(view=view, ephemeral=True)
            return

        canal_id = CANAIS.get("RANKING_CHAMADAS") or 0
        canal = (
            interaction.guild.get_channel(canal_id)
            if interaction.guild and canal_id
            else None
        )
        if canal is None:
            await interaction.followup.send(
                "❌ `CANAIS['RANKING_CHAMADAS']` não configurado ou canal inválido.",
                ephemeral=True,
            )
            return
        msg = await canal.send(view=view)
        await salvar_historico_plantao(
            tipo=f"chamada_{periodo.value}",
            inicio=inicio,
            fim=fim,
            contagem=contagem,
            total=total,
            channel_id=canal.id,
            message_id=msg.id,
        )
        await interaction.followup.send(
            f"✅ Ranking de chamadas **{periodo.value}** postado em {canal.mention}.",
            ephemeral=True,
        )

    @ranking_chamadas.command(
        name="historico", description="Últimos rankings de chamadas salvos"
    )
    @app_commands.describe(limite="Quantidade (1–25)")
    async def chamadas_historico(
        self,
        interaction: discord.Interaction,
        limite: app_commands.Range[int, 1, 25] = 10,
    ):
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.NotFound:
                return
            except discord.HTTPException:
                return
        try:
            regs = await listar_historico_plantao("chamada", limite=limite)
        except Exception as erro_db:
            from src.database.connection import reiniciar_pool_se_preciso

            try:
                await reiniciar_pool_se_preciso()
            except Exception:
                pass
            await interaction.followup.send(
                f"❌ Banco indisponível no momento (`{type(erro_db).__name__}`). "
                "Tente novamente em alguns segundos.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            view=_lista_historico(
                regs, interaction.guild, titulo="🩺 HISTÓRICO — CHAMADAS"
            ),
            ephemeral=True,
        )

    # ── /ranking-horas ───────────────────────────────────────────────────

    @ranking_horas.command(
        name="tempo-real", description="Horas de plantão no ciclo atual (relatório)"
    )
    async def horas_tempo_real(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            view, *_ = await gerar_view_ranking_horas(
                "tempo_real", guild=interaction.guild, modo_postagem=False
            )
            await interaction.followup.send(view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: `{e}`", ephemeral=True)

    @ranking_horas.command(
        name="postar", description="Gera ranking oficial de horas (relatório)"
    )
    @app_commands.describe(
        periodo="Semanal ou mensal",
        no_canal="Postar no canal oficial de ranking de horas",
    )
    @app_commands.choices(
        periodo=[
            app_commands.Choice(name="Semanal", value="semanal"),
            app_commands.Choice(name="Mensal", value="mensal"),
        ]
    )
    @is_authorized()
    async def horas_postar(
        self,
        interaction: discord.Interaction,
        periodo: app_commands.Choice[str],
        no_canal: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            view, contagem, inicio, fim, total = await gerar_view_ranking_horas(
                periodo.value, guild=interaction.guild, modo_postagem=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: `{e}`", ephemeral=True)
            return

        if not no_canal:
            await interaction.followup.send(view=view, ephemeral=True)
            return

        canal_id = CANAIS.get("RANKING_HORAS_PLANTAO") or 0
        canal = (
            interaction.guild.get_channel(canal_id)
            if interaction.guild and canal_id
            else None
        )
        if canal is None:
            await interaction.followup.send(
                "❌ `CANAIS['RANKING_HORAS_PLANTAO']` não configurado ou canal inválido.",
                ephemeral=True,
            )
            return
        msg = await canal.send(view=view)
        await salvar_historico_plantao(
            tipo=f"horas_{periodo.value}",
            inicio=inicio,
            fim=fim,
            contagem=contagem,
            total=total,
            channel_id=canal.id,
            message_id=msg.id,
        )
        await interaction.followup.send(
            f"✅ Ranking de horas **{periodo.value}** postado em {canal.mention} "
            f"(relatório · total `{formatar_hms(total)}`).",
            ephemeral=True,
        )

    @ranking_horas.command(
        name="historico", description="Últimos rankings de horas salvos"
    )
    @app_commands.describe(limite="Quantidade (1–25)")
    async def horas_historico(
        self,
        interaction: discord.Interaction,
        limite: app_commands.Range[int, 1, 25] = 10,
    ):
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.NotFound:
                return
            except discord.HTTPException:
                return
        try:
            regs = await listar_historico_plantao("horas", limite=limite)
        except Exception as erro_db:
            from src.database.connection import reiniciar_pool_se_preciso

            try:
                await reiniciar_pool_se_preciso()
            except Exception:
                pass
            await interaction.followup.send(
                f"❌ Banco indisponível no momento (`{type(erro_db).__name__}`). "
                "Tente novamente em alguns segundos.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            view=_lista_historico(
                regs,
                interaction.guild,
                titulo="⏱️ HISTÓRICO — HORAS DE PLANTÃO",
                horas=True,
            ),
            ephemeral=True,
        )


def _lista_historico(
    registros,
    guild: discord.Guild | None,
    *,
    titulo: str,
    horas: bool = False,
) -> discord.ui.LayoutView:
    from src.plantao.ranking_plantao_service import _formatar_data_curta

    if not registros:
        linhas = "_Nenhum ranking histórico encontrado._"
    else:
        blocos = []
        for r in registros:
            periodo = f"{_formatar_data_curta(r.periodo_inicio)} → {_formatar_data_curta(r.periodo_fim)}"
            if horas:
                metrica = f"⏱️ **{formatar_hms(r.total_recrutamentos)}**"
            else:
                metrica = f"🩺 **{r.total_recrutamentos}** chamadas"
            link = ""
            if r.channel_id and r.message_id and guild:
                link = f" • [abrir](https://discord.com/channels/{guild.id}/{r.channel_id}/{r.message_id})"
            blocos.append(f"`#{r.id}` **{r.tipo}** `{periodo}`\n↳ {metrica}{link}")
        linhas = "\n\n".join(blocos)

    agora_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = (
        f"-# {guild.name} • <t:{agora_ts}:f>"
        if guild
        else f"-# Histórico • <t:{agora_ts}:f>"
    )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {titulo}"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(linhas),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(rodape),
            accent_color=discord.Color.dark_grey(),
        )
    )
    return view


async def setup(bot: commands.Bot):
    await bot.add_cog(RankingPlantaoTasks(bot))
