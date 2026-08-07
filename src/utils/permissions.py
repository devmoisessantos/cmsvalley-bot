"""Checagens de permissão para slash commands."""

from __future__ import annotations

import discord
from discord import app_commands

from src.config import ADMIN_ROLE_NAMES


def is_authorized():
    """Administrador do Discord OU um dos cargos listados em ADMIN_ROLE_NAMES."""

    async def predicado(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True

        nomes_dos_cargos = {cargo.name for cargo in interaction.user.roles}
        if nomes_dos_cargos.intersection(ADMIN_ROLE_NAMES):
            return True

        await interaction.response.send_message(
            "❌ Você não tem permissão para usar este comando. "
            "É necessário ser Administrador ou ter um dos cargos autorizados.",
            ephemeral=True,
        )
        return False

    return app_commands.check(predicado)


def apenas_administrador():
    """Somente quem tem a permissão Administrator no servidor."""

    async def predicado(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True

        await interaction.response.send_message(
            "❌ Este comando é restrito a **Administradores** do servidor.",
            ephemeral=True,
        )
        return False

    return app_commands.check(predicado)
