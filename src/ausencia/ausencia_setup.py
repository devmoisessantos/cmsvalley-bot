# src/ausencia/ausencia_setup.py
"""Publicação idempotente do painel de ausência."""

from __future__ import annotations

import discord
from sqlalchemy import select

from src.ausencia.ausencia_panel import PainelAusenciaLayout
from src.config import (
    CANAIS,
    GUILD_ID,
)
from src.database.connection import async_session
from src.database.models import PainelPostado


async def garantir_painel_ausencia(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "ausencia")
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_REGISTRAR_AUSENCIA") or 0
        if not canal_id:
            print("⚠️ CANAL_REGISTRAR_AUSENCIA ainda não configurado (0).")
            return

        canal = bot.get_channel(int(canal_id))
        if canal is None:
            print(f"❌ CANAL_REGISTRAR_AUSENCIA não encontrado ({canal_id}).")
            return

        guilda = (
            interacao.guild
            if interacao and interacao.guild
            else bot.get_guild(int(GUILD_ID))
        )
        if guilda is None:
            print("❌ Guild não encontrada ao publicar painel de ausência.")
            return

        mensagem = await canal.send(view=PainelAusenciaLayout(guilda=guilda))
        sessao.add(
            PainelPostado(
                nome_painel="ausencia",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        print(f"✅ Painel de ausência postado em #{getattr(canal, 'name', canal.id)}.")
