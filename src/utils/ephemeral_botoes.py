"""
Botões padrão para mensagens ephemeral — Fechar, Cancelar, Voltar.

Por que existe
--------------
Membros não usam "Ignorar mensagem" e ficam clicando de novo no painel,
gerando várias ephemerals iguais. O botão **Fechar** precisa apagar de
verdade a ephemeral (não só parar o loop / o timeout).

Como apaga
----------
Usa ``excluir_mensagem`` (já provado no projeto: delay do Ver Informações,
followups, etc.). No clique: reconhece a interação e agenda delete com
``delay=0`` (ou 1s se preferir) na mensagem conhecida.

Uso típico
----------
::

    from src.utils.ephemeral_botoes import (
        apagar_ephemeral,
        botao_fechar,
        botao_voltar,
        linha_ephemeral,
    )

    row = linha_ephemeral(
        fechar=True,
        on_voltar=self._ao_voltar,  # opcional
        get_mensagem=lambda: self._mensagem_ao_vivo,
        on_antes=lambda _: self._parar_loop_ao_vivo(),
    )
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord

from src.utils.mensagens import excluir_mensagem

registrador = logging.getLogger(__name__)

OnAntes = Callable[[discord.Interaction], Awaitable[Any] | Any]
OnVoltar = Callable[[discord.Interaction], Awaitable[Any]]
GetMensagem = Callable[[], discord.Message | None]

# Após o clique: 1s e apaga (mesmo caminho do auto-delete que já funciona).
DELAY_APAGAR_AO_FECHAR = 1


async def apagar_ephemeral(
    interacao: discord.Interaction,
    *,
    mensagem: discord.Message | None = None,
    delay: int = DELAY_APAGAR_AO_FECHAR,
) -> bool:
    """
    Reconhece o clique e apaga a ephemeral via ``excluir_mensagem``.

    Mesmo caminho do auto-delete que já funciona (Ver Informações, etc.).
    Devolve True se conseguiu agendar a exclusão.
    """
    import asyncio

    # 1) Reconhece o clique — senão o Discord mostra "interação falhou"
    if not interacao.response.is_done():
        try:
            await interacao.response.defer()
        except (discord.NotFound, discord.HTTPException) as erro_defer:
            registrador.debug(
                "apagar_ephemeral: defer falhou (%s)",
                erro_defer,
            )

    # 2) Mensagem do botão (interaction.message) tem prioridade — é a
    #    ephemeral em que o membro clicou. Fallback: ref guardada no view.
    alvo = interacao.message if interacao.message is not None else mensagem

    if alvo is None:
        try:
            await interacao.delete_original_response()
            return True
        except discord.NotFound:
            return True
        except (discord.Forbidden, discord.HTTPException) as erro_orig:
            registrador.warning(
                "apagar_ephemeral: sem mensagem e delete_original falhou: %s",
                erro_orig,
            )
            return False

    # 3) Agenda no event loop (igual ao auto-delete do Ver Informações).
    #    Não bloqueia o callback com await sleep — o create_task é o mesmo
    #    padrão que já apaga a ephemeral após DELAY_INFORMACOES_SEGUNDOS.
    try:
        asyncio.create_task(excluir_mensagem(alvo, delay=delay))
        return True
    except Exception as erro_excluir:
        registrador.warning(
            "apagar_ephemeral: falha ao agendar excluir_mensagem id=%s: %s",
            getattr(alvo, "id", "?"),
            erro_excluir,
        )
        return False


def _callback_fechar_ou_cancelar(
    *,
    get_mensagem: GetMensagem | None,
    on_antes: OnAntes | None,
    delay: int,
) -> Callable[[discord.Interaction], Awaitable[None]]:
    async def _callback(interacao: discord.Interaction) -> None:
        if on_antes is not None:
            resultado = on_antes(interacao)
            if hasattr(resultado, "__await__"):
                await resultado
        msg = get_mensagem() if get_mensagem is not None else None
        await apagar_ephemeral(interacao, mensagem=msg, delay=delay)

    return _callback


def botao_fechar(
    *,
    get_mensagem: GetMensagem | None = None,
    on_antes: OnAntes | None = None,
    label: str = "Fechar",
    emoji: str | None = "❌",
    custom_id: str | None = None,
    delay: int = DELAY_APAGAR_AO_FECHAR,
) -> discord.ui.Button:
    """Botão que apaga a ephemeral (equivalente a 'Ignorar mensagem')."""
    kwargs: dict[str, Any] = {
        "label": label,
        "style": discord.ButtonStyle.secondary,
    }
    if emoji:
        kwargs["emoji"] = emoji
    if custom_id:
        kwargs["custom_id"] = custom_id
    botao = discord.ui.Button(**kwargs)
    botao.callback = _callback_fechar_ou_cancelar(
        get_mensagem=get_mensagem,
        on_antes=on_antes,
        delay=delay,
    )
    return botao


def botao_cancelar(
    *,
    get_mensagem: GetMensagem | None = None,
    on_antes: OnAntes | None = None,
    label: str = "Cancelar",
    emoji: str | None = "❌",
    custom_id: str | None = None,
    delay: int = DELAY_APAGAR_AO_FECHAR,
) -> discord.ui.Button:
    """Mesmo efeito de Fechar, rótulo de desistência de fluxo."""
    return botao_fechar(
        get_mensagem=get_mensagem,
        on_antes=on_antes,
        label=label,
        emoji=emoji,
        custom_id=custom_id,
        delay=delay,
    )


def botao_voltar(
    on_voltar: OnVoltar,
    *,
    label: str = "Voltar",
    emoji: str | None = "◀️",
    custom_id: str | None = None,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
) -> discord.ui.Button:
    """
    Botão Voltar: chama ``on_voltar(interacao)``.

    Quem implementa o callback decide se edita a ephemeral com o card
    anterior ou se apaga e abre outro fluxo.
    """
    kwargs: dict[str, Any] = {
        "label": label,
        "style": style,
    }
    if emoji:
        kwargs["emoji"] = emoji
    if custom_id:
        kwargs["custom_id"] = custom_id
    botao = discord.ui.Button(**kwargs)

    async def _callback(interacao: discord.Interaction) -> None:
        await on_voltar(interacao)

    botao.callback = _callback
    return botao


def linha_ephemeral(
    *,
    fechar: bool = True,
    cancelar: bool = False,
    on_voltar: OnVoltar | None = None,
    get_mensagem: GetMensagem | None = None,
    on_antes: OnAntes | None = None,
    delay: int = DELAY_APAGAR_AO_FECHAR,
) -> discord.ui.ActionRow:
    """
    Monta uma ActionRow com Fechar / Cancelar / Voltar conforme flags.

    - ``fechar`` e ``cancelar`` juntos: só um botão de saída (prioriza Fechar).
    - ``on_voltar``: adiciona Voltar à esquerda da saída.
    """
    row = discord.ui.ActionRow()
    if on_voltar is not None:
        row.add_item(botao_voltar(on_voltar))
    if fechar:
        row.add_item(
            botao_fechar(
                get_mensagem=get_mensagem,
                on_antes=on_antes,
                delay=delay,
            )
        )
    elif cancelar:
        row.add_item(
            botao_cancelar(
                get_mensagem=get_mensagem,
                on_antes=on_antes,
                delay=delay,
            )
        )
    return row
