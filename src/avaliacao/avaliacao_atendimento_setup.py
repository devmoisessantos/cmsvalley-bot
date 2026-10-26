"""Publicação idempotente do painel de avaliação de atendimento."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.avaliacao.avaliacao_atendimento_panel import (
    PainelAvaliarAtendimentoLayout,
)
from src.config import CANAIS, GUILD_ID
from src.database.conexao import async_session
from src.database.models import PainelPostado

registrador = logging.getLogger(__name__)

NOME_PAINEL = "avaliar_atendimento"


async def garantir_painel_avaliar_atendimento(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Publica o painel fixo só quando ainda não há referência salva.

    A consulta ao banco evita painéis duplicados após reinícios. Quando
    consegue enviar o card, grava canal e message_id para as próximas
    subidas reconhecerem a publicação existente.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == NOME_PAINEL)
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_AVALIAR_ATENDIMENTO") or 0
        if not canal_id:
            registrador.warning(
                "CANAL_AVALIAR_ATENDIMENTO ainda não configurado (0)."
            )
            return

        canal = bot.get_channel(int(canal_id))
        if canal is None:
            registrador.error(
                "CANAL_AVALIAR_ATENDIMENTO não encontrado (%s).",
                canal_id,
            )
            return

        guilda = (
            interacao.guild
            if interacao and interacao.guild
            else bot.get_guild(int(GUILD_ID))
        )
        if guilda is None:
            registrador.error(
                "Guild não encontrada ao publicar painel de avaliar atendimento."
            )
            return

        mensagem = await canal.send(
            view=PainelAvaliarAtendimentoLayout(guilda=guilda)
        )
        sessao.add(
            PainelPostado(
                nome_painel=NOME_PAINEL,
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        registrador.info(
            "Painel de avaliar atendimento postado em #%s.",
            getattr(canal, "name", canal.id),
        )
