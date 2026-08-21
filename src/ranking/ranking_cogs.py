"""
Grupo único /ranking — todos os rankings do hospital.

Subcomandos (nomes com prefixo da área):
  horas-* · chamadas-* · recrutamento-* · laudos-* · moedas-*

Padrão tempo real (igual ranking de moedas):
  - parâmetro no_canal (False = ephemeral só pra você; True = posta no canal)
  - rodapé ``atualizado em tempo real`` montado nos services de cada área
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.config import CANAIS
from src.database.conexao import tentar_reanimar_as_conexoes
from src.laudos.ranking_laudos_service import (
    gerar_view_ranking_laudos,
    listar_historico_laudos,
    salvar_historico_laudos,
)
from src.plantao.carteira_ranking_service import montar_view_ranking_moedas
from src.plantao.ranking_plantao_service import (
    gerar_view_ranking_chamadas,
    gerar_view_ranking_horas,
    listar_historico_plantao,
    salvar_historico_plantao,
)
from src.recrutamento.ranking_service import (
    gerar_view_ranking,
    listar_historico,
    salvar_historico,
)
from src.utils.formatacao import formatar_hms
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
    responder_view,
)
from src.utils.permissions import is_authorized

registrador = logging.getLogger(__name__)


def _canal_por_chave(
    guilda: discord.Guild | None,
    chave: str,
) -> discord.TextChannel | None:
    """Resolve canal de ranking pelo nome da chave em CANAIS."""
    if guilda is None:
        return None
    canal_id = CANAIS.get(chave) or 0
    if not canal_id:
        return None
    canal = guilda.get_channel(int(canal_id))
    if isinstance(canal, discord.TextChannel):
        return canal
    return None


class RankingCog(commands.Cog):
    """Único dono do grupo /ranking na árvore de comandos."""

    grupo_ranking = app_commands.Group(
        name="ranking",
        description=(
            "Rankings do hospital (horas, chamadas, recrutamento, laudos, moedas)"
        ),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Horas ────────────────────────────────────────────────────────────

    @grupo_ranking.command(
        name="horas-tempo-real",
        description="Horas de plantão no ciclo atual (parcial / ao vivo)",
    )
    @app_commands.describe(
        no_canal=(
            "True = posta no canal oficial. False = só você vê (ephemeral)."
        ),
    )
    async def horas_tempo_real(
        self,
        interacao: discord.Interaction,
        no_canal: bool = False,
    ):
        """Prévia ou postagem do ranking de horas em tempo real."""
        await interacao.response.defer(ephemeral=True)
        try:
            view, *_ = await gerar_view_ranking_horas(
                "tempo_real",
                guild=interacao.guild,
                modo_postagem=False,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha inesperada",
                linhas=[f"Erro: `{erro}`"],
            )
            return
        await self._entregar_view(
            interacao,
            view,
            no_canal=no_canal,
            chave_canal="RANKING_HORAS_PLANTAO",
            titulo_ok="Ranking de horas (tempo real)",
        )

    @grupo_ranking.command(
        name="horas-postar",
        description="Gera ranking oficial de horas (semanal/mensal)",
    )
    @app_commands.describe(
        periodo="Semanal ou mensal",
        no_canal="True = posta no canal oficial. False = só você vê.",
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
        interacao: discord.Interaction,
        periodo: app_commands.Choice[str],
        no_canal: bool = False,
    ):
        """Fecha e opcionalmente publica o ranking oficial de horas."""
        await interacao.response.defer(ephemeral=True)
        try:
            view, contagem, inicio, fim, total = await gerar_view_ranking_horas(
                periodo.value,
                guild=interacao.guild,
                modo_postagem=True,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha inesperada",
                linhas=[f"Erro: `{erro}`"],
            )
            return

        mensagem = await self._entregar_view(
            interacao,
            view,
            no_canal=no_canal,
            chave_canal="RANKING_HORAS_PLANTAO",
            titulo_ok=f"Ranking de horas ({periodo.value})",
            linhas_ok=[
                f"Total `{formatar_hms(total)}`.",
            ],
        )
        if mensagem is not None:
            await salvar_historico_plantao(
                tipo=f"horas_{periodo.value}",
                inicio=inicio,
                fim=fim,
                contagem=contagem,
                total=total,
                channel_id=mensagem.channel.id,
                message_id=mensagem.id,
            )

    @grupo_ranking.command(
        name="horas-historico",
        description="Últimos rankings de horas salvos",
    )
    @app_commands.describe(limite="Quantidade (1–25)")
    async def horas_historico(
        self,
        interacao: discord.Interaction,
        limite: app_commands.Range[int, 1, 25] = 10,
    ):
        """Lista histórico de horas no ephemeral."""
        await interacao.response.defer(ephemeral=True)
        try:
            from src.plantao.ranking_plantao_tasks import _lista_historico

            registros = await listar_historico_plantao("horas", limite=limite)
            await responder_view(
                interacao,
                _lista_historico(
                    registros,
                    interacao.guild,
                    titulo="⏱️ HISTÓRICO — HORAS",
                ),
                ephemeral=True,
            )
        except Exception as erro_db:
            await tentar_reanimar_as_conexoes(
                contexto="listar o historico de horas",
            )
            await responder_erro(
                interacao,
                titulo="Banco de dados indisponível",
                linhas=[f"`{type(erro_db).__name__}` — tente de novo em instantes."],
            )

    # ── Chamadas ─────────────────────────────────────────────────────────

    @grupo_ranking.command(
        name="chamadas-tempo-real",
        description="Ranking parcial de chamadas do ciclo atual",
    )
    @app_commands.describe(
        no_canal=(
            "True = posta no canal oficial. False = só você vê (ephemeral)."
        ),
    )
    async def chamadas_tempo_real(
        self,
        interacao: discord.Interaction,
        no_canal: bool = False,
    ):
        """Prévia ou postagem do ranking de chamadas em tempo real."""
        await interacao.response.defer(ephemeral=True)
        try:
            view, *_ = await gerar_view_ranking_chamadas(
                "tempo_real",
                guild=interacao.guild,
                modo_postagem=False,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha inesperada",
                linhas=[f"Erro: `{erro}`"],
            )
            return
        await self._entregar_view(
            interacao,
            view,
            no_canal=no_canal,
            chave_canal="RANKING_CHAMADAS",
            titulo_ok="Ranking de chamadas (tempo real)",
        )

    @grupo_ranking.command(
        name="chamadas-postar",
        description="Gera ranking oficial de chamadas",
    )
    @app_commands.describe(
        periodo="Semanal ou mensal",
        no_canal="True = posta no canal oficial. False = só você vê.",
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
        interacao: discord.Interaction,
        periodo: app_commands.Choice[str],
        no_canal: bool = False,
    ):
        """Fecha e opcionalmente publica o ranking oficial de chamadas."""
        await interacao.response.defer(ephemeral=True)
        try:
            view, contagem, inicio, fim, total = await gerar_view_ranking_chamadas(
                periodo.value,
                guild=interacao.guild,
                modo_postagem=True,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha inesperada",
                linhas=[f"Erro: `{erro}`"],
            )
            return

        mensagem = await self._entregar_view(
            interacao,
            view,
            no_canal=no_canal,
            chave_canal="RANKING_CHAMADAS",
            titulo_ok=f"Ranking de chamadas ({periodo.value})",
        )
        if mensagem is not None:
            await salvar_historico_plantao(
                tipo=f"chamada_{periodo.value}",
                inicio=inicio,
                fim=fim,
                contagem=contagem,
                total=total,
                channel_id=mensagem.channel.id,
                message_id=mensagem.id,
            )

    @grupo_ranking.command(
        name="chamadas-historico",
        description="Últimos rankings de chamadas salvos",
    )
    @app_commands.describe(limite="Quantidade (1–25)")
    async def chamadas_historico(
        self,
        interacao: discord.Interaction,
        limite: app_commands.Range[int, 1, 25] = 10,
    ):
        """Lista histórico de chamadas no ephemeral."""
        await interacao.response.defer(ephemeral=True)
        try:
            from src.plantao.ranking_plantao_tasks import _lista_historico

            registros = await listar_historico_plantao("chamada", limite=limite)
            await responder_view(
                interacao,
                _lista_historico(
                    registros,
                    interacao.guild,
                    titulo="🩺 HISTÓRICO — CHAMADAS",
                ),
                ephemeral=True,
            )
        except Exception as erro_db:
            await tentar_reanimar_as_conexoes(
                contexto="listar o historico de chamadas",
            )
            await responder_erro(
                interacao,
                titulo="Banco de dados indisponível",
                linhas=[f"`{type(erro_db).__name__}` — tente de novo em instantes."],
            )

    # ── Recrutamento ─────────────────────────────────────────────────────

    @grupo_ranking.command(
        name="recrutamento-tempo-real",
        description="Ranking de recrutadores do ciclo atual (parcial)",
    )
    @app_commands.describe(
        escopo="Semanal (ciclo atual) ou mensal (mês atual)",
        no_canal=(
            "True = posta no canal oficial. False = só você vê (ephemeral)."
        ),
    )
    @app_commands.choices(
        escopo=[
            app_commands.Choice(name="Semanal (ciclo atual)", value="semanal"),
            app_commands.Choice(name="Mensal (mês atual)", value="mensal"),
        ]
    )
    async def recrutamento_tempo_real(
        self,
        interacao: discord.Interaction,
        escopo: app_commands.Choice[str] | None = None,
        no_canal: bool = False,
    ):
        """Prévia ou postagem do ranking de recrutamento em tempo real."""
        await interacao.response.defer(ephemeral=True)
        tipo = (
            "tempo_real"
            if (escopo is None or escopo.value == "semanal")
            else "mensal"
        )
        try:
            view, *_ = await gerar_view_ranking(
                tipo,
                guild=interacao.guild,
                modo_postagem=False,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=[f"Erro ao gerar ranking: `{erro}`"],
            )
            return
        await self._entregar_view(
            interacao,
            view,
            no_canal=no_canal,
            chave_canal="RANKING_RECRUTADORES",
            titulo_ok="Ranking de recrutamento (tempo real)",
        )

    @grupo_ranking.command(
        name="recrutamento-postar",
        description="Gera ranking oficial de recrutadores",
    )
    @app_commands.describe(
        tipo="Semanal ou mensal",
        no_canal="True = posta no canal oficial. False = só você vê.",
    )
    @app_commands.choices(
        tipo=[
            app_commands.Choice(name="Semanal", value="semanal"),
            app_commands.Choice(name="Mensal", value="mensal"),
        ]
    )
    @is_authorized()
    async def recrutamento_postar(
        self,
        interacao: discord.Interaction,
        tipo: app_commands.Choice[str],
        no_canal: bool = False,
    ):
        """Fecha e opcionalmente publica o ranking oficial de recrutamento."""
        await interacao.response.defer(ephemeral=True)
        try:
            (
                view,
                contagem,
                inicio,
                fim,
                total_rec,
                total_pago,
            ) = await gerar_view_ranking(
                tipo.value,
                guild=interacao.guild,
                modo_postagem=True,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=[f"Erro: `{erro}`"],
            )
            return

        mensagem = await self._entregar_view(
            interacao,
            view,
            no_canal=no_canal,
            chave_canal="RANKING_RECRUTADORES",
            titulo_ok=f"Ranking de recrutamento ({tipo.value})",
            linhas_ok=[f"Total **{total_rec}** recrutamentos."],
        )
        if mensagem is not None:
            await salvar_historico(
                tipo=tipo.value,
                inicio=inicio,
                fim=fim,
                contagem=contagem,
                total_recrutamentos=total_rec,
                total_pago=total_pago,
                channel_id=mensagem.channel.id,
                message_id=mensagem.id,
            )

    @grupo_ranking.command(
        name="recrutamento-historico",
        description="Últimos rankings de recrutamento salvos",
    )
    @app_commands.describe(
        tipo="Filtrar por tipo",
        limite="Quantidade (1–25)",
    )
    @app_commands.choices(
        tipo=[
            app_commands.Choice(name="Todos", value="todos"),
            app_commands.Choice(name="Semanal", value="semanal"),
            app_commands.Choice(name="Mensal", value="mensal"),
        ]
    )
    async def recrutamento_historico(
        self,
        interacao: discord.Interaction,
        tipo: app_commands.Choice[str] | None = None,
        limite: app_commands.Range[int, 1, 25] = 10,
    ):
        """Lista histórico de recrutamento (ephemeral)."""
        await interacao.response.defer(ephemeral=True)
        filtro = None if (tipo is None or tipo.value == "todos") else tipo.value
        try:
            from src.recrutamento.ranking_tasks import (
                montar_view_lista_historico_com_ids,
            )

            registros = await listar_historico(tipo=filtro, limite=limite)
            await responder_view(
                interacao,
                montar_view_lista_historico_com_ids(
                    registros, interacao.guild
                ),
                ephemeral=True,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Erro ao listar histórico",
                linhas=[f"`{erro}`"],
            )

    # ── Laudos ───────────────────────────────────────────────────────────

    @grupo_ranking.command(
        name="laudos-tempo-real",
        description="Ranking de laudos do ciclo atual (parcial)",
    )
    @app_commands.describe(
        escopo="Semanal (ciclo atual) ou mensal (mês atual)",
        no_canal=(
            "True = posta no canal oficial. False = só você vê (ephemeral)."
        ),
    )
    @app_commands.choices(
        escopo=[
            app_commands.Choice(name="Semanal (ciclo atual)", value="semanal"),
            app_commands.Choice(name="Mensal (mês atual)", value="mensal"),
        ]
    )
    async def laudos_tempo_real(
        self,
        interacao: discord.Interaction,
        escopo: app_commands.Choice[str] | None = None,
        no_canal: bool = False,
    ):
        """Prévia ou postagem do ranking de laudos em tempo real."""
        await interacao.response.defer(ephemeral=True)
        periodo = "tempo_real"
        if escopo is not None and escopo.value == "mensal":
            periodo = "mensal"
        try:
            view, *_ = await gerar_view_ranking_laudos(
                periodo,
                guild=interacao.guild,
                modo_postagem=False,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=[f"`{type(erro).__name__}: {erro}`"],
            )
            return
        await self._entregar_view(
            interacao,
            view,
            no_canal=no_canal,
            chave_canal="CANAL_RANKING_LAUDOS",
            titulo_ok="Ranking de laudos (tempo real)",
        )

    @grupo_ranking.command(
        name="laudos-postar",
        description="Gera ranking oficial de laudos",
    )
    @app_commands.describe(
        periodo="Semanal ou mensal",
        no_canal="True = posta no canal oficial. False = só você vê.",
    )
    @app_commands.choices(
        periodo=[
            app_commands.Choice(name="Semanal", value="semanal"),
            app_commands.Choice(name="Mensal", value="mensal"),
        ]
    )
    @is_authorized()
    async def laudos_postar(
        self,
        interacao: discord.Interaction,
        periodo: app_commands.Choice[str],
        no_canal: bool = False,
    ):
        """Fecha e opcionalmente publica o ranking oficial de laudos."""
        await interacao.response.defer(ephemeral=True)
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
                linhas=[f"`{type(erro).__name__}: {erro}`"],
            )
            return

        mensagem = await self._entregar_view(
            interacao,
            view,
            no_canal=no_canal,
            chave_canal="CANAL_RANKING_LAUDOS",
            titulo_ok=f"Ranking de laudos ({periodo.value})",
            linhas_ok=[f"Total **{total}** laudos."],
        )
        if mensagem is not None:
            await salvar_historico_laudos(
                tipo=f"laudos_{periodo.value}",
                inicio=inicio,
                fim=fim,
                contagem=contagem,
                total=total,
                channel_id=mensagem.channel.id,
                message_id=mensagem.id,
            )

    @grupo_ranking.command(
        name="laudos-historico",
        description="Últimos rankings de laudos salvos",
    )
    @app_commands.describe(limite="Quantidade (1–25)")
    async def laudos_historico(
        self,
        interacao: discord.Interaction,
        limite: app_commands.Range[int, 1, 25] = 10,
    ):
        """Lista histórico de laudos (ephemeral)."""
        await interacao.response.defer(ephemeral=True)
        try:
            from src.utils.formatacao import formatar_data_hora_local

            registros = await listar_historico_laudos(limite=limite)
            if not registros:
                await responder_erro(
                    interacao,
                    titulo="Nada para mostrar",
                    linhas=["_Nenhum ranking de laudos salvo ainda._"],
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
                    discord.ui.TextDisplay(
                        "# 🧠 HISTÓRICO — LAUDOS\n" + "\n".join(linhas)
                    ),
                    accent_color=discord.Color.blurple(),
                )
            )
            await responder_view(interacao, layout, ephemeral=True)
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Erro ao listar histórico",
                linhas=[f"`{erro}`"],
            )

    # ── Moedas ───────────────────────────────────────────────────────────

    @grupo_ranking.command(
        name="moedas-tempo-real",
        description="Ranking de moedas (saldo atual / ao vivo)",
    )
    @app_commands.describe(
        no_canal=(
            "True = posta/atualiza no canal oficial. False = só você vê."
        ),
    )
    async def moedas_tempo_real(
        self,
        interacao: discord.Interaction,
        no_canal: bool = False,
    ):
        """
        Prévia ephemeral ou postagem no canal de moedas.

        O loop de 1 min continua atualizando a mensagem persistente do canal;
        este comando serve para ver agora ou forçar uma publicação.
        """
        await interacao.response.defer(ephemeral=True)
        if interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Sem servidor",
                linhas=["Use dentro do servidor."],
            )
            return
        try:
            view = await montar_view_ranking_moedas(interacao.guild)
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha inesperada",
                linhas=[f"`{erro}`"],
            )
            return
        await self._entregar_view(
            interacao,
            view,
            no_canal=no_canal,
            chave_canal="RANKING_MOEDAS",
            titulo_ok="Ranking de moedas (tempo real)",
        )

    # ── Entrega comum (ephemeral x canal) ───────────────────────────────

    async def _entregar_view(
        self,
        interacao: discord.Interaction,
        view: discord.ui.LayoutView,
        *,
        no_canal: bool,
        chave_canal: str,
        titulo_ok: str,
        linhas_ok: list[str] | None = None,
    ) -> discord.Message | None:
        """
        no_canal=False → só o autor vê (ephemeral).
        no_canal=True  → envia no canal oficial e confirma no ephemeral.

        Devolve a mensagem do canal quando publicou; None na prévia.
        """
        if not no_canal:
            await responder_view(interacao, view, ephemeral=True)
            return None

        canal = _canal_por_chave(interacao.guild, chave_canal)
        if canal is None:
            await responder_erro(
                interacao,
                titulo="Canal não configurado",
                linhas=[
                    f"`CANAIS['{chave_canal}']` ausente ou canal inválido.",
                    "A prévia não foi postada. Use no_canal=False para ver só você.",
                ],
            )
            return None

        mensagem = await canal.send(view=view)
        linhas = [f"Postado em {canal.mention}."]
        if linhas_ok:
            linhas.extend(linhas_ok)
        await responder_sucesso(
            interacao,
            titulo=titulo_ok,
            linhas=linhas,
        )
        return mensagem


async def setup(bot: commands.Bot) -> None:
    """Registra o grupo /ranking único."""
    await bot.add_cog(RankingCog(bot))
    nomes = [comando.name for comando in RankingCog.grupo_ranking.commands]
    registrador.info("RankingCog registrado com subcomandos: %s", nomes)
