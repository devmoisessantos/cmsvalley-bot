"""
Apaga e recria canais de texto, devolvendo o novo ID.

Usado pelo painel de wipe quando o administrador escolhe um canal.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from src.config import ATRASO_WIPE_SEGUNDOS

registrador = logging.getLogger(__name__)


async def recriar_canal_de_texto(
    canal: discord.TextChannel,
) -> tuple[discord.TextChannel | None, str]:
    """
    Apaga o canal e recria com as mesmas propriedades.

    Devolve (canal_novo ou None, linha de relatório).
    Em sucesso a linha vem no formato: NOME: ID
    """
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
        await canal.delete(reason="Wipe — recriar canal (histórico limpo)")
    except discord.HTTPException as erro:
        mensagem = f"Falha ao apagar #{nome} ({id_antigo}): {erro}"
        registrador.warning("[wipe] %s", mensagem)
        return None, mensagem

    await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)

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
        mensagem = f"Falha ao recriar #{nome} (antigo {id_antigo}): {erro}"
        registrador.warning("[wipe] %s", mensagem)
        return None, mensagem

    linha = f"{novo.name}: {novo.id}"
    registrador.info(
        "[wipe] canal recriado %s (antigo=%s novo=%s)",
        novo.name,
        id_antigo,
        novo.id,
    )
    return novo, linha


async def recriar_canais_por_ids(
    guilda: discord.Guild,
    ids_canais: list[int],
) -> list[str]:
    """
    Recria cada canal de texto da lista.

    Devolve linhas de relatório (sucesso no formato NOME: ID).
    """
    linhas: list[str] = []
    for id_canal in ids_canais:
        canal = guilda.get_channel(id_canal)
        if canal is None or not isinstance(canal, discord.TextChannel):
            linhas.append(f"Canal não encontrado ou não é texto: {id_canal}")
            continue
        _novo, linha = await recriar_canal_de_texto(canal)
        linhas.append(linha)
        await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)
    return linhas
