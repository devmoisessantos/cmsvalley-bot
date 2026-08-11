"""Publicação idempotente do painel de cursos."""

from __future__ import annotations

import discord
from sqlalchemy import select

from src.config import CANAIS, GUILD_ID
from src.cursos.cursos_views import PainelCursosLayout
from src.database.connection import async_session
from src.database.models import PainelPostado


async def garantir_painel_cursos(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "cursos")
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_PAINEL_SOLICITAR_CURSOS") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            print(f"❌ CANAL_PAINEL_SOLICITAR_CURSOS não encontrado ({canal_id}).")
            return

        guilda = (
            interacao.guild
            if interacao and interacao.guild
            else bot.get_guild(int(GUILD_ID))
        )
        if guilda is None:
            print("❌ Guild não encontrada ao publicar painel de cursos.")
            return

        mensagem = await canal.send(view=PainelCursosLayout(guild=guilda))
        sessao.add(
            PainelPostado(
                nome_painel="cursos",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        print(f"✅ Painel de cursos postado em #{canal.name}.")
