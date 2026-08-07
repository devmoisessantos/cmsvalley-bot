# src/utils/logger.py
"""
Funções de log de auditoria (cargos e decisões).

Todas usam LogContainerView (Components V2).
"""

from datetime import (
    datetime,
    timezone,
)

import discord

from src.config import CANAIS
from src.utils.log_container import LogContainerView
from src.utils.mensagens import COR_INFO


async def log_cargo(
    guilda: discord.Guild,
    canal_id: int,
    *,
    candidato: discord.Member,
    executor: discord.abc.User,
    acao: str,
    cargo: str,
    extra: str = "",
):
    """
    Log simples de uma ação de cargo em um canal qualquer.

    Preferível usar log_mudanca_cargo ou log_decisao quando fizer sentido.
    """
    canal = guilda.get_channel(canal_id)
    if canal is None:
        return

    momento_atual = int(datetime.now(timezone.utc).timestamp())

    linhas = (
        f"**{acao}**\n"
        f"- **Membro:** {candidato.mention} (`{candidato.id}`)\n"
        f"- **Cargo:** {cargo}\n"
        f"- **Executor:** {executor.mention}\n"
        f"- **Data:** <t:{momento_atual}:F>"
    )
    if extra:
        linhas += f"\n- {extra}"

    view_do_log = LogContainerView(
        titulo="📋 Ação de Cargo",
        linhas=linhas,
        guild=guilda,
        cor=COR_INFO,
        avatar_url=candidato.display_avatar.url,
    )
    await canal.send(view=view_do_log)


async def log_mudanca_cargo(
    guilda: discord.Guild,
    *,
    candidato: discord.Member,
    executor: discord.abc.User,
    cargos_adicionados: list[str] | None = None,
    cargos_removidos: list[str] | None = None,
):
    """
    Auditoria de toda vez que o bot adiciona ou remove cargos de um membro.
    Envia no canal LOG_CARGOS.
    """
    canal = guilda.get_channel(CANAIS["LOG_CARGOS"])
    if canal is None:
        return

    partes = [f"- **Membro:** {candidato.mention} (`{candidato.id}`)"]

    if cargos_adicionados:
        lista_adicionados = ", ".join(cargos_adicionados)
        partes.append(f"- **Adicionados:** {lista_adicionados}")

    if cargos_removidos:
        lista_removidos = ", ".join(cargos_removidos)
        partes.append(f"- **Removidos:** {lista_removidos}")

    partes.append(f"- **Executor:** {executor.mention}")

    view_do_log = LogContainerView(
        titulo="🔧 Alteração de Cargo(s)",
        linhas="\n".join(partes),
        guild=guilda,
        cor=COR_INFO,
        avatar_url=candidato.display_avatar.url,
    )
    await canal.send(view=view_do_log)


async def log_decisao(
    guilda: discord.Guild,
    canal_id: int,
    *,
    titulo: str,
    candidato: discord.Member,
    executor: discord.abc.User,
    cargo: str,
    extra: str = "",
    cor: discord.Color = COR_INFO,
):
    """
    Log padronizado para aprovações e reprovações.
    """
    canal = guilda.get_channel(canal_id)
    if canal is None:
        return

    linhas = (
        f"- **Membro:** {candidato.mention} (`{candidato.id}`)\n"
        f"- **Cargo:** {cargo}\n"
        f"- **Executor:** {executor.mention}"
    )
    if extra:
        linhas += f"\n- {extra}"

    view_do_log = LogContainerView(
        titulo=titulo,
        linhas=linhas,
        guild=guilda,
        cor=cor,
        avatar_url=candidato.display_avatar.url,
    )
    await canal.send(view=view_do_log)
