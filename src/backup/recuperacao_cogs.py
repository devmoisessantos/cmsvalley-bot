"""
Recuperação a partir dos canais de LOG.

Os comandos **não** ficam mais no grupo solto `/recuperar`.
Tudo foi centralizado em `/backup recuperar …` dentro de
`src/backup/backup_cogs.py`.

Este arquivo permanece só para não quebrar imports antigos e para
documentar a mudança. O setup não registra cog.
"""

from __future__ import annotations

import logging

from discord.ext import commands

logger = logging.getLogger(__name__)


async def setup(bot: commands.Bot):
    """
    Não registra mais o grupo /recuperar.

    Os subcomandos vivem em BackupCog.grupo_recuperar
    (`/backup recuperar …`).
    """
    logger.info(
        "recuperacao_cogs: comandos movidos para /backup recuperar — "
        "nada a registrar neste cog."
    )
