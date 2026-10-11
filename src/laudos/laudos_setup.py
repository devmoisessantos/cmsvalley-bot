"""Publicação idempotente dos painéis de laudos e da prova de porte."""

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
from src.laudos.laudos_panel import PainelLaudosLayout
from src.laudos.prova_panel import PainelProvaLaudoLayout

registrador = logging.getLogger(__name__)


async def garantir_painel_laudos(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Garante o painel no CANAL_PAINEL_LAUDOS (não duplica se já houver registro)."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "laudos")
        )
        registro = resultado.scalar_one_or_none()
        if registro is not None:
            return

        canal = bot.get_channel(CANAIS.get("CANAL_PAINEL_LAUDOS", 0))
        if canal is None:
            registrador.error("❌ Canal CANAL_PAINEL_LAUDOS não encontrado.")
            return

        if interacao and interacao.guild:
            guilda = interacao.guild
        else:
            guilda = bot.get_guild(int(GUILD_ID))
        if guilda is None:
            registrador.error("❌ Guild não encontrada ao publicar painel de laudos.")
            return

        view = PainelLaudosLayout(guild=guilda)
        mensagem = await canal.send(view=view)
        sessao.add(
            PainelPostado(
                nome_painel="laudos",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        registrador.info(f"✅ Painel de Laudos postado no canal #{canal.name}.")


async def garantir_painel_prova_laudo(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """
    Garante o painel do paciente em CANAL_AVALIACAO_PSICOLOGICA.

    Se o id do canal ainda for 0 (não configurado), só registra aviso no log.
    """
    canal_id = CANAIS.get("CANAL_AVALIACAO_PSICOLOGICA") or 0
    if not canal_id:
        registrador.warning(
            "[laudos] CANAL_AVALIACAO_PSICOLOGICA=0 — painel da prova não postado."
        )
        return

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(
                PainelPostado.nome_painel == "laudos_prova"
            )
        )
        registro = resultado.scalar_one_or_none()
        if registro is not None:
            return

        canal = bot.get_channel(int(canal_id))
        if canal is None:
            registrador.error(
                "❌ Canal CANAL_AVALIACAO_PSICOLOGICA não encontrado."
            )
            return

        if interacao and interacao.guild:
            guilda = interacao.guild
        else:
            guilda = bot.get_guild(int(GUILD_ID))
        if guilda is None:
            registrador.error(
                "❌ Guild não encontrada ao publicar painel da prova de laudo."
            )
            return

        view = PainelProvaLaudoLayout(guild=guilda)
        mensagem = await canal.send(view=view)
        sessao.add(
            PainelPostado(
                nome_painel="laudos_prova",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await sessao.commit()
        registrador.info(
            f"✅ Painel da prova de laudo postado no canal #{canal.name}."
        )
