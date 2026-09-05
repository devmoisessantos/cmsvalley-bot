"""
Recuperação de emergência após limpar-cargos indevido.

1. Garante permissão Administrator no cargo RESPONSÁVEL GERAL.
2. Devolve esse cargo (+ HP S・Valley) ao ID fixo do responsável no on_ready.
"""

from __future__ import annotations

import logging

import discord

from src.config import (
    CARGO_BASE_APOS_WIPE,
    CARGOS,
)

registrador = logging.getLogger(__name__)

# ID do responsável a restaurar no on_ready (Guxta).
ID_MEMBRO_PARA_RESTAURAR = 859100649366356000

# Cargo que recebe Administrador e é devolvido ao ID acima.
NOME_CARGO_COM_ADMINISTRADOR = "👑 | RESPONSÁVEL GERAL"


async def garantir_administrador_no_responsavel_geral(
    guilda: discord.Guild,
) -> list[str]:
    """
    Coloca a permissão Administrator no cargo RESPONSÁVEL GERAL.

    Só altera se a permissão ainda não estiver ligada.
    """
    linhas: list[str] = []
    id_cargo = CARGOS.get(NOME_CARGO_COM_ADMINISTRADOR)
    if id_cargo is None:
        linhas.append(
            f"Cargo `{NOME_CARGO_COM_ADMINISTRADOR}` não está no config."
        )
        return linhas

    cargo = guilda.get_role(id_cargo)
    if cargo is None:
        linhas.append(
            f"Cargo `{NOME_CARGO_COM_ADMINISTRADOR}` "
            f"(id {id_cargo}) não existe no servidor."
        )
        return linhas

    if cargo.permissions.administrator:
        linhas.append(
            f"Cargo `{cargo.name}` já tem Administrador — nada a fazer."
        )
        return linhas

    try:
        novas_permissoes = cargo.permissions
        novas_permissoes.administrator = True
        await cargo.edit(
            permissions=novas_permissoes,
            reason=(
                "Wipe recuperação — Administrador no RESPONSÁVEL GERAL"
            ),
        )
        linhas.append(
            f"Administrador ligado no cargo `{cargo.name}` ({cargo.id})."
        )
    except discord.Forbidden:
        linhas.append(
            f"Sem permissão para editar o cargo `{cargo.name}` "
            "(bot precisa estar acima e ter Gerenciar Cargos)."
        )
    except discord.HTTPException as erro:
        linhas.append(f"Falha ao editar `{cargo.name}`: {erro}")

    return linhas


def _cargos_para_restaurar_no_responsavel(
    guilda: discord.Guild,
) -> list[discord.Role]:
    """
    Cargos mínimos para recuperar o acesso: RESPONSÁVEL GERAL + base.
    """
    nomes = [NOME_CARGO_COM_ADMINISTRADOR, CARGO_BASE_APOS_WIPE]
    encontrados: list[discord.Role] = []
    nomes_vistos: set[str] = set()
    for nome in nomes:
        if nome in nomes_vistos:
            continue
        nomes_vistos.add(nome)
        id_cargo = CARGOS.get(nome)
        if id_cargo is None:
            continue
        cargo = guilda.get_role(id_cargo)
        if cargo is not None:
            encontrados.append(cargo)
    return encontrados


async def restaurar_cargos_do_responsavel(
    guilda: discord.Guild,
    discord_id: int = ID_MEMBRO_PARA_RESTAURAR,
) -> list[str]:
    """
    Devolve RESPONSÁVEL GERAL + HP S・Valley ao membro informado.

    Usado no on_ready para recuperar quem perdeu admin no limpar-cargos.
    """
    linhas: list[str] = []
    membro = guilda.get_member(discord_id)
    if membro is None:
        try:
            membro = await guilda.fetch_member(discord_id)
        except discord.NotFound:
            linhas.append(
                f"Membro {discord_id} não está no servidor — "
                "não restaurei cargos."
            )
            return linhas
        except discord.HTTPException as erro:
            linhas.append(f"Falha ao buscar membro {discord_id}: {erro}")
            return linhas

    cargos_desejados = _cargos_para_restaurar_no_responsavel(guilda)
    if not cargos_desejados:
        linhas.append(
            "Nenhum cargo de recuperação encontrado na guilda "
            f"({NOME_CARGO_COM_ADMINISTRADOR} / {CARGO_BASE_APOS_WIPE})."
        )
        return linhas

    faltando = [
        cargo for cargo in cargos_desejados if cargo not in membro.roles
    ]
    if not faltando:
        linhas.append(
            f"{membro} ({discord_id}) já tem os cargos de recuperação — ok."
        )
        return linhas

    try:
        await membro.add_roles(
            *faltando,
            reason="Wipe recuperação — restaurar cargos do responsável",
        )
        nomes = ", ".join(cargo.name for cargo in faltando)
        linhas.append(
            f"Cargos restaurados em {membro} ({discord_id}): {nomes}"
        )
    except discord.Forbidden:
        linhas.append(
            f"Sem permissão para dar cargos a {membro} ({discord_id})."
        )
    except discord.HTTPException as erro:
        linhas.append(f"Falha ao restaurar cargos de {discord_id}: {erro}")

    return linhas


async def executar_recuperacao_no_ready(guilda: discord.Guild) -> list[str]:
    """
    Sequência completa chamada no on_ready.

    1. Administrador no cargo RESPONSÁVEL GERAL
    2. Restaurar esse cargo (+ base) no ID fixo
    """
    linhas: list[str] = []
    linhas.extend(
        await garantir_administrador_no_responsavel_geral(guilda)
    )
    linhas.extend(await restaurar_cargos_do_responsavel(guilda))
    for linha in linhas:
        registrador.info("[wipe-recuperacao] %s", linha)
    return linhas
