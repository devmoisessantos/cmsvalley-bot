"""Publicação idempotente do painel de promoção."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.config import CANAIS, GUILD_ID
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.promocoes.promocoes_views import PainelPromocaoLayout

registrador = logging.getLogger(__name__)


async def garantir_painel_promocao(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """
    Publica o painel de promocao no canal, se ele ainda nao existir.

    Confere primeiro na tabela de paineis postados. Se ja tem registro, sai sem
    fazer nada — e por isso a funcao se chama "garantir" e nao "criar": ela roda a
    cada vez que o bot liga, e postar outro painel a cada reinicio encheria o canal.

    Se o canal configurado nao for encontrado, registra o erro no log e desiste, sem
    derrubar a inicializacao do bot.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "promocao")
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_PAINEL_SOLICITAR_PROMOCAO") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            registrador.error(
                f"❌ CANAL_PAINEL_SOLICITAR_PROMOCAO não encontrado ({canal_id})."
            )
            return

        guilda = (
            interacao.guild
            if interacao and interacao.guild
            else bot.get_guild(int(GUILD_ID))
        )
        if guilda is None:
            registrador.error("❌ Guild não encontrada ao publicar painel de promoção.")
            return

        mensagem = await canal.send(view=PainelPromocaoLayout(guild=guilda))
        sessao.add(
            PainelPostado(
                nome_painel="promocao",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        registrador.info(f"✅ Painel de promoção postado em #{canal.name}.")
