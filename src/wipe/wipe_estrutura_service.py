"""
Recriação seletiva de canais de texto (limpar histórico).

Não apaga categorias, cargos nem canais não marcados.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from src.config import (
    ATRASO_WIPE_SEGUNDOS,
    CANAIS,
    CANAIS_PLANTAO,
)

registrador = logging.getLogger(__name__)


async def _pausar() -> None:
    await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)


def montar_mapa_chaves_config_por_id() -> dict[int, list[str]]:
    """Inverte CANAIS / CANAIS_PLANTAO: id → nomes exatos no config.py."""
    mapa: dict[int, list[str]] = {}

    for chave, valor in CANAIS.items():
        if isinstance(valor, int):
            mapa.setdefault(valor, []).append(chave)

    for chave, valor in CANAIS_PLANTAO.items():
        if isinstance(valor, int):
            mapa.setdefault(valor, []).append(f"CANAIS_PLANTAO.{chave}")
        elif isinstance(valor, list):
            for indice, id_canal in enumerate(valor):
                if isinstance(id_canal, int):
                    mapa.setdefault(id_canal, []).append(
                        f"CANAIS_PLANTAO.{chave}[{indice}]"
                    )
    return mapa


async def recriar_canal_de_texto(
    canal: discord.TextChannel,
) -> tuple[discord.TextChannel | None, str]:
    """Apaga um canal de texto e recria com as mesmas propriedades."""
    guilda = canal.guild
    nome = canal.name
    categoria = canal.category
    posicao = canal.position
    topico = canal.topic
    nsfw = canal.nsfw
    slowmode = canal.slowmode_delay
    overwrites = dict(canal.overwrites)
    id_antigo = canal.id

    try:
        await canal.delete(reason="Wipe — limpar histórico do canal")
    except discord.HTTPException as erro:
        mensagem = f"Falha ao apagar #{nome} ({id_antigo}): {erro}"
        registrador.warning("[wipe] %s", mensagem)
        return None, mensagem

    await _pausar()

    try:
        novo = await guilda.create_text_channel(
            name=nome,
            overwrites=overwrites,
            category=categoria,
            position=posicao,
            topic=topico,
            nsfw=nsfw,
            slowmode_delay=slowmode,
            reason="Wipe — canal recriado (histórico limpo)",
        )
    except discord.HTTPException as erro:
        mensagem = f"Falha ao recriar #{nome} (era {id_antigo}): {erro}"
        registrador.warning("[wipe] %s", mensagem)
        return None, mensagem

    await _pausar()
    return novo, f"Canal recriado: #{nome} {id_antigo} → {novo.id}"


async def recriar_canais_escolhidos(
    guilda: discord.Guild,
    ids_canais: set[int],
) -> tuple[list[str], dict[str, int], dict[int, int]]:
    """
    Recria só os canais de texto escolhidos.

    Devolve linhas, mapa config→novo_id, mapa id_antigo→id_novo.
    """
    linhas: list[str] = []
    mapa_config: dict[str, int] = {}
    mapa_ids: dict[int, int] = {}
    chaves_por_id = montar_mapa_chaves_config_por_id()

    for id_canal in sorted(ids_canais):
        canal = guilda.get_channel(id_canal)
        if canal is None:
            linhas.append(f"Canal {id_canal} não encontrado.")
            continue
        if not isinstance(canal, discord.TextChannel):
            linhas.append(f"Ignorado {id_canal}: não é texto ({type(canal).__name__}).")
            continue

        id_antigo = canal.id
        novo, linha = await recriar_canal_de_texto(canal)
        linhas.append(linha)
        if novo is None:
            continue

        mapa_ids[id_antigo] = novo.id
        for chave in chaves_por_id.get(id_antigo, []):
            mapa_config[chave] = novo.id
            linhas.append(f"Config `{chave}` → {novo.id}")

    return linhas, mapa_config, mapa_ids
