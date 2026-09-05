"""
Limpeza de cargos e prefixos no wipe de temporada.

Funções usadas pelo painel:
- limpar cargos da lista de preservação (fluxo clássico)
- remover cargos escolhidos no select de todos os membros
- listar quem seria preservado
"""

from __future__ import annotations

import asyncio
import logging

import discord

from src.config import (
    ATRASO_WIPE_SEGUNDOS,
    CARGO_BASE_APOS_WIPE,
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


def id_do_cargo_base() -> int | None:
    """ID do cargo HP S・Valley, se existir no config."""
    return CARGOS.get(CARGO_BASE_APOS_WIPE)


def membro_e_preservado(membro: discord.Member) -> bool:
    """True se o membro tem cargo da lista, ID fixo ou Administrator."""
    if membro.bot:
        return False
    if membro.id in IDS_PRESERVADOS_NO_WIPE:
        return True
    if membro.guild_permissions.administrator:
        return True
    ids_preservados = ids_dos_cargos_preservados()
    return any(cargo.id in ids_preservados for cargo in membro.roles)


def nomes_cargos_preservados_do_membro(membro: discord.Member) -> list[str]:
    """Nomes dos cargos de gestão que o membro tem agora."""
    nomes_ok = set(CARGOS_PRESERVADOS_NO_WIPE)
    return [cargo.name for cargo in membro.roles if cargo.name in nomes_ok]


def listar_preservados_e_comuns(
    guilda: discord.Guild,
) -> tuple[list[discord.Member], list[discord.Member]]:
    """Separa membros humanos em (preservados, comuns). Bots ficam de fora."""
    preservados: list[discord.Member] = []
    comuns: list[discord.Member] = []
    for membro in guilda.members:
        if membro.bot:
            continue
        if membro_e_preservado(membro):
            preservados.append(membro)
        else:
            comuns.append(membro)
    return preservados, comuns


def _cargos_que_podem_ser_removidos(
    membro: discord.Member,
    ids_para_manter: set[int],
) -> list[discord.Role]:
    """Cargos que o bot consegue tirar deste membro."""
    removiveis: list[discord.Role] = []
    for cargo in membro.roles:
        if cargo.is_default():
            continue
        if cargo.managed:
            continue
        if cargo.id in ids_para_manter:
            continue
        removiveis.append(cargo)
    return removiveis


async def _limpar_apelido_do_membro(
    membro: discord.Member,
    motivo: str,
) -> str | None:
    """
    Remove o apelido do servidor (volta só o username).

    Na virada de temporada o membro refaz whitelist com nome e ID novos.
    """
    if membro.nick is None:
        return None

    try:
        await membro.edit(nick=None, reason=motivo)
        return f"Apelido removido: {membro} ({membro.id}) → username `{membro.name}`"
    except discord.Forbidden:
        return f"Sem permissão para nick de {membro} ({membro.id})"
    except discord.HTTPException as erro:
        return f"Falha no nick de {membro.id}: {erro}"


async def limpar_cargos_e_prefixos(
    guilda: discord.Guild,
    motivo: str,
) -> tuple[int, int, int, list[str]]:
    """
    Remove cargos e prefixos (fluxo clássico da temporada).

    Preservados: mantêm cargos da lista + HP S・Valley + admin.
    Comuns: perdem todos os cargos removíveis.
    """
    preservados, comuns = listar_preservados_e_comuns(guilda)
    ids_preservados = ids_dos_cargos_preservados()
    id_base = id_do_cargo_base()
    cargo_base = guilda.get_role(id_base) if id_base else None

    contagem_preservados = 0
    contagem_limpos = 0
    contagem_falhas = 0
    linhas: list[str] = []

    for membro in preservados:
        ids_para_manter = set(ids_preservados)
        if id_base is not None:
            ids_para_manter.add(id_base)
        removiveis = _cargos_que_podem_ser_removidos(membro, ids_para_manter)
        try:
            if removiveis:
                await membro.remove_roles(*removiveis, reason=motivo)
            if cargo_base is not None and cargo_base not in membro.roles:
                await membro.add_roles(cargo_base, reason=motivo)
            contagem_preservados += 1
            nomes = nomes_cargos_preservados_do_membro(membro)
            linhas.append(
                f"Preservado: {membro} ({membro.id}) → {nomes or ['ok']}"
            )
        except (discord.Forbidden, discord.HTTPException) as erro:
            contagem_falhas += 1
            linhas.append(f"Falha preservado {membro.id}: {erro}")

        log_nick = await _limpar_apelido_do_membro(membro, motivo)
        if log_nick:
            linhas.append(log_nick)
        await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)

    for membro in comuns:
        removiveis = _cargos_que_podem_ser_removidos(membro, set())
        try:
            if removiveis:
                await membro.remove_roles(*removiveis, reason=motivo)
            contagem_limpos += 1
            linhas.append(
                f"Limpo: {membro} ({membro.id}) — {len(removiveis)} cargos"
            )
        except (discord.Forbidden, discord.HTTPException) as erro:
            contagem_falhas += 1
            linhas.append(f"Falha limpo {membro.id}: {erro}")

        log_nick = await _limpar_apelido_do_membro(membro, motivo)
        if log_nick:
            linhas.append(log_nick)
        await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)

    return contagem_preservados, contagem_limpos, contagem_falhas, linhas


async def remover_cargos_escolhidos_de_todos(
    guilda: discord.Guild,
    cargos: list[discord.Role],
    motivo: str,
    tambem_limpar_prefixo: bool = True,
) -> tuple[int, int, list[str]]:
    """
    Tira os cargos selecionados de todos os membros que os têm.

    Não mexe em bots. Não remove cargo managed.
    Devolve (membros_afetados, falhas, linhas).
    """
    cargos_uteis = [
        cargo
        for cargo in cargos
        if not cargo.is_default() and not cargo.managed
    ]
    if not cargos_uteis:
        return 0, 0, ["Nenhum cargo válido selecionado."]

    afetados = 0
    falhas = 0
    linhas: list[str] = []
    ids_alvo = {cargo.id for cargo in cargos_uteis}

    for membro in guilda.members:
        if membro.bot:
            continue
        para_tirar = [cargo for cargo in membro.roles if cargo.id in ids_alvo]
        if not para_tirar:
            continue
        try:
            await membro.remove_roles(*para_tirar, reason=motivo)
            afetados += 1
            nomes = ", ".join(cargo.name for cargo in para_tirar)
            linhas.append(f"Removido de {membro} ({membro.id}): {nomes}")
        except (discord.Forbidden, discord.HTTPException) as erro:
            falhas += 1
            linhas.append(f"Falha em {membro.id}: {erro}")

        if tambem_limpar_prefixo:
            log_nick = await _limpar_apelido_do_membro(membro, motivo)
            if log_nick:
                linhas.append(log_nick)

        await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)

    return afetados, falhas, linhas
