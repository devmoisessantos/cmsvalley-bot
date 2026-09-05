"""
Duplica canais de texto e só então apaga o original.

Ordem: criar cópia (mesmo nome, permissões, categoria, posição) → apagar antigo.
Assim, se a criação falhar, o canal original continua existindo.
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
    Duplica o canal e apaga o original depois que a cópia existir.

    Devolve (canal_novo ou None, linha). Sucesso no formato: NOME: ID
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
        novo = await guilda.create_text_channel(
            name=nome,
            overwrites=overwrites,
            category=categoria,
            position=posicao,
            topic=topico,
            nsfw=nsfw,
            slowmode_delay=slowmode,
            reason="Wipe — duplicar canal antes de apagar o original",
        )
    except discord.HTTPException as erro:
        mensagem = (
            f"Falha ao duplicar #{nome} ({id_antigo}): {erro} "
            "(original mantido)"
        )
        registrador.warning("[wipe] %s", mensagem)
        return None, mensagem

    await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)

    try:
        await canal.delete(
            reason="Wipe — original removido após duplicação concluída"
        )
    except discord.HTTPException as erro:
        mensagem = (
            f"Cópia criada #{novo.name} ({novo.id}), mas falha ao apagar "
            f"original #{nome} ({id_antigo}): {erro}"
        )
        registrador.warning("[wipe] %s", mensagem)
        return novo, mensagem

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
    Recria cada canal da lista (texto ou qualquer canal de texto).

    Sem exceção de canal: tenta todos os IDs informados.
    Devolve linhas (sucesso no formato NOME: ID).
    """
    linhas: list[str] = []
    vistos: set[int] = set()
    for id_canal in ids_canais:
        if id_canal in vistos:
            continue
        vistos.add(id_canal)
        canal = guilda.get_channel(id_canal)
        if canal is None:
            try:
                canal = await guilda.fetch_channel(id_canal)
            except (discord.NotFound, discord.HTTPException) as erro:
                linhas.append(f"Canal não encontrado: {id_canal} ({erro})")
                continue
        if not isinstance(canal, discord.TextChannel):
            linhas.append(
                f"Canal {id_canal} não é de texto "
                f"(tipo={type(canal).__name__}) — ignorado"
            )
            continue
        _novo, linha = await recriar_canal_de_texto(canal)
        linhas.append(linha)
        await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)
    return linhas
