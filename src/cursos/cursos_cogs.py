"""Comandos admin e listeners persistentes do domínio de cursos."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import delete

from src.cursos.cursos_setup import garantir_painel_cursos
from src.cursos.cursos_views import (
    CUSTOM_ID_ACEITAR,
    CUSTOM_ID_APROVAR,
    CUSTOM_ID_CANCELA_DECISAO,
    CUSTOM_ID_REPROVAR,
    processar_clique_abrir_decisao,
    processar_clique_aceitar_curso,
    processar_clique_cancelar_decisao,
    processar_select_decisao_curso,
    view_persistente_cursos,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.utils.mensagens import responder_sucesso
from src.utils.permissions import apenas_administrador

registrador = logging.getLogger(__name__)

PREFIXO_SELECT_DECISAO = "cursos:sel_decisao:"


class CursosCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="painel-cursos",
        description="Republica o painel de solicitar cursos (admin)",
    )
    @apenas_administrador()
    async def painel_cursos(self, interacao: discord.Interaction):
        """Força a recriação do painel de cursos no canal configurado.

        Remove a referência persistida antes de chamar a garantia do painel,
        evitando que uma mensagem antiga seja tratada como válida. A operação
        grava no banco e informa privadamente ao administrador quando termina.
        """
        await interacao.response.defer(ephemeral=True)
        async with async_session() as sessao:
            await sessao.execute(
                delete(PainelPostado).where(PainelPostado.nome_painel == "cursos")
            )
            await sessao.commit()
        await garantir_painel_cursos(self.bot, interacao)
        await responder_sucesso(
            interacao,
            titulo="Painel de cursos",
            linhas=["Painel republicado no canal configurado."],
            delay=12,
        )

    @commands.Cog.listener()
    async def on_interaction(self, interacao: discord.Interaction):
        """
        Botões e selects dinâmicos de cursos (aceitar / aprovar / reprovar).

        custom_id carrega o id da solicitação. Depois de um restart a view
        em memória some, mas o Discord ainda entrega o clique — este listener
        reconstrói a ação a partir do banco, sem precisar apagar mensagens.
        """
        if interacao.type is not discord.InteractionType.component:
            return
        if interacao.response.is_done():
            return

        data = interacao.data or {}
        custom_id = str(data.get("custom_id") or "")
        if not custom_id.startswith("cursos:"):
            return

        try:
            if custom_id.startswith(CUSTOM_ID_ACEITAR):
                solicitacao_id = int(custom_id[len(CUSTOM_ID_ACEITAR) :])
                if solicitacao_id > 0:
                    await processar_clique_aceitar_curso(interacao, solicitacao_id)
                return

            if custom_id.startswith(CUSTOM_ID_APROVAR):
                solicitacao_id = int(custom_id[len(CUSTOM_ID_APROVAR) :])
                if solicitacao_id > 0:
                    await processar_clique_abrir_decisao(
                        interacao,
                        solicitacao_id,
                        modo="selecionar_aprovar",
                    )
                return

            if custom_id.startswith(CUSTOM_ID_REPROVAR):
                solicitacao_id = int(custom_id[len(CUSTOM_ID_REPROVAR) :])
                if solicitacao_id > 0:
                    await processar_clique_abrir_decisao(
                        interacao,
                        solicitacao_id,
                        modo="selecionar_reprovar",
                    )
                return

            if custom_id.startswith(CUSTOM_ID_CANCELA_DECISAO):
                solicitacao_id = int(custom_id[len(CUSTOM_ID_CANCELA_DECISAO) :])
                if solicitacao_id > 0:
                    await processar_clique_cancelar_decisao(
                        interacao,
                        solicitacao_id,
                    )
                return

            if custom_id.startswith(PREFIXO_SELECT_DECISAO):
                # cursos:sel_decisao:{id}:{modo}
                resto = custom_id[len(PREFIXO_SELECT_DECISAO) :]
                partes = resto.split(":", 1)
                if len(partes) != 2:
                    return
                solicitacao_id = int(partes[0])
                modo = partes[1]
                if solicitacao_id > 0 and modo in (
                    "selecionar_aprovar",
                    "selecionar_reprovar",
                ):
                    await processar_select_decisao_curso(
                        interacao,
                        solicitacao_id,
                        modo,
                    )
                return
        except ValueError:
            registrador.warning(
                "custom_id de curso inválido: %s",
                custom_id,
            )
        except Exception:
            registrador.exception(
                "Falha no on_interaction de cursos (%s)",
                custom_id,
            )


async def setup(bot: commands.Bot):
    """Registra a visualização persistente e os comandos de cursos."""
    bot.add_view(view_persistente_cursos())
    await bot.add_cog(CursosCog(bot))
