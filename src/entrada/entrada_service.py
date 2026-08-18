"""
Regras de negócio da entrada e saída de membros.

Garante a linha na tabela ``usuarios`` na entrada e publica os cards
nos canais de boas-vindas e adeus configurados em ``CANAIS``.
"""

from __future__ import annotations

import logging

import discord
from sqlalchemy.exc import SQLAlchemyError

from src.config import CANAIS
from src.entrada.entrada_panel import (
    montar_card_adeus,
    montar_card_boas_vindas,
)
from src.membros.sincronizar_usuarios_service import garantir_usuario_basico

registrador = logging.getLogger(__name__)


async def processar_entrada_do_membro(membro: discord.Member) -> None:
    """
    Trata a chegada de um membro no servidor.

    1. Ignora bots.
    2. Garante a linha mínima em ``usuarios`` (ligação direta com a tabela).
    3. Publica o card de boas-vindas no canal configurado.
    """
    if membro.bot:
        return

    try:
        await garantir_usuario_basico(membro)
    except SQLAlchemyError as erro_do_banco:
        registrador.exception(
            "Falha ao garantir usuario na entrada de %s: %s",
            membro.id,
            erro_do_banco,
        )
    except Exception as erro_inesperado:
        registrador.exception(
            "Erro inesperado ao garantir usuario na entrada de %s: %s",
            membro.id,
            erro_inesperado,
        )

    await _publicar_boas_vindas(membro)


async def processar_saida_do_membro(
    membro: discord.Member,
) -> None:
    """
    Trata a saída de um membro do servidor.

    Publica o card de adeus. A linha em ``usuarios`` permanece no banco
    para histórico e para um possível retorno (rejoin).
    """
    if membro.bot:
        return

    await _publicar_adeus(membro)


async def _publicar_boas_vindas(membro: discord.Member) -> None:
    """
    Envia o card de boas-vindas no canal CANAL_BOAS_VINDAS.
    """
    guilda = membro.guild
    if guilda is None:
        return

    id_do_canal = CANAIS.get("CANAL_BOAS_VINDAS", 0)
    if not id_do_canal:
        registrador.warning(
            "CANAL_BOAS_VINDAS nao configurado; card de entrada ignorado."
        )
        return

    canal = guilda.get_channel(id_do_canal)
    if canal is None:
        registrador.warning(
            "Canal de boas-vindas %s nao encontrado na guilda %s.",
            id_do_canal,
            guilda.id,
        )
        return

    if not isinstance(canal, discord.TextChannel):
        registrador.warning(
            "CANAL_BOAS_VINDAS (%s) nao e um canal de texto.",
            id_do_canal,
        )
        return

    try:
        view_do_card = montar_card_boas_vindas(membro)
        await canal.send(view=view_do_card)
    except discord.HTTPException as erro_http:
        registrador.exception(
            "Falha HTTP ao enviar boas-vindas de %s: %s",
            membro.id,
            erro_http,
        )
    except Exception as erro_inesperado:
        registrador.exception(
            "Erro inesperado ao enviar boas-vindas de %s: %s",
            membro.id,
            erro_inesperado,
        )


async def _publicar_adeus(membro: discord.Member) -> None:
    """
    Envia o card de adeus no canal CANAL_ADEUS_SERVIDOR.
    """
    guilda = membro.guild
    if guilda is None:
        return

    id_do_canal = CANAIS.get("CANAL_ADEUS_SERVIDOR", 0)
    if not id_do_canal:
        registrador.warning(
            "CANAL_ADEUS_SERVIDOR nao configurado; card de saida ignorado."
        )
        return

    canal = guilda.get_channel(id_do_canal)
    if canal is None:
        registrador.warning(
            "Canal de adeus %s nao encontrado na guilda %s.",
            id_do_canal,
            guilda.id,
        )
        return

    if not isinstance(canal, discord.TextChannel):
        registrador.warning(
            "CANAL_ADEUS_SERVIDOR (%s) nao e um canal de texto.",
            id_do_canal,
        )
        return

    try:
        view_do_card = montar_card_adeus(membro, guilda)
        await canal.send(view=view_do_card)
    except discord.HTTPException as erro_http:
        registrador.exception(
            "Falha HTTP ao enviar adeus de %s: %s",
            membro.id,
            erro_http,
        )
    except Exception as erro_inesperado:
        registrador.exception(
            "Erro inesperado ao enviar adeus de %s: %s",
            membro.id,
            erro_inesperado,
        )
