# src/guia/guia_setup.py
"""
Publicação idempotente dos painéis do domínio guia.

Cada função garante que o painel existe no canal configurado
e grava o registro em PainelPostado (não duplica se já existir).
"""

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
from src.guia.boas_vindas_panel import PainelBoasVindasLayout
from src.guia.tutoriais_panel import PainelTutoriaisLayout

registrador = logging.getLogger(__name__)


async def _resolver_guilda(
    bot: discord.Client,
    interacao: discord.Interaction | None,
) -> discord.Guild | None:
    """Obtém a guilda a partir da interação ou do bot."""
    if interacao is not None and interacao.guild is not None:
        return interacao.guild

    guilda = bot.get_guild(int(GUILD_ID))
    return guilda


async def _painel_ja_esta_registrado(nome_do_painel: str) -> bool:
    """Retorna True se já existe registro desse painel no banco."""
    async with async_session() as sessao:
        resultado_da_consulta = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == nome_do_painel)
        )
        registro = resultado_da_consulta.scalar_one_or_none()
        painel_ja_existe = registro is not None
        return painel_ja_existe


async def _salvar_registro_do_painel(
    nome_do_painel: str,
    canal_id: int,
    message_id: int,
) -> None:
    """Grava o painel postado na tabela PainelPostado."""
    async with async_session() as sessao:
        novo_registro = PainelPostado(
            nome_painel=nome_do_painel,
            canal_id=canal_id,
            message_id=message_id,
        )
        sessao.add(novo_registro)
        await sessao.commit()


async def garantir_painel_boas_vindas(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Garante o painel de boas-vindas (Guia — preparação inicial) no canal."""
    nome_do_painel = "boas_vindas"

    painel_ja_existe = await _painel_ja_esta_registrado(nome_do_painel)
    if painel_ja_existe:
        return

    canal_id = CANAIS.get("PAINEL_BOAS_VINDAS") or 0
    canal_esta_configurado = canal_id > 0
    if not canal_esta_configurado:
        registrador.warning(
            "⚠️ CANAIS['PAINEL_BOAS_VINDAS'] não configurado — painel não postado."
        )
        return

    canal = bot.get_channel(canal_id)
    if canal is None:
        registrador.error(f"❌ Canal de boas-vindas ({canal_id}) não encontrado.")
        return

    guilda = await _resolver_guilda(bot, interacao)
    if guilda is None:
        registrador.error("❌ Guild não encontrada!")
        return

    mensagem = await canal.send(view=PainelBoasVindasLayout(guilda))
    await _salvar_registro_do_painel(
        nome_do_painel=nome_do_painel,
        canal_id=canal.id,
        message_id=mensagem.id,
    )
    registrador.info(f"✅ Painel de Boas-Vindas postado no canal #{canal.name}.")


async def garantir_painel_tutoriais(
    bot: discord.Client,
    interacao: discord.Interaction | None = None,
) -> None:
    """Garante o painel de tutoriais no canal dedicado."""
    nome_do_painel = "tutoriais"

    painel_ja_existe = await _painel_ja_esta_registrado(nome_do_painel)
    if painel_ja_existe:
        return

    canal_id = CANAIS.get("PAINEL_TUTORIAIS") or CANAIS.get("GUIA_TUTORIAIS") or 0
    canal_esta_configurado = canal_id > 0
    if not canal_esta_configurado:
        registrador.warning(
            "⚠️ CANAIS['PAINEL_TUTORIAIS'] não configurado — painel não postado."
        )
        return

    canal = bot.get_channel(canal_id)
    if canal is None:
        registrador.error(f"❌ Canal de tutoriais ({canal_id}) não encontrado.")
        return

    guilda = await _resolver_guilda(bot, interacao)
    if guilda is None:
        registrador.error("❌ Guild não encontrada!")
        return

    mensagem = await canal.send(view=PainelTutoriaisLayout(guilda))
    await _salvar_registro_do_painel(
        nome_do_painel=nome_do_painel,
        canal_id=canal.id,
        message_id=mensagem.id,
    )
    registrador.info(f"✅ Painel de Tutoriais postado no canal #{canal.name}.")
