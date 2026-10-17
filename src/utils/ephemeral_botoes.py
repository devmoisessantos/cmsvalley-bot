"""
Botões padrão para mensagens ephemeral — Fechar, Cancelar, Voltar.

Por que existe
--------------
Membros não usam "Ignorar mensagem" e ficam clicando de novo no painel,
gerando várias ephemerals iguais. O botão **Fechar** precisa apagar de
verdade a ephemeral (não só parar o loop / o timeout).

Discord trata ephemeral de followup e de response de formas diferentes.
``apagar_ephemeral`` tenta, nesta ordem:

1. mensagem conhecida (ex.: a que o view guardou ao postar)
2. ``interaction.message.delete()``
3. ``followup.delete_message(id)``
4. ``delete_original_response()``

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
        voltar=self._ao_voltar,  # opcional
        get_mensagem=lambda: self._mensagem_ao_vivo,
        on_antes=self._parar_loop_ao_vivo,
    )
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord

registrador = logging.getLogger(__name__)

OnAntes = Callable[[discord.Interaction], Awaitable[Any] | Any]
OnVoltar = Callable[[discord.Interaction], Awaitable[Any]]
GetMensagem = Callable[[], discord.Message | None]


async def apagar_ephemeral(
    interacao: discord.Interaction,
    *,
    mensagem: discord.Message | None = None,
) -> bool:
    """
    Apaga a mensagem ephemeral ligada ao clique.

    Devolve True se alguma tentativa de delete funcionou.
    Reconhece a interação (defer) se ainda não foi respondida — senão o
    Discord marca o clique como falho.
    """
    if not interacao.response.is_done():
        try:
            # defer sem ephemeral flag: só reconhece o clique no componente
            await interacao.response.defer()
        except (discord.NotFound, discord.HTTPException) as erro_defer:
            registrador.debug(
                "apagar_ephemeral: defer falhou (%s) — tenta apagar mesmo assim",
                erro_defer,
            )

    alvos: list[discord.Message | None] = []
    if mensagem is not None:
        alvos.append(mensagem)
    if interacao.message is not None and interacao.message is not mensagem:
        alvos.append(interacao.message)

    for alvo in alvos:
        if alvo is None:
            continue
        try:
            await alvo.delete()
            return True
        except discord.NotFound:
            return True  # já sumiu
        except (discord.Forbidden, discord.HTTPException) as erro_del:
            registrador.debug(
                "apagar_ephemeral: message.delete id=%s falhou: %s",
                getattr(alvo, "id", "?"),
                erro_del,
            )

    # Followup webhook (ephemeral de followup após defer no painel público)
    message_id = None
    if mensagem is not None:
        message_id = getattr(mensagem, "id", None)
    elif interacao.message is not None:
        message_id = interacao.message.id

    if message_id is not None:
        try:
            await interacao.followup.delete_message(int(message_id))
            return True
        except discord.NotFound:
            return True
        except (discord.Forbidden, discord.HTTPException) as erro_fu:
            registrador.debug(
                "apagar_ephemeral: followup.delete_message %s falhou: %s",
                message_id,
                erro_fu,
            )

    try:
        await interacao.delete_original_response()
        return True
    except discord.NotFound:
        return True
    except (discord.Forbidden, discord.HTTPException) as erro_orig:
        registrador.debug(
            "apagar_ephemeral: delete_original_response falhou: %s",
            erro_orig,
        )

    return False


def _callback_fechar_ou_cancelar(
    *,
    get_mensagem: GetMensagem | None,
    on_antes: OnAntes | None,
) -> Callable[[discord.Interaction], Awaitable[None]]:
    async def _callback(interacao: discord.Interaction) -> None:
        if on_antes is not None:
            resultado = on_antes(interacao)
            if hasattr(resultado, "__await__"):
                await resultado
        msg = get_mensagem() if get_mensagem is not None else None
        await apagar_ephemeral(interacao, mensagem=msg)

    return _callback


def botao_fechar(
    *,
    get_mensagem: GetMensagem | None = None,
    on_antes: OnAntes | None = None,
    label: str = "Fechar",
    emoji: str | None = "❌",
    custom_id: str | None = None,
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
    )
    return botao


def botao_cancelar(
    *,
    get_mensagem: GetMensagem | None = None,
    on_antes: OnAntes | None = None,
    label: str = "Cancelar",
    emoji: str | None = "❌",
    custom_id: str | None = None,
) -> discord.ui.Button:
    """Mesmo efeito de Fechar, rótulo de desistência de fluxo."""
    return botao_fechar(
        get_mensagem=get_mensagem,
        on_antes=on_antes,
        label=label,
        emoji=emoji,
        custom_id=custom_id,
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
            botao_fechar(get_mensagem=get_mensagem, on_antes=on_antes)
        )
    elif cancelar:
        row.add_item(
            botao_cancelar(get_mensagem=get_mensagem, on_antes=on_antes)
        )
    return row
