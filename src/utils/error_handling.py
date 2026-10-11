# src/utils/error_handling.py
"""
Captura e publica erros do bot de forma legível.

Quando um botão, select, modal, comando ou serviço quebra, este módulo:
1. classifica o tipo de falha (banco, interação, rede, etc.)
2. aponta o arquivo e a linha do projeto onde o erro nasceu
3. explica o problema em português
4. sugere onde olhar para corrigir
5. manda o traceback para o canal de LOG_ERROS

Use sempre `capturar_erro_e_logar` em `except` de service/task/listener.
Views e Modals herdam os mixins abaixo.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

import discord

from src.config import CANAIS
from src.utils.mensagens import responder_erro

# Limite seguro para um bloco de código no Discord (mensagem ≤ 2000).
_LIMITE_TRACEBACK = 1600


def _caminho_relativo_do_projeto(caminho: str) -> str:
    """Encurta caminho absoluto para algo legível (a partir de src/)."""
    texto = caminho.replace("\\", "/")
    marcador = "/src/"
    if marcador in texto:
        return "src/" + texto.split(marcador, 1)[1]
    return Path(texto).name


def _local_do_erro_no_projeto(erro: BaseException) -> str:
    """
    Devolve a última linha do traceback que está dentro de src/.

    Exemplo: `src/plantao/plantao_service.py:379 em _finalizar_periodo_em_call`
    """
    pilha = traceback.extract_tb(erro.__traceback__)
    if not pilha:
        return "local desconhecido"

    candidato = None
    for quadro in pilha:
        caminho = (quadro.filename or "").replace("\\", "/")
        if "/src/" in caminho or caminho.endswith(".py"):
            if "/site-packages/" in caminho or "/discord/" in caminho:
                continue
            if "/src/" in caminho or "cmsvalley" in caminho:
                candidato = quadro

    if candidato is None:
        candidato = pilha[-1]

    arquivo = _caminho_relativo_do_projeto(candidato.filename or "?")
    linha = candidato.lineno or "?"
    funcao = candidato.name or "?"
    return f"`{arquivo}:{linha}` em `{funcao}`"


def _traceback_texto(erro: BaseException) -> str:
    """Traceback completo em texto, cortado pelo fim (parte mais útil)."""
    completo = "".join(
        traceback.format_exception(type(erro), erro, erro.__traceback__)
    )
    if len(completo) <= _LIMITE_TRACEBACK:
        return completo
    return completo[-_LIMITE_TRACEBACK:]


def classificar_erro(erro: BaseException) -> tuple[str, str, str]:
    """
    Devolve (categoria, o_que_aconteceu, como_corrigir).

    A classificação é só para o log humano: não muda o fluxo do bot.
    """
    nome = type(erro).__name__
    texto = str(erro) or ""
    texto_minusculo = texto.lower()

    # --- Banco de dados ---
    try:
        from sqlalchemy.exc import (
            DBAPIError,
            IntegrityError,
            OperationalError,
            SQLAlchemyError,
            TimeoutError as SQLTimeoutError,
        )
    except ImportError:  # pragma: no cover
        DBAPIError = IntegrityError = OperationalError = SQLAlchemyError = ()
        SQLTimeoutError = ()

    if isinstance(erro, IntegrityError) if IntegrityError else False:
        return (
            "Banco de dados (integridade)",
            "O banco recusou gravar porque a regra de integridade quebrou "
            "(chave duplicada, foreign key, NOT NULL, etc.).",
            "Confira se o registro já existe, se o ID ligado existe na "
            "tabela pai e se a migração correspondente foi aplicada. "
            "Olhe a constraint citada na mensagem do erro.",
        )

    if isinstance(erro, OperationalError) if OperationalError else False:
        return (
            "Banco de dados (conexão/operação)",
            "Falha ao falar com o PostgreSQL (conexão, timeout ou SQL inválido).",
            "Verifique se o banco está no ar, as variáveis do `.env`, "
            "firewall/rede e se alguma migração ficou pela metade. "
            "Reiniciar o pool de conexões costuma ajudar em idle timeout.",
        )

    if isinstance(erro, SQLTimeoutError) if SQLTimeoutError else False:
        return (
            "Banco de dados (timeout)",
            "A consulta ou a conexão com o banco demorou além do limite.",
            "Veja consultas lentas, locks e o tamanho da tabela envolvida. "
            "Confira também se há transação aberta sem commit/rollback.",
        )

    if isinstance(erro, (SQLAlchemyError, DBAPIError)) if SQLAlchemyError else False:
        return (
            "Banco de dados",
            f"Erro do SQLAlchemy/driver: {nome}.",
            "Leia a mensagem do driver abaixo no traceback. Confira modelo, "
            "sessão (`rollback` no except) e se a coluna/tabela existe.",
        )

    if "asyncpg" in nome.lower() or "psycopg" in nome.lower():
        return (
            "Banco de dados (driver)",
            f"O driver do PostgreSQL levantou `{nome}`.",
            "Confira conexão, SSL do banco e a SQL gerada pelo ORM. "
            "A mensagem do driver no traceback costuma apontar a causa.",
        )

    # --- Interação Discord ---
    from src.utils.mensagens import interacao_nao_pode_mais_ser_respondida

    if interacao_nao_pode_mais_ser_respondida(erro):
        return (
            "Interação expirada (não respondeu a tempo)",
            "O Discord invalidou o token da interação (código 10062). "
            "Isso acontece quando o bot demora mais de 3 segundos para "
            "responder ou dar `defer`, ou quando tenta responder de novo "
            "depois que o token já morreu.",
            "No início do callback, use `await interaction.response.defer("
            "ephemeral=True)` (ou responda na hora). Depois use followup. "
            "Evite trabalho pesado (banco/API) antes do defer.",
        )

    if isinstance(erro, discord.InteractionResponded):
        return (
            "Interação já respondida",
            "O código tentou usar `interaction.response` de novo, mas a "
            "interação já tinha sido respondida ou adiada.",
            "Depois do primeiro `response.send_message` / `defer`, use só "
            "`followup` ou `edit_original_response`. Busque um segundo "
            "`response.` no mesmo fluxo.",
        )

    if isinstance(erro, discord.HTTPException):
        codigo = getattr(erro, "code", None)
        status = getattr(erro, "status", None)
        if codigo == 40060 or "already been acknowledged" in texto_minusculo:
            return (
                "Interação já reconhecida",
                "O Discord já tinha aceitado uma resposta para esta "
                "interação (código 40060).",
                "Garanta um único caminho de resposta. Se deu defer, "
                "não chame `response.send_message` de novo.",
            )
        if isinstance(erro, discord.Forbidden) or status == 403:
            return (
                "Sem permissão no Discord",
                "O bot não tem permissão para a ação (enviar mensagem, "
                "editar cargo, gerenciar canal, etc.).",
                "Confira o cargo do bot no servidor, permissões do canal "
                "e se algum cargo alvo está acima do bot na hierarquia.",
            )
        if isinstance(erro, discord.NotFound) or status == 404:
            return (
                "Recurso Discord não encontrado",
                "Mensagem, canal, membro ou reação não existe mais.",
                "A mensagem pode ter sido apagada ou o membro saiu. "
                "Trate `NotFound` quando a ausência for esperada; senão "
                "confira o ID gravado no banco/config.",
            )
        return (
            "Erro HTTP do Discord",
            f"A API do Discord respondeu com falha "
            f"(status={status}, code={codigo}): {texto[:200]}",
            "Veja rate limit (429), payload inválido e permissões. "
            "O corpo da resposta no traceback ajuda a achar o campo errado.",
        )

    # --- Rede ---
    if "ClientConnectorSSLError" in nome or (
        "ssl" in texto_minusculo and "handshake" in texto_minusculo
    ):
        return (
            "Rede / SSL",
            "Falha de SSL ou handshake ao falar com discord.com (ou outro host).",
            "Costuma ser instabilidade de rede ou proxy. Se for frequente, "
            "verifique DNS, firewall e horário do sistema.",
        )

    if "cannot connect to host" in texto_minusculo or "ClientConnectorError" in nome:
        return (
            "Rede / conexão",
            "Não foi possível abrir conexão com o host remoto.",
            "Verifique internet do servidor do bot, DNS e se o Discord "
            "está acessível a partir da máquina.",
        )

    # --- Timeout genérico ---
    if nome in {"TimeoutError", "asyncio.TimeoutError"} or "timeout" in nome.lower():
        return (
            "Timeout",
            "Uma operação demorou além do tempo máximo e foi cancelada.",
            "Veja qual await travou (banco, HTTP, Discord). Aumentar timeout "
            "só mascara; o ideal é achar a operação lenta.",
        )

    # --- Validação ---
    if isinstance(erro, ValueError):
        return (
            "Valor inválido",
            f"Uma validação de negócio ou de entrada falhou: {texto[:240]}",
            "Confira o que o usuário digitou e as regras da função que "
            "levantou o ValueError. Se a mensagem for clara, o fluxo pode "
            "só devolver aviso ao membro em vez de logar como erro grave.",
        )

    if isinstance(erro, KeyError):
        return (
            "Chave ausente",
            f"O código procurou uma chave que não existe: {texto[:240]}",
            "Veja dicionário de config (CANAIS, CARGOS) ou o payload. "
            "Chave faltando em `config.py` é causa comum.",
        )

    if isinstance(erro, AttributeError):
        return (
            "Atributo ausente",
            f"Objeto sem o atributo esperado: {texto[:240]}",
            "Algo veio `None` ou de tipo diferente do previsto. Confira "
            "se o membro/canal/estado existe antes de acessar o campo.",
        )

    if isinstance(erro, TypeError):
        return (
            "Tipo incorreto",
            f"Argumento ou operação com tipo errado: {texto[:240]}",
            "Compare a assinatura da função com o que foi passado. "
            "Misturar `None` com int/str é causa frequente.",
        )

    return (
        "Erro não classificado",
        f"`{nome}`: {texto[:280] or '(sem mensagem)'}",
        "Leia o traceback: a última linha dentro de `src/` costuma ser o "
        "ponto exato. Reproduza a ação do membro e confira logs anteriores.",
    )


def _erro_e_transitorio_de_rede_pura(erro: BaseException) -> bool:
    """
    True só para falha de rede/SSL em que logar no Discord também pode falhar.

    Interação expirada NÃO entra aqui: ela deve ir para LOG_ERROS com
    explicação (o token morreu, mas o canal de log ainda funciona).
    """
    nome_do_erro = type(erro).__name__
    texto = str(erro).lower()
    if "ClientConnectorSSLError" in nome_do_erro:
        return True
    if "ssl" in texto and "handshake" in texto:
        return True
    if "cannot connect to host discord.com" in texto:
        return True
    if "ClientConnectorError" in nome_do_erro and "discord" in texto:
        return True
    return False


async def enviar_erro_para_log_erros(
    guilda: discord.Guild | None,
    titulo: str,
    erro: Exception,
    *,
    contexto: str | None = None,
    usuario: discord.abc.User | None = None,
):
    """
    Envia erro classificado + traceback para CANAIS['LOG_ERROS'].

    Pode ser chamado de qualquer lugar (service, task, modal), com ou sem interação.
    """
    categoria, o_que_aconteceu, como_corrigir = classificar_erro(erro)
    local = _local_do_erro_no_projeto(erro)
    traceback_curto = _traceback_texto(erro)

    logging.error(
        "%s | %s | %s | %s: %s",
        titulo,
        categoria,
        local,
        type(erro).__name__,
        erro,
        exc_info=erro,
    )

    if guilda is None:
        return

    canal_de_logs = guilda.get_channel(CANAIS.get("LOG_ERROS") or 0)
    if canal_de_logs is None:
        return

    linhas_principais = [
        f"**Categoria:** {categoria}",
        f"**Tipo:** `{type(erro).__name__}`",
        f"**Onde:** {local}",
    ]
    if usuario is not None:
        linhas_principais.append(
            f"**Usuário:** {usuario.mention} (`{usuario.id}`)"
        )
    if contexto:
        linhas_principais.append(f"**Contexto:** `{contexto}`")
    linhas_principais.append(f"**Mensagem:** `{erro}`")
    linhas_principais.append("")
    linhas_principais.append(f"**O que aconteceu:** {o_que_aconteceu}")
    linhas_principais.append("")
    linhas_principais.append(f"**Como corrigir / onde verificar:** {como_corrigir}")

    bloco_traceback = f"**Traceback:**\n```py\n{traceback_curto}\n```"

    try:
        from src.utils.log_container import LogContainerView

        view_do_log = LogContainerView(
            titulo=titulo,
            linhas="\n".join(linhas_principais),
            guild=guilda,
            cor=discord.Color.red(),
            blocos_extra=[bloco_traceback],
        )
        await canal_de_logs.send(view=view_do_log)
    except Exception as erro_ao_publicar_no_log:
        # Fallback em texto puro se o Components V2 falhar.
        texto_plano = (
            f"⚠️ **{titulo}**\n"
            + "\n".join(linhas_principais)
            + f"\n{bloco_traceback}"
        )
        try:
            await canal_de_logs.send(texto_plano[:1900])
        except discord.HTTPException as erro_http:
            logging.error(
                "Nao consegui publicar o erro %r no canal de LOG_ERROS: %s "
                "(fallback tambem falhou: %s)",
                titulo,
                erro_ao_publicar_no_log,
                erro_http,
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
        Registra falhas de componentes e avisa quem interagiu.

        Interação expirada também vai para LOG_ERROS (com orientação de
        defer). Só falha pura de rede/SSL fica só no console.
        """
        nome_do_componente = item.__class__.__name__

        if _erro_e_transitorio_de_rede_pura(erro):
            logging.warning(
                "Falha de rede em componente %s (usuário %s): %s",
                nome_do_componente,
                getattr(interacao.user, "id", "?"),
                erro,
            )
            return

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
        Centraliza erros de formulários: log classificado + aviso discreto.
        """
        nome_do_modal = self.__class__.__name__

        if _erro_e_transitorio_de_rede_pura(erro):
            logging.warning(
                "Falha de rede em modal %s (usuário %s): %s",
                nome_do_modal,
                getattr(interacao.user, "id", "?"),
                erro,
            )
            return

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
    Registra um erro no console e, se possível, no canal de LOG_ERROS.

    É o jeito padrão de tratar erro em qualquer lugar do projeto: services,
    tasks, listeners e comandos. Nunca deixe um `except` sem chamar isto (ou
    um log equivalente), porque erro engolido em silêncio é bug invisível.

    Parâmetros:
    - erro: a exceção que foi capturada
    - contexto: onde aconteceu, em palavras (ex.: "aplicar_punicao")
    - guilda: servidor onde mandar o log; opcional se a interação for passada
    - interacao: se vier, o usuário aparece no log
    - avisar_o_membro: se True, também manda um card discreto para quem clicou
    """
    servidor_para_o_log = guilda
    usuario_do_erro = None

    if interacao is not None:
        if servidor_para_o_log is None:
            servidor_para_o_log = interacao.guild
        usuario_do_erro = interacao.user

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
    Registra, em nível baixinho, uma falha que NÃO muda o resultado da ação.

    Existe para acabar com o `except ...: pass`. O `pass` é proibido porque
    apaga a prova do problema: quando alguém reclama que algo não funcionou,
    não há nenhuma pista no log. Esta função resolve isso sem encher o log de
    ruído, porque grava em nível `debug` (ligado só quando alguém está
    investigando de propósito).

    Use SOMENTE quando as três coisas abaixo forem verdade:

    1. A ação principal já terminou com sucesso.
    2. O que falhou é enfeite: apagar mensagem antiga, atualizar um painel,
       mandar DM, editar um card informativo.
    3. Você sabe por que pode falhar e não há o que fazer a respeito
       (mensagem já apagada, bot sem permissão no canal, DM fechada).

    Se qualquer uma das três for falsa, use `capturar_erro_e_logar` no lugar.
    """
    logging.debug(
        "Falha sem consequência ao %s: %s: %s",
        o_que_falhou,
        type(erro).__name__,
        erro,
    )
