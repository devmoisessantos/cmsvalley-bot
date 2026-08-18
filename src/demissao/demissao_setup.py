# src/demissao/demissao_setup.py
"""Publicação idempotente do painel de demissão."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.config import CANAIS, GUILD_ID
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.demissao.demissao_panel import PainelDemissaoLayout

registrador = logging.getLogger(__name__)


async def garantir_painel_demissao(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Publica o painel de desligamento somente quando não há referência salva.

    A consulta ao banco torna a inicialização idempotente e impede painéis
    duplicados após reinícios. Quando consegue enviar o card, grava seu canal e
    identificador para que futuras execuções reconheçam a publicação existente.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "demissao")
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_PAINEL_DEMISSAO") or 0
        if not canal_id:
            registrador.warning("⚠️ CANAL_PAINEL_DEMISSAO ainda não configurado (0).")
            return

        canal = bot.get_channel(int(canal_id))
        if canal is None:
            registrador.error(f"❌ CANAL_PAINEL_DEMISSAO não encontrado ({canal_id}).")
            return

        guilda = (
            interacao.guild
            if interacao and interacao.guild
            else bot.get_guild(int(GUILD_ID))
        )
        if guilda is None:
            registrador.error("❌ Guild não encontrada ao publicar painel de demissão.")
            return

        mensagem = await canal.send(view=PainelDemissaoLayout(guilda=guilda))
        sessao.add(
            PainelPostado(
                nome_painel="demissao",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        registrador.info(
            f"✅ Painel de demissão postado em #{getattr(canal, 'name', canal.id)}."
        )
