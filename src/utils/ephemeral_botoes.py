"""
Botões padrão para mensagens ephemeral — Fechar, Cancelar, Voltar.

Por que existe
--------------
Membros não usam "Ignorar mensagem" e ficam clicando de novo no painel,
gerando várias ephemerals iguais. O botão **Fechar** precisa apagar de
verdade a ephemeral (não só parar o loop / o timeout).

Como apaga
----------
Usa **sempre** o objeto da interação, nunca o objeto da mensagem:

    await interacao.response.defer()          # reconhece o clique
    await interacao.delete_original_response()  # apaga a ephemeral

Em interações de componente (botão/select), o token da interação trata a
mensagem em que o componente está como ``@original``. É o mesmo efeito
prático de "Ignorar mensagem".

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
        on_antes=lambda _: self._parar_loop_ao_vivo(),
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


async def apagar_ephemeral(
    interacao: discord.Interaction,
    *,
    mensagem: discord.Message | None = None,  # legado — ignorado
    delay: int = 0,  # legado — ignorado
) -> bool:
    """
    Apaga a ephemeral via interação (igual ao "Ignorar mensagem").

    Fluxo:
      1. Reconhece o clique (defer) — senão o Discord mostra "interação falhou".
      2. ``delete_original_response()`` — no token de componente, ``@original``
         é a mensagem em que o botão está.

    Não usa o objeto da mensagem. Os parâmetros ``mensagem`` e ``delay``
    existem só por compatibilidade com chamadas antigas e são ignorados.
    """
    # 1) Reconhece o clique
    if not interacao.response.is_done():
        try:
            await interacao.response.defer()
        except (discord.NotFound, discord.HTTPException) as erro_defer:
            registrador.debug(
                "apagar_ephemeral: defer falhou (%s)",
                erro_defer,
            )

    # 2) Apaga pela interação — não pelo objeto Message
    try:
        await interacao.delete_original_response()
        return True
    except discord.NotFound:
        # Já sumiu (timeout, outro clique, cliente ignorou)
        return True
    except (discord.Forbidden, discord.HTTPException) as erro:
        registrador.warning(
            "apagar_ephemeral: delete_original_response falhou: %s",
            erro,
        )
        return False


def _callback_fechar_ou_cancelar(
    *,
    on_antes: OnAntes | None,
) -> Callable[[discord.Interaction], Awaitable[None]]:
    async def _callback(interacao: discord.Interaction) -> None:
        if on_antes is not None:
            resultado = on_antes(interacao)
            if hasattr(resultado, "__await__"):
                await resultado
        await apagar_ephemeral(interacao)

    return _callback


def botao_fechar(
    *,
    get_mensagem: Any = None,  # legado — ignorado
    on_antes: OnAntes | None = None,
    label: str = "Fechar",
    emoji: str | None = "❌",
    custom_id: str | None = None,
    delay: int = 0,  # legado — ignorado
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
    botao.callback = _callback_fechar_ou_cancelar(on_antes=on_antes)
    return botao


def botao_cancelar(
    *,
    get_mensagem: Any = None,  # legado — ignorado
    on_antes: OnAntes | None = None,
    label: str = "Cancelar",
    emoji: str | None = "❌",
    custom_id: str | None = None,
    delay: int = 0,  # legado — ignorado
) -> discord.ui.Button:
    """Mesmo efeito de Fechar, rótulo de desistência de fluxo."""
    return botao_fechar(
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
    get_mensagem: Any = None,  # legado — ignorado
    on_antes: OnAntes | None = None,
    delay: int = 0,  # legado — ignorado
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
        row.add_item(botao_fechar(on_antes=on_antes))
    elif cancelar:
        row.add_item(botao_cancelar(on_antes=on_antes))
    return row
