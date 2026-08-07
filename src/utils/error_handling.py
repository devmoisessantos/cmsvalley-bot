# src/utils/error_handling.py
"""
Mixins que capturam erros em Views e Modals.

Quando um botão, select ou modal quebra, o erro vai para o canal de LOG_ERROS
e o membro recebe um aviso discreto.
"""

import traceback

import discord

from src.config import CANAIS
from src.utils.mensagens import responder_erro


async def _enviar_erro_para_canal_de_logs(
    interacao: discord.Interaction,
    titulo: str,
    nome_do_componente: str,
    erro: Exception,
):
    """Monta o texto do erro e envia no canal de logs, se existir."""
    guilda = interacao.guild
    if guilda is None:
        return

    canal_de_logs = guilda.get_channel(CANAIS["LOG_ERROS"])
    if canal_de_logs is None:
        return

    traceback_completo = "".join(
        traceback.format_exception(type(erro), erro, erro.__traceback__)
    )
    # O Discord limita o tamanho da mensagem; pegamos só o final do traceback.
    traceback_curto = traceback_completo[-1200:]

    texto_do_log = (
        f"⚠️ **{titulo}**\n"
        f"Usuário: {interacao.user.mention} (`{interacao.user.id}`)\n"
        f"Componente: `{nome_do_componente}`\n"
        f"Erro: `{erro}`\n"
        f"```py\n{traceback_curto}\n```"
    )

    try:
        await canal_de_logs.send(texto_do_log)
    except discord.HTTPException:
        pass


async def _avisar_membro_sobre_erro(interacao: discord.Interaction):
    """Avisa o membro que algo deu errado, sem expor detalhes técnicos."""
    try:
        await responder_erro(
            interacao,
            titulo="Erro inesperado",
            linhas=["Ocorreu um erro inesperado. A equipe foi notificada."],
            delay=15,
        )
    except discord.HTTPException:
        # A interação pode ter expirado (passou de 3 segundos ou 15 minutos).
        pass


class LoggingViewMixin:
    """
    Coloque este mixin nas Views para capturar erros de botões e selects.

    Exemplo:
        class MeuPainel(LoggingViewMixin, discord.ui.LayoutView):
            ...
    """

    async def on_error(
        self,
        interacao: discord.Interaction,
        erro: Exception,
        item,
    ):
        nome_do_componente = item.__class__.__name__
        await _enviar_erro_para_canal_de_logs(
            interacao,
            titulo="Erro em componente",
            nome_do_componente=nome_do_componente,
            erro=erro,
        )
        await _avisar_membro_sobre_erro(interacao)


class LoggingModalMixin:
    """
    Coloque este mixin nos Modals para capturar erros no envio do formulário.

    Exemplo:
        class MeuModal(LoggingModalMixin, discord.ui.Modal):
            ...
    """

    async def on_error(
        self,
        interacao: discord.Interaction,
        erro: Exception,
    ):
        nome_do_modal = self.__class__.__name__
        await _enviar_erro_para_canal_de_logs(
            interacao,
            titulo="Erro em Modal",
            nome_do_componente=nome_do_modal,
            erro=erro,
        )
        await _avisar_membro_sobre_erro(interacao)
