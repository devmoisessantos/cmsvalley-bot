# src/gate/gate_logger.py
"""
Logs visuais dos eventos GATE no canal de log.

Usa LogContainerView (Components V2).
"""

from __future__ import annotations

import discord

from src.config import CANAIS
from src.gate.gate_service import salvar_log_message_id
from src.utils.formatacao import formatar_data_hora
from src.utils.log_container import LogContainerView


def _montar_linhas_do_log(evento) -> str:
    """Texto do corpo do log (quem criou, início e fim)."""
    texto_inicio = formatar_data_hora(evento.created_at)

    evento_esta_encerrado = (
        evento.status == "encerrado" and evento.closed_at is not None
    )
    if evento_esta_encerrado:
        texto_fim = formatar_data_hora(evento.closed_at)
    else:
        texto_fim = "Em andamento"

    return (
        f"**Quem iniciou o evento:** <@{evento.criado_por}> `({evento.criado_por})`\n"
        f"**Início do evento:** {texto_inicio}\n"
        f"**Finalizado em:** {texto_fim}"
    )


async def enviar_log_evento(
    bot: discord.Client,
    evento,
    guilda: discord.Guild,
) -> bool:
    """
    Publica o log do evento no canal LOG_GATE e salva o message_id.

    Retorna True se enviou, False se o canal não existir.
    """
    canal = bot.get_channel(CANAIS["LOG_GATE"])
    if canal is None:
        print("[GATE] Canal LOG_GATE não encontrado — log não enviado.")
        return False

    view_do_log = LogContainerView(
        titulo=f"📋 {evento.titulo}",
        linhas=_montar_linhas_do_log(evento),
        guild=guilda,
    )
    mensagem = await canal.send(view=view_do_log)
    await salvar_log_message_id(evento.id, mensagem.id)
    return True


async def atualizar_log_evento(
    bot: discord.Client,
    evento,
    guilda: discord.Guild,
) -> bool:
    """Atualiza a mensagem de log já existente (ex.: ao encerrar o evento)."""
    if not evento.log_message_id:
        return False

    canal = bot.get_channel(CANAIS["LOG_GATE"])
    if canal is None:
        print("[GATE] Canal LOG_GATE não encontrado — log não atualizado.")
        return False

    try:
        mensagem = await canal.fetch_message(evento.log_message_id)
    except (discord.NotFound, discord.HTTPException):
        return False

    view_do_log = LogContainerView(
        titulo=f"📋 {evento.titulo}",
        linhas=_montar_linhas_do_log(evento),
        guild=guilda,
    )
    await mensagem.edit(view=view_do_log)
    return True
