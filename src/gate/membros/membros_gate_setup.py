"""Garante os painéis de ingresso e gestão GATE no Discord."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    GUILD_ID,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.gate.membros.membros_gate_panel import (
    PainelGerenciarGateLayout,
    PainelIngressarGateLayout,
)

registrador = logging.getLogger(__name__)


async def _garantir_painel(
    bot: discord.Client,
    nome_painel: str,
    chave_canal: str,
    montar_view,
    interaction: discord.Interaction | None = None,
) -> None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == nome_painel)
        )
        if resultado.scalar_one_or_none() is not None:
            return

        id_canal = CANAIS.get(chave_canal) or 0
        if not id_canal:
            registrador.warning(
                "Canal %s não configurado — painel %s não postado.",
                chave_canal,
                nome_painel,
            )
            return

        canal = bot.get_channel(id_canal)
        if canal is None:
            registrador.warning(
                "Canal %s (%s) não encontrado.", chave_canal, id_canal
            )
            return

        if interaction and interaction.guild:
            guild = interaction.guild
        else:
            guild = bot.get_guild(int(GUILD_ID))
        if guild is None:
            return

        mensagem = await canal.send(view=montar_view(guild=guild))
        sessao.add(
            PainelPostado(
                nome_painel=nome_painel,
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        registrador.info(
            "Painel %s postado em #%s.", nome_painel, getattr(canal, "name", "?")
        )


async def garantir_painel_ingressar_gate(
    bot: discord.Client, interaction: discord.Interaction = None
):
    await _garantir_painel(
        bot,
        "ingressar_gate",
        "CANAL_PAINEL_INGRESSAR_GATE",
        PainelIngressarGateLayout,
        interaction,
    )


async def garantir_painel_gerenciar_gate(
    bot: discord.Client, interaction: discord.Interaction = None
):
    await _garantir_painel(
        bot,
        "gerenciar_gate",
        "CANAL_PAINEL_GERENCIAR_GATE",
        PainelGerenciarGateLayout,
        interaction,
    )
