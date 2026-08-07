# src/utils/mensagens.py
"""
Central de respostas ao usuário.

Toda mensagem rápida para o membro deve passar por aqui.
Usa Components V2 (LayoutView + Container + TextDisplay).
"""

import asyncio

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
    ):
        super().__init__(timeout=timeout)

        texto_das_linhas = "\n".join(f"`•` {linha}" for linha in linhas)

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
    except discord.NotFound:
        # A mensagem já foi apagada por outra pessoa ou pelo próprio bot.
        pass
    except discord.Forbidden:
        print("Sem permissão para excluir a mensagem")


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
    except discord.HTTPException:
        pass

    async def _destruir_as_duas_mensagens():
        await asyncio.sleep(delay)
        for mensagem_para_apagar in (mensagem_print, mensagem_aviso):
            if mensagem_para_apagar is None:
                continue
            try:
                await mensagem_para_apagar.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

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
    )


async def responder_erro(
    interacao: discord.Interaction,
    titulo: str,
    linhas: list[str],
    extra_row: discord.ui.ActionRow | None = None,
    delay: int | None = 15,
) -> discord.Message:
    """Responde com card vermelho de erro. Delay um pouco maior para dar tempo de ler."""
    return await enviar_card(
        interacao=interacao,
        titulo=titulo,
        linhas=linhas,
        cor=COR_ERRO,
        extra_row=extra_row,
        delay=delay,
    )


async def responder_aviso(
    interacao: discord.Interaction,
    titulo: str,
    linhas: list[str],
    extra_row: discord.ui.ActionRow | None = None,
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
    )


async def responder_info(
    interacao: discord.Interaction,
    titulo: str,
    linhas: list[str],
    extra_row: discord.ui.ActionRow | None = None,
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
    )


# ---------------------------------------------------------------------------
# Compatibilidade com código antigo (não remover)
# ---------------------------------------------------------------------------


async def responder_card(
    interaction: discord.Interaction,
    titulo: str,
    linhas: list[str],
    cor: discord.Color = COR_INFO,
    extra_row: discord.ui.ActionRow | None = None,
    delay: int | None = 10,
) -> discord.Message:
    """
    Atalho antigo — mantido para não quebrar imports existentes.

    Prefira responder_sucesso, responder_erro, responder_aviso ou responder_info
    em código novo. Este ainda funciona e agora também trata followup automaticamente.
    """
    return await enviar_card(
        interacao=interaction,
        titulo=titulo,
        linhas=linhas,
        cor=cor,
        extra_row=extra_row,
        delay=delay,
    )


async def responder_ephemera(
    interaction: discord.Interaction,
    content: str,
    view: discord.ui.View | None = None,
    delay: int = 10,
):
    """
    Resposta efêmera em texto simples (sem card).

    Mantida por compatibilidade. Em código novo, prefira os cards.
    Também trata response vs followup automaticamente.
    """
    interacao_ja_foi_respondida = interaction.response.is_done()

    if interacao_ja_foi_respondida:
        mensagem_enviada = await interaction.followup.send(
            content=content,
            view=view,
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            content=content,
            view=view,
            ephemeral=True,
        )
        mensagem_enviada = await interaction.original_response()

    asyncio.create_task(excluir_mensagem(mensagem_enviada, delay=delay))


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
