"""
Painéis visuais do dashboard no Discord (Components V2).

Nada de Embed clássico: LayoutView + Container + TextDisplay.
Os números vêm prontos do service; aqui só montamos o card.
"""

from __future__ import annotations

import discord

from src.dashboard.dashboard_templates import (
    montar_corpo_de_kpis,
    montar_corpo_de_status,
    montar_rodape_de_kpis,
    montar_titulo_de_kpis,
)
from src.utils.mensagens import COR_INFO, COR_SUCESSO


def montar_painel_de_kpis(
    metas: list[dict],
    *,
    ao_vivo: int | None = None,
    periodo_rotulo: str = "mês",
) -> discord.ui.LayoutView:
    """
    Card de KPIs/metas para o canal do dashboard.

    metas: lista no formato do overview da API
      (label, value, target, pct, color, inverse).
    """
    titulo = montar_titulo_de_kpis()
    corpo = montar_corpo_de_kpis(
        metas,
        ao_vivo=ao_vivo,
        periodo_rotulo=periodo_rotulo,
    )
    rodape = montar_rodape_de_kpis()

    container = discord.ui.Container(
        discord.ui.TextDisplay(titulo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(corpo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(rodape),
        accent_color=COR_SUCESSO,
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


def montar_painel_de_status(bot_info: dict) -> discord.ui.LayoutView:
    """Card técnico: online/offline, latência, cogs, versão."""
    online = bool(bot_info.get("online"))
    cor = COR_SUCESSO if online else discord.Color.red()
    titulo = "# Status do cmsvalley-bot\nHeartbeat e saúde da integração"
    corpo = montar_corpo_de_status(bot_info)
    rodape = montar_rodape_de_kpis("integração dashboard")

    container = discord.ui.Container(
        discord.ui.TextDisplay(titulo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(corpo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(rodape),
        accent_color=cor,
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


def montar_card_de_anuncio(
    titulo: str,
    descricao: str,
    *,
    campos: list[dict] | None = None,
    rodape: str | None = None,
    cor: discord.Color | None = None,
) -> discord.ui.LayoutView:
    """
    Card genérico para comandos send_embed vindos do site.

    Substitui o Embed clássico: título, descrição, campos opcionais.
    """
    partes = [f"# {titulo or 'Dashboard CMS Valley'}"]
    if descricao:
        partes.append(descricao)

    if campos:
        for campo in campos:
            nome = campo.get("name") or "—"
            valor = campo.get("value") or "—"
            partes.append(f"**{nome}**\n{valor}")

    corpo = "\n\n".join(partes)
    if rodape:
        corpo = f"{corpo}\n\n-# {rodape}"

    container = discord.ui.Container(
        discord.ui.TextDisplay(corpo),
        accent_color=cor or COR_INFO,
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view
