"""
Domínio wipe sem comandos de barra.

O controle fica só no painel persistente (PainelWipeLayout).
Este cog existe apenas para manter o listener de recuperação no join
carregado junto do domínio, se necessário no futuro.

Comandos /wipe e /moderacao wipe* foram removidos de propósito.
"""

from __future__ import annotations

import logging

from discord.ext import commands

registrador = logging.getLogger(__name__)


class WipeCog(commands.Cog):
    """Placeholder do domínio wipe (sem slash commands)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot) -> None:
    """Registra o cog vazio (painel sobe pelo bot.py + wipe_setup)."""
    await bot.add_cog(WipeCog(bot))
    registrador.info("WipeCog registrado (sem comandos de barra).")
