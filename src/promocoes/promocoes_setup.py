"""Publicação idempotente do painel de promoção."""

from __future__ import annotations

import discord
from sqlalchemy import select

from src.config import CANAIS, GUILD_ID
from src.database.connection import async_session
from src.database.models import PainelPostado
from src.promocoes.promocoes_views import PainelPromocaoLayout


async def garantir_painel_promocao(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "promocao")
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_PAINEL_SOLICITAR_PROMOCAO") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            print(f"❌ CANAL_PAINEL_SOLICITAR_PROMOCAO não encontrado ({canal_id}).")
            return

        guilda = (
            interacao.guild
            if interacao and interacao.guild
            else bot.get_guild(int(GUILD_ID))
        )
        if guilda is None:
            print("❌ Guild não encontrada ao publicar painel de promoção.")
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
        print(f"✅ Painel de promoção postado em #{canal.name}.")
