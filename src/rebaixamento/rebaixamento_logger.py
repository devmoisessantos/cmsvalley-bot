"""Log visual de rebaixamento (canal público + log interno).

Canais:
  - CANAL_REBAIXADOS   → card público (Components V2)
  - LOG_REBAIXAMENTOS  → resumo interno mais simples
"""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)

import discord

from src.config import CANAIS

registrador = logging.getLogger(__name__)


def _canal(guild: discord.Guild, chave: str) -> discord.abc.Messageable | None:
    """Resolve um canal configurado; None se o id não existir ou sumiu."""
    canal_id = CANAIS.get(chave) or 0
    if not canal_id:
        return None
    canal = guild.get_channel(int(canal_id))
    if canal is None or not hasattr(canal, "send"):
        return None
    return canal  # type: ignore[return-value]


def _mencao_cargo(guild: discord.Guild, nome_cargo: str | None) -> str:
    """Devolve cargo.mention se o role existir; senão o nome em negrito."""
    if not nome_cargo:
        return "**—**"
    cargo = discord.utils.get(guild.roles, name=nome_cargo)
    if cargo is None:
        return f"**{nome_cargo}**"
    return cargo.mention


def _rodape(guild: discord.Guild) -> str:
    """Rodapé padrão: nome do servidor + data/hora Discord."""
    momento = int(datetime.now(timezone.utc).timestamp())
    return f"-# **{guild.name}** · <t:{momento}:f>"


def _view_rebaixamento_publico(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    cargo_de: str,
    cargo_para: str,
    motivo: str,
    dias_bloqueio: int,
) -> discord.ui.LayoutView:
    """Card do canal público de rebaixamento (com thumbnail do membro)."""
    mencao_de = _mencao_cargo(guild, cargo_de)
    mencao_para = _mencao_cargo(guild, cargo_para)
    corpo = (
        f"`👤` **Membro:** {alvo.mention} (`{alvo.id}`)\n"
        f"- **De:** {mencao_de} → **Para:** {mencao_para}\n"
        f"- **Motivo:** {motivo}\n"
        f"- **Bloqueio de promoção:** {dias_bloqueio} dias\n"
        f"`🛡️` **Responsável pelo rebaixamento:** {executor.mention}"
    )
    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("# ⬇️ Rebaixamento"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.Section(
                corpo,
                accessory=discord.ui.Thumbnail(alvo.display_avatar.url),
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(_rodape(guild)),
            accent_color=discord.Color.dark_red(),
        )
    )
    return layout


def _view_revogacao_publico(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    punicao_id: int,
    motivo: str,
    cargo_restaurado: str | None,
) -> discord.ui.LayoutView:
    """Card do canal público de revogação (com thumbnail do membro)."""
    mencao_cargo = _mencao_cargo(guild, cargo_restaurado)
    corpo = (
        f"`👤` **Membro:** {alvo.mention} (`{alvo.id}`)\n"
        f"- **Punição:** `#{punicao_id}`\n"
        f"- **Motivo:** {motivo}\n"
        f"- **Cargo restaurado:** {mencao_cargo}\n"
        f"`🛡️` **Responsável pela revogação:** {executor.mention}"
    )
    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("# ♻️ Revogação de rebaixamento"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.Section(
                corpo,
                accessory=discord.ui.Thumbnail(alvo.display_avatar.url),
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(_rodape(guild)),
            accent_color=discord.Color.dark_green(),
        )
    )
    return layout


def _view_log_interno(
    *,
    guild: discord.Guild,
    titulo: str,
    linhas: str,
    cor: discord.Color,
) -> discord.ui.LayoutView:
    """Card mais simples para LOG_REBAIXAMENTOS."""
    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {titulo}"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(linhas),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(_rodape(guild)),
            accent_color=cor,
        )
    )
    return layout


async def _enviar(
    canal: discord.abc.Messageable,
    view: discord.ui.LayoutView,
    *,
    chave: str,
) -> None:
    """Envia o card; falha cosméica só gera warning."""
    try:
        await canal.send(view=view)
    except (discord.Forbidden, discord.HTTPException) as erro:
        registrador.warning(
            "Falha ao publicar log de rebaixamento em %s: %s",
            chave,
            erro,
        )


async def publicar_log_rebaixamento(
    guild: discord.Guild,
    *,
    alvo: discord.Member,
    executor: discord.Member,
    cargo_de: str,
    cargo_para: str,
    motivo: str,
    dias_bloqueio: int,
    detalhe: str = "",
) -> None:
    """
    Publica o rebaixamento no canal público e no log interno.

    - CANAL_REBAIXADOS: card completo (formato pedido pela diretoria)
    - LOG_REBAIXAMENTOS: resumo mais simples
    """
    canal_publico = _canal(guild, "CANAL_REBAIXADOS")
    if canal_publico is not None:
        view_publico = _view_rebaixamento_publico(
            guild=guild,
            alvo=alvo,
            executor=executor,
            cargo_de=cargo_de,
            cargo_para=cargo_para,
            motivo=motivo,
            dias_bloqueio=dias_bloqueio,
        )
        await _enviar(canal_publico, view_publico, chave="CANAL_REBAIXADOS")
    else:
        registrador.warning(
            "Canal CANAL_REBAIXADOS não encontrado — log público omitido."
        )

    canal_log = _canal(guild, "LOG_REBAIXAMENTOS")
    if canal_log is not None:
        mencao_de = _mencao_cargo(guild, cargo_de)
        mencao_para = _mencao_cargo(guild, cargo_para)
        linhas = (
            f"- **Membro:** {alvo.mention} (`{alvo.id}`)\n"
            f"- **De:** {mencao_de} → **Para:** {mencao_para}\n"
            f"- **Bloqueio:** {dias_bloqueio} dias\n"
            f"- **Motivo:** {motivo}\n"
            f"- **Executor:** {executor.mention}"
        )
        if detalhe:
            linhas += f"\n- **Detalhe:** {detalhe}"
        view_log = _view_log_interno(
            guild=guild,
            titulo="⬇️ Rebaixamento",
            linhas=linhas,
            cor=discord.Color.dark_red(),
        )
        await _enviar(canal_log, view_log, chave="LOG_REBAIXAMENTOS")
    else:
        registrador.warning(
            "Canal LOG_REBAIXAMENTOS não encontrado — log interno omitido."
        )


async def publicar_log_revogacao_rebaixamento(
    guild: discord.Guild,
    *,
    alvo: discord.Member,
    executor: discord.Member,
    punicao_id: int,
    motivo: str,
    cargo_restaurado: str | None = None,
    detalhe: str = "",
) -> None:
    """
    Publica a revogação no canal público e no log interno.

    - CANAL_REBAIXADOS: card completo
    - LOG_REBAIXAMENTOS: resumo mais simples
    """
    canal_publico = _canal(guild, "CANAL_REBAIXADOS")
    if canal_publico is not None:
        view_publico = _view_revogacao_publico(
            guild=guild,
            alvo=alvo,
            executor=executor,
            punicao_id=punicao_id,
            motivo=motivo,
            cargo_restaurado=cargo_restaurado,
        )
        await _enviar(canal_publico, view_publico, chave="CANAL_REBAIXADOS")
    else:
        registrador.warning(
            "Canal CANAL_REBAIXADOS não encontrado — log público omitido."
        )

    canal_log = _canal(guild, "LOG_REBAIXAMENTOS")
    if canal_log is not None:
        mencao_cargo = _mencao_cargo(guild, cargo_restaurado)
        linhas = (
            f"- **Membro:** {alvo.mention} (`{alvo.id}`)\n"
            f"- **Punição:** `#{punicao_id}`\n"
            f"- **Cargo restaurado:** {mencao_cargo}\n"
            f"- **Motivo:** {motivo}\n"
            f"- **Executor:** {executor.mention}"
        )
        if detalhe:
            linhas += f"\n- **Detalhe:** {detalhe}"
        view_log = _view_log_interno(
            guild=guild,
            titulo="♻️ Revogação de rebaixamento",
            linhas=linhas,
            cor=discord.Color.dark_green(),
        )
        await _enviar(canal_log, view_log, chave="LOG_REBAIXAMENTOS")
    else:
        registrador.warning(
            "Canal LOG_REBAIXAMENTOS não encontrado — log interno omitido."
        )
