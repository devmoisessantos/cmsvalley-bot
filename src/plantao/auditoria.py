"""Logs de auditoria para ações admin de plantão / membros."""

from __future__ import annotations

import discord

from src.config import CANAIS
from src.utils.log_container import LogContainerView


async def registrar_auditoria_admin(
    guild: discord.Guild,
    *,
    executor: discord.Member | discord.User,
    alvo: discord.Member | discord.User | None,
    acao: str,
    detalhes: str | None = None,
    cor: discord.Color | None = None,
) -> None:
    """Posta no canal LOG_AUDITORIA_ADMIN um registro V2."""
    canal_id = CANAIS.get("LOG_AUDITORIA_ADMIN")
    canal = guild.get_channel(canal_id) if canal_id else None
    if canal is None:
        return

    linhas = f"- **Executor:** {executor.mention} (`{executor.id}`)\n- **Ação:** {acao}"
    if alvo is not None:
        linhas += f"\n- **Alvo:** {alvo.mention} (`{alvo.id}`)"
    if detalhes:
        linhas += f"\n- **Detalhes:** {detalhes}"

    view = LogContainerView(
        titulo="🔎 Auditoria Admin — Gerenciar Membros",
        linhas=linhas,
        guild=guild,
        cor=cor or discord.Color.dark_gold(),
        avatar_url=getattr(executor, "display_avatar", None)
        and executor.display_avatar.url,
    )
    await canal.send(view=view)
