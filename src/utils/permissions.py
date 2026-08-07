# src/utils/permissions.py
"""
Checagens de permissão para comandos de barra (slash commands).
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.config import ADMIN_ROLE_NAMES
from src.utils.mensagens import responder_erro


def esta_autorizado():
    """
    Libera o comando se o membro for Administrador do Discord
    ou tiver um dos cargos listados em ADMIN_ROLE_NAMES.
    """

    async def predicado(interacao: discord.Interaction) -> bool:
        membro = interacao.user

        if membro.guild_permissions.administrator:
            return True

        nomes_dos_cargos_do_membro = {cargo.name for cargo in membro.roles}
        tem_cargo_autorizado = bool(
            nomes_dos_cargos_do_membro.intersection(ADMIN_ROLE_NAMES)
        )

        if tem_cargo_autorizado:
            return True

        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=[
                "Você não tem permissão para usar este comando.",
                "É necessário ser Administrador ou ter um dos cargos autorizados.",
            ],
        )
        return False

    return app_commands.check(predicado)


# Nome antigo mantido para não quebrar imports existentes.
# Em código novo, use esta_autorizado.
is_authorized = esta_autorizado


def apenas_administrador():
    """Libera o comando somente para quem tem a permissão Administrator."""

    async def predicado(interacao: discord.Interaction) -> bool:
        membro = interacao.user

        if membro.guild_permissions.administrator:
            return True

        await responder_erro(
            interacao,
            titulo="Somente administradores",
            linhas=[
                "Este comando é restrito a **Administradores** do servidor.",
            ],
        )
        return False

    return app_commands.check(predicado)
