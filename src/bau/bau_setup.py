"""Publicação idempotente do painel de controle do baú."""

from __future__ import annotations

import discord
from sqlalchemy import select

from src.bau.bau_panel import PainelBauLayout
from src.config import (
    CANAIS,
    GUILD_ID,
)
from src.database.connection import async_session
from src.database.models import PainelPostado


async def garantir_painel_bau(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Garante o painel em CANAL_PAINEL_BAU (não duplica se já houver registro)."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "bau")
        )
        registro = resultado.scalar_one_or_none()
        if registro is not None:
            return

        canal_id = CANAIS.get("CANAL_PAINEL_BAU") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            print(
                "❌ Canal CANAL_PAINEL_BAU não encontrado. "
                f"Confira config CANAIS['CANAL_PAINEL_BAU']={canal_id}."
            )
            return

        if interacao and interacao.guild:
            guilda = interacao.guild
        else:
            guilda = bot.get_guild(int(GUILD_ID))
        if guilda is None:
            print("❌ Guild não encontrada ao publicar painel do baú.")
            return

        view = PainelBauLayout(guild=guilda)
        mensagem = await canal.send(view=view)
        sessao.add(
            PainelPostado(
                nome_painel="bau",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        print(f"✅ Painel de Controle do Baú postado no canal #{canal.name}.")
