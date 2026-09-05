"""
Limpeza de cargos e prefixos no wipe de temporada.

Não expulsa ninguém. Remove cargos e prefixo do nick.

Quem tem cargo em CARGOS_PRESERVADOS_NO_WIPE mantém esses cargos
e o cargo base HP S・Valley. Todo o resto perde todos os cargos.
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
)
from src.utils.nickname import remover_prefixo_existente

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
    """
    True se o membro não deve perder cargos de gestão.

    Protege: cargos da lista, permissão Administrator nativa e IDs
    extras em IDS_PRESERVADOS_NO_WIPE (quando configurados no .env).
    """
    if membro.bot:
        return False
    if membro.guild_permissions.administrator:
        return True
    from src.config import IDS_PRESERVADOS_NO_WIPE

    if membro.id in IDS_PRESERVADOS_NO_WIPE:
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
    """
    Separa membros humanos em (preservados, comuns).

    Bots ficam de fora das duas listas.
    """
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
    """
    Cargos que o bot consegue tirar deste membro.

    Ignora @everyone, cargos gerenciados por integração e o que deve ficar.
    """
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


async def _limpar_prefixo_do_membro(
    membro: discord.Member,
    motivo: str,
) -> str | None:
    """
    Tira o prefixo do nick. Devolve texto de log ou None se não mudou.
    """
    nick_atual = membro.nick or membro.display_name
    nick_limpo = remover_prefixo_existente(nick_atual)[:32]

    # Se o nick no servidor já está limpo (ou é o username sem prefixo), pula.
    if membro.nick is None and nick_limpo == membro.name:
        return None
    if membro.nick is not None and membro.nick == nick_limpo:
        return None

    try:
        await membro.edit(nick=nick_limpo if nick_limpo else None, reason=motivo)
        return f"Prefixo removido: {membro} → `{nick_limpo or membro.name}`"
    except discord.Forbidden:
        return f"Sem permissão para nick de {membro} ({membro.id})"
    except discord.HTTPException as erro:
        return f"Falha no nick de {membro.id}: {erro}"


async def limpar_cargos_e_prefixos(
    guilda: discord.Guild,
    motivo: str,
) -> tuple[int, int, int, list[str]]:
    """
    Remove cargos e prefixos de todos os membros humanos.

    Preservados: mantêm cargos da lista + HP S・Valley.
    Comuns: perdem todos os cargos removíveis.

    Devolve (preservados, limpos, falhas, linhas de log).
    """
    preservados, comuns = listar_preservados_e_comuns(guilda)
    ids_preservados = ids_dos_cargos_preservados()
    id_base = id_do_cargo_base()
    cargo_base = guilda.get_role(id_base) if id_base else None

    contagem_preservados = 0
    contagem_limpos = 0
    contagem_falhas = 0
    linhas: list[str] = []

    # --- Diretoria / responsáveis ---
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
                f"Preservado: {membro} ({membro.id}) → {nomes or ['(cargos da lista)']}"
            )
        except discord.Forbidden:
            contagem_falhas += 1
            linhas.append(f"Sem permissão (preservado): {membro} ({membro.id})")
            registrador.warning("[wipe] forbidden preservado %s", membro.id)
        except discord.HTTPException as erro:
            contagem_falhas += 1
            linhas.append(f"Falha preservado {membro.id}: {erro}")
            registrador.warning("[wipe] http preservado %s: %s", membro.id, erro)

        log_nick = await _limpar_prefixo_do_membro(membro, motivo)
        if log_nick:
            linhas.append(log_nick)

        await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)

    # --- Membros comuns ---
    for membro in comuns:
        removiveis = _cargos_que_podem_ser_removidos(membro, set())
        try:
            if removiveis:
                await membro.remove_roles(*removiveis, reason=motivo)
            contagem_limpos += 1
            linhas.append(f"Limpo: {membro} ({membro.id}) — {len(removiveis)} cargos")
        except discord.Forbidden:
            contagem_falhas += 1
            linhas.append(f"Sem permissão (limpo): {membro} ({membro.id})")
            registrador.warning("[wipe] forbidden limpo %s", membro.id)
        except discord.HTTPException as erro:
            contagem_falhas += 1
            linhas.append(f"Falha limpo {membro.id}: {erro}")
            registrador.warning("[wipe] http limpo %s: %s", membro.id, erro)

        log_nick = await _limpar_prefixo_do_membro(membro, motivo)
        if log_nick:
            linhas.append(log_nick)

        await asyncio.sleep(ATRASO_WIPE_SEGUNDOS)

    return contagem_preservados, contagem_limpos, contagem_falhas, linhas
