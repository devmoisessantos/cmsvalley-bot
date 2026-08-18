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
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
    responder_view,
)
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
        """Mostra privadamente o ranking parcial semanal ou mensal solicitado.

        Adia a resposta para acomodar a consulta ao banco e trata erros de
        geração sem expor uma exceção bruta ao membro que executou o comando.
        """
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
            await responder_view(
                interacao,
                view,
                ephemeral=True,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=[
                    f"Erro ao gerar ranking: `{type(erro).__name__}: {erro}`",
                ],
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
        """Gera o ranking oficial e, opcionalmente, publica-o no canal do domínio.

        A prévia permanece privada quando `no_canal` é falso. Ao publicar,
        envia o card ao Discord e grava seus identificadores e a contagem no
        histórico, preservando uma referência administrativa da edição.
        """
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
            await responder_erro(
                interacao,
                titulo="Falha inesperada",
                linhas=[
                    f"Erro: `{type(erro).__name__}: {erro}`",
                ],
            )
            return

        if not no_canal:
            await responder_view(
                interacao,
                view,
                ephemeral=True,
            )
            return

        canal_id = CANAIS.get("CANAL_RANKING_LAUDOS") or 0
        canal = (
            interacao.guild.get_channel(canal_id)
            if interacao.guild and canal_id
            else None
        )
        if canal is None:
            await responder_erro(
                interacao,
                titulo="Dado inválido",
                linhas=[
                    "`CANAL_RANKING_LAUDOS` não configurado ou canal inválido.",
                ],
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
        await responder_sucesso(
            interacao,
            titulo="Ranking postado",
            linhas=[
                f"Ranking de laudos **{periodo.value}** postado em {canal.mention} "
                f"(total **{total}** laudos).",
            ],
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
        """Exibe privadamente os últimos rankings de laudos já persistidos.

        O limite tipado pelo comando impede consultas excessivas e cada linha
        mostra a data local, o tipo e o total, facilitando auditorias rápidas.
        """
        if not interacao.response.is_done():
            try:
                await interacao.response.defer(ephemeral=True)
            except discord.NotFound:
                return

        try:
            registros = await listar_historico_laudos(limite=limite)
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Banco de dados indisponível",
                linhas=[
                    f"Banco indisponível (`{type(erro).__name__}`).",
                ],
            )
            return

        if not registros:
            await responder_aviso(
                interacao,
                titulo="Nada para mostrar",
                linhas=[
                    "_Nenhum ranking de laudos salvo ainda._",
                ],
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
        await responder_view(
            interacao,
            layout,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    """Registra o cog responsável pelos rankings e suas tarefas agendadas."""
    await bot.add_cog(RankingLaudosTasks(bot))
