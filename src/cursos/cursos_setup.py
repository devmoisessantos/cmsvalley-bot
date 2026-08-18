"""Publicação idempotente do painel de cursos."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.config import CANAIS, GUILD_ID
from src.cursos.cursos_views import PainelCursosLayout
from src.database.conexao import async_session
from src.database.models import PainelPostado

registrador = logging.getLogger(__name__)


async def garantir_painel_cursos(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Publica o painel de cursos uma única vez e registra sua mensagem.

    Consulta a referência persistida antes de enviar o painel para impedir
    duplicatas após reinicializações. Usa a interação quando disponível para
    obter a guilda e grava no banco o canal e a mensagem recém-publicada.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "cursos")
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_PAINEL_SOLICITAR_CURSOS") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            registrador.error(
                f"❌ CANAL_PAINEL_SOLICITAR_CURSOS não encontrado ({canal_id})."
            )
            return

        guilda = (
            interacao.guild
            if interacao and interacao.guild
            else bot.get_guild(int(GUILD_ID))
        )
        if guilda is None:
            registrador.error("❌ Guild não encontrada ao publicar painel de cursos.")
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
        registrador.info(f"✅ Painel de cursos postado em #{canal.name}.")
