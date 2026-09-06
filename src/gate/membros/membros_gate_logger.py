"""Logs visuais do ingresso e da gestão de membros GATE."""

from __future__ import annotations

import discord

from src.config import CANAIS
from src.utils.mensagens import COR_AVISO, COR_ERRO, COR_SUCESSO


async def publicar_no_canal(
    guild: discord.Guild,
    chave_canal: str,
    view: discord.ui.LayoutView,
) -> discord.Message | None:
    id_canal = CANAIS.get(chave_canal) or 0
    canal = guild.get_channel(id_canal) if id_canal else None
    if canal is None:
        return None
    return await canal.send(view=view)


def montar_card_texto(
    titulo: str,
    linhas: list[str],
    cor: discord.Color,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(
        discord.ui.TextDisplay(f"# {titulo}"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay("\n".join(linhas)),
        accent_color=cor,
    )
    view.add_item(container)
    return view


async def log_ingresso_aprovado(
    guild: discord.Guild,
    candidato: discord.Member,
    aprovador: discord.Member,
) -> None:
    view = montar_card_texto(
        "✅ Ingresso GATE aprovado",
        [
            f"**Membro:** {candidato.mention}",
            f"**Aprovado por:** {aprovador.mention}",
            f"**Cargo inicial:** Guardião",
        ],
        COR_SUCESSO,
    )
    await publicar_no_canal(guild, "LOG_GATE", view)
    await publicar_no_canal(guild, "CANAL_PROMOVIDOS_GATE", view)


async def log_ingresso_reprovado(
    guild: discord.Guild,
    candidato: discord.Member,
    reprovador: discord.Member,
    motivo: str,
) -> None:
    view = montar_card_texto(
        "❌ Ingresso GATE reprovado",
        [
            f"**Membro:** {candidato.mention}",
            f"**Reprovado por:** {reprovador.mention}",
            f"**Motivo:** {motivo}",
        ],
        COR_ERRO,
    )
    await publicar_no_canal(guild, "LOG_GATE", view)


async def log_promocao_gate(
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    detalhe: str,
) -> None:
    view = montar_card_texto(
        "⬆️ Promoção GATE",
        [
            f"**Membro:** {alvo.mention}",
            f"**Por:** {executor.mention}",
            detalhe,
        ],
        COR_SUCESSO,
    )
    await publicar_no_canal(guild, "LOG_GATE", view)
    await publicar_no_canal(guild, "CANAL_PROMOVIDOS_GATE", view)


async def log_rebaixamento_gate(
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    detalhe: str,
) -> None:
    view = montar_card_texto(
        "⬇️ Rebaixamento GATE",
        [
            f"**Membro:** {alvo.mention}",
            f"**Por:** {executor.mention}",
            detalhe,
        ],
        COR_AVISO,
    )
    await publicar_no_canal(guild, "LOG_GATE", view)
    await publicar_no_canal(guild, "CANAL_REBAIXADOS_GATE", view)


async def log_expulsao_gate(
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    detalhe: str,
) -> None:
    view = montar_card_texto(
        "🚪 Expulsão GATE",
        [
            f"**Membro:** {alvo.mention}",
            f"**Por:** {executor.mention}",
            detalhe,
        ],
        COR_ERRO,
    )
    await publicar_no_canal(guild, "LOG_GATE", view)
    await publicar_no_canal(guild, "CANAL_REBAIXADOS_GATE", view)
