# src/utils/error_handling.py
"""
Mixins que capturam erros em Views e Modals.

Quando um botão, select ou modal quebra, o erro vai para o canal de LOG_ERROS
e o membro recebe um aviso discreto.
"""

import logging
import traceback

import discord

from src.config import CANAIS
from src.utils.mensagens import responder_erro


async def enviar_erro_para_log_erros(
    guilda: discord.Guild | None,
    titulo: str,
    erro: Exception,
    *,
    contexto: str | None = None,
    usuario: discord.abc.User | None = None,
):
    """
    Envia traceback para CANAIS['LOG_ERROS'].
    Pode ser chamado de qualquer lugar (service, task, modal), com ou sem interação.
    """
    if guilda is None:
        return

    canal_de_logs = guilda.get_channel(CANAIS.get("LOG_ERROS") or 0)
    if canal_de_logs is None:
        return

    traceback_completo = "".join(
        traceback.format_exception(type(erro), erro, erro.__traceback__)
    )
    traceback_curto = traceback_completo[-1500:]

    linhas = [f"⚠️ **{titulo}**"]
    if usuario is not None:
        linhas.append(f"Usuário: {usuario.mention} (`{usuario.id}`)")
    if contexto:
        linhas.append(f"Contexto: `{contexto}`")
    linhas.append(f"Erro: `{type(erro).__name__}: {erro}`")
    linhas.append(f"```py\n{traceback_curto}\n```")

    try:
        await canal_de_logs.send("\n".join(linhas))
    except discord.HTTPException as erro_ao_publicar_no_log:
        # Ultimo recurso: se nem o canal de LOG_ERROS aceita a mensagem, nao
        # da para avisar pelo Discord. Sobra o console, e ele TEM que receber,
        # senao o erro original desaparece por completo.
        logging.error(
            "Nao consegui publicar o erro %r no canal de LOG_ERROS: %s",
            titulo,
            erro_ao_publicar_no_log,
        )


async def _enviar_erro_para_canal_de_logs(
    interacao: discord.Interaction,
    titulo: str,
    nome_do_componente: str,
    erro: Exception,
):
    """Atalho a partir de uma interação (Views / Modals)."""
    await enviar_erro_para_log_erros(
        interacao.guild,
        titulo,
        erro,
        contexto=nome_do_componente,
        usuario=interacao.user,
    )


async def _avisar_membro_sobre_erro(interacao: discord.Interaction):
    """Avisa o membro que algo deu errado, sem expor detalhes técnicos."""
    try:
        await responder_erro(
            interacao,
            titulo="Erro inesperado",
            linhas=["Ocorreu um erro inesperado. A equipe foi notificada."],
            delay=15,
        )
    except discord.HTTPException as erro_ao_avisar_o_membro:
        # A interacao pode ter expirado (passou de 3 segundos ou 15 minutos).
        # O erro original ja foi para o log e para o canal de LOG_ERROS, entao
        # aqui so registro que o aviso na tela nao chegou a aparecer.
        logging.debug(
            "Nao consegui avisar quem clicou sobre o erro: %s",
            erro_ao_avisar_o_membro,
        )


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
        """
        Registra falhas de componentes e avisa quem interagiu sem expor detalhes.

        Este mixin é reutilizado por todos os domínios: envia o erro ao canal de
        auditoria e responde no Discord, impedindo que uma exceção de botão ou select
        termine como uma falha silenciosa para o membro.
        """
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
        """
        Centraliza erros de formulários para que todos os modais ajam da mesma forma.

        Como base reutilizada pelos domínios, identifica a classe do modal, registra
        a exceção no canal de logs e envia uma resposta discreta. Assim, uma falha
        no envio não revela detalhes técnicos nem deixa a pessoa sem retorno.
        """
        nome_do_modal = self.__class__.__name__
        await _enviar_erro_para_canal_de_logs(
            interacao,
            titulo="Erro em Modal",
            nome_do_componente=nome_do_modal,
            erro=erro,
        )
        await _avisar_membro_sobre_erro(interacao)


async def capturar_erro_e_logar(
    erro: Exception,
    *,
    contexto: str,
    guilda: discord.Guild | None = None,
    interacao: discord.Interaction | None = None,
    avisar_o_membro: bool = False,
):
    """
    Registra um erro no console e, se possivel, no canal de LOG_ERROS.

    E o jeito padrao de tratar erro em qualquer lugar do projeto: services,
    tasks, listeners e comandos. Nunca deixe um `except` sem chamar isto (ou
    um log equivalente), porque erro engolido em silencio e bug invisivel.

    Parametros:
    - erro: a excecao que foi capturada
    - contexto: onde aconteceu, em palavras (ex.: "aplicar_punicao")
    - guilda: servidor onde mandar o log; opcional se a interacao for passada
    - interacao: se vier, o usuario aparece no log
    - avisar_o_membro: se True, tambem manda um card discreto para quem clicou

    Exemplo:

        try:
            await aplicar_punicao(...)
        except Exception as erro_da_punicao:
            await capturar_erro_e_logar(
                erro_da_punicao,
                contexto="aplicar_punicao",
                interacao=interacao,
                avisar_o_membro=True,
            )
    """
    logging.error(
        "Erro em %s: %s: %s",
        contexto,
        type(erro).__name__,
        erro,
        exc_info=erro,
    )

    servidor_para_o_log = guilda
    usuario_do_erro = None

    if interacao is not None:
        if servidor_para_o_log is None:
            servidor_para_o_log = interacao.guild
        usuario_do_erro = interacao.user

    if servidor_para_o_log is not None:
        await enviar_erro_para_log_erros(
            servidor_para_o_log,
            titulo="Erro capturado",
            erro=erro,
            contexto=contexto,
            usuario=usuario_do_erro,
        )

    if avisar_o_membro and interacao is not None:
        await _avisar_membro_sobre_erro(interacao)


def ignorar_falha_cosmetica(erro: Exception, *, o_que_falhou: str) -> None:
    """
    Registra, em nivel baixinho, uma falha que NAO muda o resultado da acao.

    Existe para acabar com o `except ...: pass`. O `pass` e proibido porque
    apaga a prova do problema: quando alguem reclama que algo nao funcionou,
    nao ha nenhuma pista no log. Esta funcao resolve isso sem encher o log de
    ruido, porque grava em nivel `debug` (ligado so quando alguem esta
    investigando de proposito).

    Use SOMENTE quando as tres coisas abaixo forem verdade:

    1. A acao principal ja terminou com sucesso.
    2. O que falhou e enfeite: apagar mensagem antiga, atualizar um painel,
       mandar DM, editar um card informativo.
    3. Voce sabe por que pode falhar e nao ha o que fazer a respeito
       (mensagem ja apagada, bot sem permissao no canal, DM fechada).

    Se qualquer uma das tres for falsa, use `capturar_erro_e_logar` no lugar,
    porque ai o erro importa de verdade.

    Parametros:
    - erro: a excecao capturada
    - o_que_falhou: descricao curta do enfeite que nao deu certo, em palavras
      (ex.: "apagar a mensagem de preview")

    Exemplo:

        try:
            await mensagem_antiga.delete()
        except discord.NotFound as erro_ao_apagar:
            ignorar_falha_cosmetica(
                erro_ao_apagar,
                o_que_falhou="apagar a mensagem antiga do painel",
            )
    """
    logging.debug(
        "Falha sem consequencia ao %s: %s: %s",
        o_que_falhou,
        type(erro).__name__,
        erro,
    )
