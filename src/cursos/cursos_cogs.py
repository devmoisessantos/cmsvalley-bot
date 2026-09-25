"""Comandos admin e listeners persistentes do domínio de cursos."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import delete

from src.cursos.cursos_service import listar_solicitacoes_por_status
from src.cursos.cursos_setup import garantir_painel_cursos
from src.cursos.cursos_views import (
    CUSTOM_ID_ACEITAR,
    CUSTOM_ID_APROVAR,
    CUSTOM_ID_CANCELA_DECISAO,
    CUSTOM_ID_RECUSAR,
    CUSTOM_ID_REGISTRAR_REPASSE,
    CUSTOM_ID_REPROVAR,
    apagar_card_do_pedido,
    atualizar_ou_publicar_agendamento,
    processar_clique_abrir_decisao,
    processar_clique_aceitar_curso,
    processar_clique_cancelar_decisao,
    processar_clique_recusar_curso,
    processar_registrar_repasse_curso,
    processar_select_decisao_curso,
    publicar_para_decisao,
    view_persistente_cursos,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.utils.error_handling import enviar_erro_para_log_erros
from src.utils.mensagens import responder_erro, responder_sucesso
from src.utils.permissions import apenas_administrador

registrador = logging.getLogger(__name__)

PREFIXO_SELECT_DECISAO = "cursos:sel_decisao:"


class CursosCog(commands.Cog):
    """Comandos de barra e roteamento de botões do domínio de cursos."""

    grupo_cursos = app_commands.Group(
        name="cursos",
        description="Administração do fluxo de cursos",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_cursos.command(
        name="painel",
        description="Republica o painel de solicitar cursos (admin)",
    )
    @apenas_administrador()
    async def painel(self, interacao: discord.Interaction):
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

    @grupo_cursos.command(
        name="republicar-pendentes",
        description=(
            "Republica cards pendentes no fim dos canais "
            "(agendamento e aprovar/reprovar)"
        ),
    )
    @apenas_administrador()
    async def republicar_pendentes(self, interacao: discord.Interaction):
        """Apaga cards antigos de pedidos em aberto e publica de novo no fim.

        - AGENDADO: remove a mensagem antiga (se ainda existir) e publica
          de novo no canal de agendamentos.
        - ACEITO: publica de novo no canal de aprovar/reprovar com os
          botões atuais (incluindo Registrar Pagamento quando couber).

        Cards já processados (aceitos/recusados/decididos) não são
        recriados. Mensagens órfãs antigas no canal de decisão precisam
        ser apagadas à mão se sobrarem duplicatas.
        """
        await interacao.response.defer(ephemeral=True)
        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use este comando dentro do servidor."],
            )
            return

        try:
            agendados = await listar_solicitacoes_por_status("AGENDADO")
            aceitos = await listar_solicitacoes_por_status("ACEITO")

            ok_agendamento = 0
            falha_agendamento = 0
            for registro in agendados:
                membro = guilda.get_member(registro.discord_id)
                if membro is None:
                    falha_agendamento += 1
                    continue
                # atualizar_ou_publicar já apaga a mensagem antiga e
                # republica no fim do canal.
                ok = await atualizar_ou_publicar_agendamento(
                    guilda,
                    membro=membro,
                    registro=registro,
                )
                if ok:
                    ok_agendamento += 1
                else:
                    falha_agendamento += 1

            ok_decisao = 0
            falha_decisao = 0
            for registro in aceitos:
                aluno = guilda.get_member(registro.discord_id)
                try:
                    # Apaga pelo id no banco (se existir) e publica de novo
                    # gravando o id da mensagem nova.
                    await apagar_card_do_pedido(guilda, registro.id)
                    ok = await publicar_para_decisao(
                        guilda,
                        registro=registro,
                        aluno=aluno,
                    )
                    if ok:
                        ok_decisao += 1
                    else:
                        falha_decisao += 1
                except Exception as erro_decisao:
                    falha_decisao += 1
                    await enviar_erro_para_log_erros(
                        guilda,
                        "Falha ao republicar card de decisão de curso",
                        erro_decisao,
                        contexto="cursos.republicar_pendentes.decisao",
                        usuario=interacao.user,
                    )

            linhas = [
                f"**Agendamento:** {ok_agendamento} republicado(s)"
                + (
                    f", {falha_agendamento} falha(s)"
                    if falha_agendamento
                    else ""
                )
                + f" de {len(agendados)} pendente(s).",
                f"**Aprovar/reprovar:** {ok_decisao} republicado(s)"
                + (
                    f", {falha_decisao} falha(s)"
                    if falha_decisao
                    else ""
                )
                + f" de {len(aceitos)} aceito(s).",
                "Cada card grava `mensagem_id` no banco.",
                "Botões usam custom_id e sobrevivem a restart.",
            ]
            await responder_sucesso(
                interacao,
                titulo="Pendentes republicados",
                linhas=linhas,
                delay=30,
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                guilda,
                "Erro ao republicar pendentes de cursos",
                erro,
                contexto="cursos.republicar_pendentes",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Falha ao republicar. Veja LOG_ERROS."],
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

            if custom_id.startswith(CUSTOM_ID_RECUSAR):
                solicitacao_id = int(custom_id[len(CUSTOM_ID_RECUSAR) :])
                if solicitacao_id > 0:
                    await processar_clique_recusar_curso(interacao, solicitacao_id)
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

            if custom_id.startswith(CUSTOM_ID_REGISTRAR_REPASSE):
                solicitacao_id = int(
                    custom_id[len(CUSTOM_ID_REGISTRAR_REPASSE) :]
                )
                if solicitacao_id > 0:
                    await processar_registrar_repasse_curso(
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
