# src/utils/mensagens.py
"""
Central de respostas ao usuário.

Toda mensagem rápida para o membro deve passar por aqui.
Usa Components V2 (LayoutView + Container + TextDisplay).
"""

import asyncio
import logging

import discord

# ---------------------------------------------------------------------------
# Cores padrão do projeto
# ---------------------------------------------------------------------------

COR_SUCESSO = discord.Color.green()
COR_ERRO = discord.Color.red()
COR_AVISO = discord.Color.orange()
COR_INFO = discord.Color.blurple()


# ---------------------------------------------------------------------------
# Card visual (Components V2)
# ---------------------------------------------------------------------------


class CardView(discord.ui.LayoutView):
    """
    Card padrão de resposta rápida.

    Mostra um título grande e uma lista de linhas com marcador.
    Pode receber uma linha extra de botões (ActionRow).
    """

    def __init__(
        self,
        titulo: str,
        linhas: list[str],
        cor: discord.Color = COR_INFO,
        timeout: int | None = 180,
        extra_row: discord.ui.ActionRow | None = None,
        com_marcador: bool = True,
    ):
        super().__init__(timeout=timeout)

        if com_marcador:
            texto_das_linhas = "\n".join(f"`•` {linha}" for linha in linhas)
        else:
            texto_das_linhas = "\n".join(linhas)

        componentes = [
            discord.ui.TextDisplay(f"# {titulo}"),
            discord.ui.TextDisplay(texto_das_linhas),
        ]

        if extra_row is not None:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
            )
            componentes.append(extra_row)

        self.container = discord.ui.Container(*componentes, accent_color=cor)
        self.add_item(self.container)


# ---------------------------------------------------------------------------
# Utilitários de exclusão
# ---------------------------------------------------------------------------


async def excluir_mensagem(mensagem: discord.Message, delay: int | None = 120):
    """
    Espera alguns segundos e apaga a mensagem.

    Se delay for None, a mensagem nunca é apagada automaticamente.
    Use None quando o card tem botões ou selects que o membro ainda vai usar.
    """
    if delay is None:
        return

    await asyncio.sleep(delay)

    try:
        await mensagem.delete()
    except discord.NotFound as erro_ao_excluir:
        # A mensagem ja foi apagada por outra pessoa ou pelo proprio bot.
        # Nao uso ignorar_falha_cosmetica aqui de proposito: este modulo e a
        # base de tudo e nao pode importar error_handling, que importa daqui.
        logging.debug(
            "Mensagem %s ja nao existia ao tentar excluir: %s",
            mensagem.id,
            erro_ao_excluir,
        )
    except discord.Forbidden:
        logging.warning(
            "Sem permissão para excluir a mensagem %s.",
            mensagem.id,
        )


async def destruir_print_com_aviso(mensagem_print: discord.Message, delay: int = 10):
    """
    Usado quando a chamada é abortada depois que o print do /ems já foi enviado.

    Avisa no canal e apaga o print e o aviso depois do delay.
    """
    if mensagem_print is None:
        return

    mensagem_aviso = None
    try:
        mensagem_aviso = await mensagem_print.reply(
            f"⚠️ Esta mensagem e o print do `/ems` serão destruídos em {delay} segundos."
        )
    except discord.HTTPException as falha_ao_avisar:
        # Se o aviso nao pode ser enviado, o print ainda precisa ser apagado.
        logging.warning(
            "Não foi possível avisar sobre a destruição do print: %s",
            falha_ao_avisar,
        )

    async def _destruir_as_duas_mensagens():
        await asyncio.sleep(delay)
        for mensagem_para_apagar in (mensagem_print, mensagem_aviso):
            if mensagem_para_apagar is None:
                continue
            try:
                await mensagem_para_apagar.delete()
            except discord.NotFound as erro_ao_apagar_par:
                # A mensagem ja foi apagada por outra pessoa. Nada a fazer.
                logging.debug(
                    "Mensagem %s ja nao existia ao tentar apagar: %s",
                    mensagem_para_apagar.id,
                    erro_ao_apagar_par,
                )
            except (discord.Forbidden, discord.HTTPException) as falha_ao_apagar:
                logging.warning(
                    "Não foi possível apagar a mensagem %s: %s",
                    mensagem_para_apagar.id,
                    falha_ao_apagar,
                )

    asyncio.create_task(_destruir_as_duas_mensagens())


# ---------------------------------------------------------------------------
# Núcleo: enviar card respeitando response vs followup
# ---------------------------------------------------------------------------


async def enviar_card(
    interacao: discord.Interaction,
    titulo: str,
    linhas: list[str],
    cor: discord.Color = COR_INFO,
    extra_row: discord.ui.ActionRow | None = None,
    delay: int | None = 10,
    ephemeral: bool = True,
    com_marcador: bool = True,
) -> discord.Message:
    """
    Envia um CardView para a interação.

    Se a interação ainda não foi respondida, usa response.send_message.
    Se já foi respondida (por exemplo depois de um defer), usa followup.send.

    A mensagem some sozinha depois de `delay` segundos.
    Se delay for None, a mensagem fica até ser apagada manualmente.

    Retorna a mensagem enviada.
    """
    view_do_card = CardView(
        titulo=titulo,
        linhas=linhas,
        cor=cor,
        timeout=None,
        extra_row=extra_row,
        com_marcador=com_marcador,
    )

    interacao_ja_foi_respondida = interacao.response.is_done()

    if interacao_ja_foi_respondida:
        mensagem_enviada = await interacao.followup.send(
            view=view_do_card,
            ephemeral=ephemeral,
        )
    else:
        await interacao.response.send_message(
            view=view_do_card,
            ephemeral=ephemeral,
        )
        mensagem_enviada = await interacao.original_response()

    if delay is not None:
        asyncio.create_task(excluir_mensagem(mensagem_enviada, delay=delay))

    return mensagem_enviada


# ---------------------------------------------------------------------------
# Atalhos semânticos (sucesso / erro / aviso / info)
# ---------------------------------------------------------------------------


async def responder_sucesso(
    interacao: discord.Interaction,
    titulo: str,
    linhas: list[str],
    extra_row: discord.ui.ActionRow | None = None,
    com_marcador: bool = True,
    delay: int | None = 10,
) -> discord.Message:
    """Responde com card verde de sucesso."""
    return await enviar_card(
        interacao=interacao,
        titulo=titulo,
        linhas=linhas,
        cor=COR_SUCESSO,
        extra_row=extra_row,
        delay=delay,
        com_marcador=com_marcador,
    )


async def responder_erro(
    interacao: discord.Interaction,
    titulo: str,
    linhas: list[str],
    extra_row: discord.ui.ActionRow | None = None,
    com_marcador: bool = True,
    delay: int | None = 15,
) -> discord.Message:
    """
    Responde com card vermelho de erro. Delay um pouco maior para dar tempo de ler.
    """
    return await enviar_card(
        interacao=interacao,
        titulo=titulo,
        linhas=linhas,
        cor=COR_ERRO,
        extra_row=extra_row,
        delay=delay,
        com_marcador=com_marcador,
    )


async def responder_aviso(
    interacao: discord.Interaction,
    titulo: str,
    linhas: list[str],
    extra_row: discord.ui.ActionRow | None = None,
    com_marcador: bool = True,
    delay: int | None = 12,
) -> discord.Message:
    """Responde com card laranja de aviso."""
    return await enviar_card(
        interacao=interacao,
        titulo=titulo,
        linhas=linhas,
        cor=COR_AVISO,
        extra_row=extra_row,
        delay=delay,
        com_marcador=com_marcador,
    )


async def responder_info(
    interacao: discord.Interaction,
    titulo: str,
    linhas: list[str],
    extra_row: discord.ui.ActionRow | None = None,
    com_marcador: bool = True,
    delay: int | None = 10,
) -> discord.Message:
    """Responde com card azul (blurple) de informação neutra."""
    return await enviar_card(
        interacao=interacao,
        titulo=titulo,
        linhas=linhas,
        cor=COR_INFO,
        extra_row=extra_row,
        delay=delay,
        com_marcador=com_marcador,
    )


# ---------------------------------------------------------------------------
# Compatibilidade com código antigo (não remover)
# ---------------------------------------------------------------------------


async def responder_card(
    interacao: discord.Interaction,
    titulo: str,
    linhas: list[str],
    cor: discord.Color = COR_INFO,
    extra_row: discord.ui.ActionRow | None = None,
    com_marcador: bool = True,
    delay: int | None = 10,
) -> discord.Message:
    """
    Atalho antigo — mantido para não quebrar imports existentes.

    Prefira responder_sucesso, responder_erro, responder_aviso ou responder_info
    em código novo. Este ainda funciona e agora também trata followup automaticamente.
    """
    return await enviar_card(
        interacao=interacao,
        titulo=titulo,
        linhas=linhas,
        cor=cor,
        extra_row=extra_row,
        delay=delay,
        com_marcador=com_marcador,
    )


async def responder_ephemera(
    interacao: discord.Interaction,
    texto: str,
    view: discord.ui.View | None = None,
    delay: int = 10,
):
    """
    Resposta efêmera em texto simples (sem card).

    Mantida por compatibilidade. Em código novo, prefira os cards.
    Também trata response vs followup automaticamente.
    """
    interacao_ja_foi_respondida = interacao.response.is_done()

    if interacao_ja_foi_respondida:
        mensagem_enviada = await interacao.followup.send(
            content=texto,
            view=view,
            ephemeral=True,
        )
    else:
        await interacao.response.send_message(
            content=texto,
            view=view,
            ephemeral=True,
        )
        mensagem_enviada = await interacao.original_response()

    asyncio.create_task(excluir_mensagem(mensagem_enviada, delay=delay))


async def responder_view(
    interacao: discord.Interaction,
    view: discord.ui.LayoutView | discord.ui.View,
    *,
    ephemeral: bool = True,
    texto: str | None = None,
) -> discord.Message:
    """
    Responde a interação com uma View, com ou sem texto acima dela.

    Útil para painéis efêmeros em Components V2 (ex.: resposta do select do guia).
    Trata response vs followup automaticamente.

    O parâmetro `texto` existe para as views clássicas antigas, que precisam de
    uma frase explicando o que escolher. Em Components V2 o texto já vai dentro
    da própria view, então lá ele não é necessário.

    Se a interação já tiver sido reconhecida entre o is_done() e o
    send_message (corrida rara entre dois handlers), cai no followup em
    vez de estourar HTTP 40060.
    """
    interacao_ja_foi_respondida = interacao.response.is_done()

    if interacao_ja_foi_respondida:
        mensagem_enviada = await interacao.followup.send(
            content=texto,
            view=view,
            ephemeral=ephemeral,
        )
        return mensagem_enviada

    try:
        await interacao.response.send_message(
            content=texto,
            view=view,
            ephemeral=ephemeral,
        )
        return await interacao.original_response()
    except discord.HTTPException as erro_http:
        # 40060 = Interaction has already been acknowledged
        ja_reconhecida = (
            getattr(erro_http, "code", None) == 40060
            or "already been acknowledged" in str(erro_http).lower()
        )
        if not ja_reconhecida:
            raise
        mensagem_enviada = await interacao.followup.send(
            content=texto,
            view=view,
            ephemeral=ephemeral,
        )
        return mensagem_enviada


# ---------------------------------------------------------------------------
# Followup, edicao e card personalizado
# ---------------------------------------------------------------------------

# Marcador interno para "nao mexa no texto atual da mensagem".
#
# Nao da para usar None como padrao porque None e um valor valido e significa
# outra coisa para o Discord: apagar o texto que estava la. Este objeto vazio
# serve so para o codigo distinguir "nao passaram nada" de "passaram None".
_MANTER_O_TEXTO_ATUAL = object()


async def enviar_followup(
    interacao: discord.Interaction,
    titulo: str,
    linhas: list[str],
    cor: discord.Color = COR_INFO,
    extra_row: discord.ui.ActionRow | None = None,
    delay: int | None = 10,
    ephemeral: bool = True,
    com_marcador: bool = True,
) -> discord.Message:
    """
    Envia uma mensagem de acompanhamento (followup) depois de um defer.

    Use quando a interacao JA foi respondida ou adiada (`defer`) e voce quer
    mandar mais uma mensagem para o membro.

    Nao e preciso conferir nada antes: se por acaso a interacao ainda nao tiver
    sido respondida, esta funcao adia sozinha e depois manda o followup. Assim
    nunca acontece o erro "InteractionResponded" nem o "Esta interacao falhou".
    """
    interacao_ainda_nao_foi_respondida = not interacao.response.is_done()

    if interacao_ainda_nao_foi_respondida:
        # Sem este defer, o followup.send falharia porque o Discord ainda
        # espera a primeira resposta da interacao.
        await interacao.response.defer(ephemeral=ephemeral)

    return await enviar_card(
        interacao=interacao,
        titulo=titulo,
        linhas=linhas,
        cor=cor,
        extra_row=extra_row,
        delay=delay,
        ephemeral=ephemeral,
        com_marcador=com_marcador,
    )


async def enviar_followup_de_texto(
    interacao: discord.Interaction,
    texto: str,
    *,
    ephemeral: bool = True,
    delay: int | None = 10,
) -> discord.Message | None:
    """
    Envia um followup de texto simples, sem card.

    Mantida para os casos em que a mensagem e curta e um card seria exagero.
    Em codigo novo, prefira `enviar_followup`, que ja monta o card padrao.
    """
    interacao_ainda_nao_foi_respondida = not interacao.response.is_done()

    if interacao_ainda_nao_foi_respondida:
        await interacao.response.defer(ephemeral=ephemeral)

    mensagem_enviada = await interacao.followup.send(
        content=texto,
        ephemeral=ephemeral,
    )

    if delay is not None and mensagem_enviada is not None:
        asyncio.create_task(excluir_mensagem(mensagem_enviada, delay=delay))

    return mensagem_enviada


async def editar_mensagem_original(
    interacao: discord.Interaction,
    titulo: str | None = None,
    linhas: list[str] | None = None,
    cor: discord.Color = COR_INFO,
    extra_row: discord.ui.ActionRow | None = None,
    view: discord.ui.LayoutView | discord.ui.View | None = None,
    com_marcador: bool = True,
    texto: str | None = _MANTER_O_TEXTO_ATUAL,
) -> discord.Message | None:
    """
    Troca o conteudo da mensagem que a interacao ja respondeu.

    Duas formas de usar:

    1. Passando titulo e linhas: um CardView novo e montado para voce.
    2. Passando `view`: aquela view especifica substitui a mensagem.

    Serve para avancar etapas de um painel sem enviar mensagem nova.

    Sobre o parâmetro `texto`:

    - Não passar nada  → o texto que já estava na mensagem continua igual.
    - Passar `texto=None` → o texto antigo é APAGADO (usado ao trocar uma
      mensagem antiga de texto puro por um card Components V2).
    - Passar uma frase → aquele texto substitui o antigo.

    Esses três casos precisam ser diferentes porque `None` já significa
    "apague o texto" para o Discord; por isso existe o marcador interno
    `_MANTER_O_TEXTO_ATUAL` como valor padrão.
    """
    if view is None:
        if titulo is None:
            raise ValueError(
                "editar_mensagem_original precisa de um titulo (ou de uma view)."
            )

        view = CardView(
            titulo=titulo,
            linhas=linhas or [],
            cor=cor,
            timeout=None,
            extra_row=extra_row,
            com_marcador=com_marcador,
        )

    o_texto_deve_mudar = texto is not _MANTER_O_TEXTO_ATUAL
    interacao_ainda_nao_foi_respondida = not interacao.response.is_done()

    if interacao_ainda_nao_foi_respondida:
        # A forma mais barata de editar a mensagem do proprio componente
        # clicado: responde a interacao ja com o conteudo novo.
        if o_texto_deve_mudar:
            await interacao.response.edit_message(content=texto, view=view)
        else:
            await interacao.response.edit_message(view=view)
        return await interacao.original_response()

    if o_texto_deve_mudar:
        return await interacao.edit_original_response(content=texto, view=view)

    return await interacao.edit_original_response(view=view)


def criar_card_personalizado(
    titulo: str,
    linhas: list[str],
    cor: discord.Color = COR_INFO,
    extra_row: discord.ui.ActionRow | None = None,
    com_marcador: bool = True,
    timeout: int | None = None,
) -> CardView:
    """
    Monta um CardView sem enviar nada.

    Use quando o card vai ser guardado, reaproveitado ou enviado por outro
    caminho (por exemplo em uma DM, ou dentro de uma view maior).
    """
    return CardView(
        titulo=titulo,
        linhas=linhas,
        cor=cor,
        timeout=timeout,
        extra_row=extra_row,
        com_marcador=com_marcador,
    )


# ---------------------------------------------------------------------------
# Enviar card em um canal (não ligado a uma interação)
# ---------------------------------------------------------------------------


async def enviar_card_no_canal(
    canal: discord.abc.Messageable,
    titulo: str,
    linhas: list[str],
    cor: discord.Color = COR_INFO,
    extra_row: discord.ui.ActionRow | None = None,
    delay: int | None = None,
) -> discord.Message:
    """
    Envia um CardView direto em um canal.

    Útil para avisos públicos ou mensagens que não vêm de um clique de botão.
    Por padrão a mensagem NÃO some sozinha (delay=None).
    """
    view_do_card = CardView(
        titulo=titulo,
        linhas=linhas,
        cor=cor,
        timeout=None,
        extra_row=extra_row,
    )

    mensagem_enviada = await canal.send(view=view_do_card)

    if delay is not None:
        asyncio.create_task(excluir_mensagem(mensagem_enviada, delay=delay))

    return mensagem_enviada


# ---------------------------------------------------------------------------
# Apelidos em portugues exigidos pelo AGENTS.md
#
# Os nomes originais continuam existindo acima porque ja sao usados em varios
# arquivos. Estes apelidos apontam para a mesma funcao e sao os nomes que o
# AGENTS.md pede na secao 7.
# ---------------------------------------------------------------------------

enviar_no_canal = enviar_card_no_canal
