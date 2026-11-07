# src/plantao/financeiro/carteira/carteira_logger.py
"""
Log visual de movimentações de moedas no canal LOG_CARTEIRA.

Ganho de plantão, troca in-game, ajuste staff, transferência e depósito
publicam aqui — separados do LOG_PLANTAO (toggle/call).
"""

from __future__ import annotations

import logging

import discord

from src.config import CANAIS
from src.utils.formatacao import formatar_dinheiro
from src.utils.log_container import LogContainerView

registrador = logging.getLogger(__name__)

TITULOS_POR_TIPO = {
    "GANHO_PLANTAO": ("💰 Ganho de plantão", discord.Color.green()),
    "TROCA_INGAME": ("💵 Troca por dinheiro in-game", discord.Color.gold()),
    "AJUSTE_STAFF": ("🛠️ Ajuste de saldo (staff)", discord.Color.orange()),
    "TRANSFERENCIA": ("↔️ Transferência entre membros", discord.Color.blurple()),
    "DEPOSITO": ("📥 Depósito", discord.Color.teal()),
    "SAQUE": ("📤 Saque", discord.Color.dark_grey()),
}


async def publicar_log_carteira(
    guild: discord.Guild,
    *,
    discord_id: int,
    tipo: str,
    valor: int,
    saldo_apos: int,
    outro_discord_id: int | None = None,
    referencia: str | None = None,
    id_fivem: str | None = None,
) -> None:
    """
    Publica um card de movimentação no canal LOG_CARTEIRA.

    Falha de envio não interrompe a operação financeira (já gravada no banco).
    """
    canal_id = int(CANAIS.get("LOG_CARTEIRA") or 0)
    if canal_id <= 0:
        return

    canal = guild.get_channel(canal_id)
    if canal is None:
        registrador.warning(
            "Canal LOG_CARTEIRA %s não encontrado na guilda %s",
            canal_id,
            guild.id,
        )
        return

    tipo_upper = (tipo or "").upper()
    titulo, cor = TITULOS_POR_TIPO.get(
        tipo_upper,
        (f"📋 Movimentação ({tipo_upper or '—'})", discord.Color.blurple()),
    )

    membro = guild.get_member(discord_id)
    mencao = membro.mention if membro else f"`{discord_id}`"

    sinal = f"{valor:+d}" if valor != 0 else "0"
    linhas = (
        f"- **Membro:** {mencao}\n"
        f"- **Discord ID:** `{discord_id}`\n"
        f"- **Tipo:** `{tipo_upper}`\n"
        f"- **Valor:** `{sinal}` moeda(s)\n"
        f"- **Saldo após:** `{int(saldo_apos)}`"
    )
    if id_fivem:
        linhas += f"\n- **ID FiveM:** `{id_fivem}`"
    if outro_discord_id:
        outro = guild.get_member(outro_discord_id)
        mencao_outro = outro.mention if outro else f"`{outro_discord_id}`"
        linhas += f"\n- **Contraparte:** {mencao_outro}"
    if referencia:
        linhas += f"\n- **Referência:** {referencia[:200]}"

    try:
        view = LogContainerView(
            titulo=titulo,
            linhas=linhas,
            guild=guild,
            cor=cor,
        )
        await canal.send(view=view)
    except Exception as erro_ao_publicar:
        registrador.exception(
            "Falha ao publicar log de carteira (%s %s): %s",
            tipo_upper,
            discord_id,
            erro_ao_publicar,
        )


async def publicar_credito_plantao(
    guild: discord.Guild,
    *,
    discord_id: int,
    quantidade: int,
    saldo_apos: int,
    id_fivem: str | None = None,
    referencia: str | None = None,
    cotacao: int | None = None,
) -> None:
    """Atalho para crédito de plantão no LOG_CARTEIRA."""
    texto_ref = referencia or f"+{quantidade} plantão"
    if cotacao and cotacao > 0:
        texto_ref = f"{texto_ref} · {formatar_dinheiro(cotacao)}/moeda"
    await publicar_log_carteira(
        guild,
        discord_id=discord_id,
        tipo="GANHO_PLANTAO",
        valor=int(quantidade),
        saldo_apos=int(saldo_apos),
        id_fivem=id_fivem,
        referencia=texto_ref,
    )
