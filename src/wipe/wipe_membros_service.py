"""
Quem fica e quem sai no wipe de temporada.

Diretoria (cargos preservados + IDs fixos), dono e bot ficam.
Todo o resto é expulso para refazer whitelist na temporada nova.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from src.config import (
    ATRASO_WIPE_SEGUNDOS,
    CARGOS,
    CARGOS_PRESERVADOS_NO_WIPE,
    IDS_PRESERVADOS_NO_WIPE,
)

registrador = logging.getLogger(__name__)


def ids_dos_cargos_preservados() -> set[int]:
    """IDs Discord dos cargos listados em CARGOS_PRESERVADOS_NO_WIPE."""
    ids: set[int] = set()
    for nome in CARGOS_PRESERVADOS_NO_WIPE:
        if nome in CARGOS:
            ids.add(CARGOS[nome])
    return ids


def membro_deve_ser_preservado(
    membro: discord.Member,
    guilda: discord.Guild,
) -> bool:
    """True se o membro não deve ser expulso no wipe."""
    if membro.bot:
        return True
    if membro.id == guilda.owner_id:
        return True
    if membro.id in IDS_PRESERVADOS_NO_WIPE:
        return True
    ids_preservados = ids_dos_cargos_preservados()
    return any(cargo.id in ids_preservados for cargo in membro.roles)


def nomes_cargos_de_gestao_do_membro(membro: discord.Member) -> list[str]:
    """Nomes dos cargos de gestão preservados que o membro tem agora."""
    nomes_ok = set(CARGOS_PRESERVADOS_NO_WIPE)
    return [cargo.name for cargo in membro.roles if cargo.name in nomes_ok]


def listar_preservados_e_expulsaveis(
    guilda: discord.Guild,
) -> tuple[list[discord.Member], list[discord.Member]]:
    """Separa membros em (preservados, a expulsar)."""
    preservados: list[discord.Member] = []
    expulsaveis: list[discord.Member] = []
    for membro in guilda.members:
        if membro_deve_ser_preservado(membro, guilda):
            preservados.append(membro)
        else:
            expulsaveis.append(membro)
    return preservados, expulsaveis


async def expulsar_membros_comuns(
    guilda: discord.Guild,
    motivo: str,
) -> tuple[int, int, list[str]]:
    """
    Expulsa todo mundo que não é preservado.

    Devolve (sucesso, falhas, linhas de log).
    """
    _preservados, expulsaveis = listar_preservados_e_expulsaveis(guilda)
    sucessos = 0
    falhas = 0
    linhas: list[str] = []

    for membro in expulsaveis:
        try:
            await membro.kick(reason=motivo)
            sucessos += 1
            linhas.append(f"Expulso: {membro} ({membro.id})")
        except discord.Forbidden:
            falhas += 1
            linhas.append(f"Sem permissão para expulsar: {membro} ({membro.id})")
            registrador.warning("[wipe] forbidden ao expulsar %s", membro.id)
        except discord.HTTPException as erro:
            falhas += 1
            linhas.append(f"Falha ao expulsar {membro.id}: {erro}")
            registrador.warning("[wipe] http ao expulsar %s: %s", membro.id, erro)
        await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)

    return sucessos, falhas, linhas
