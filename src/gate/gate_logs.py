# src\gate\gate_logs.py

import discord

from src.config import (
    CANAIS,
    MESES_ABREV,
)
from src.gate.evento_gate_services import salvar_log_message_id
from src.utils.log_container import LogContainerView


def _formatar_data_hora(dt) -> str:
    return f"{dt.day} de {MESES_ABREV[dt.month]} às {dt.strftime('%H:%M')}"


def _linhas_log(evento) -> str:
    inicio_str = _formatar_data_hora(evento.created_at)
    fim_str = (
        _formatar_data_hora(evento.closed_at)
        if evento.status == "encerrado" and evento.closed_at
        else "Em andamento"
    )
    return (
        f"**Quem iniciou o evento:** <@{evento.criado_por}> `({evento.criado_por})`\n"
        f"**Início do evento:** {inicio_str}\n"
        f"**Finalizado em:** {fim_str}"
    )


async def enviar_log_evento(bot: discord.Client, evento, guild: discord.Guild):
    canal = bot.get_channel(CANAIS["LOG_GATE"])
    view = LogContainerView(
        titulo=f"📋 {evento.titulo}", linhas=_linhas_log(evento), guild=guild
    )
    msg = await canal.send(view=view)
    await salvar_log_message_id(evento.id, msg.id)


async def atualizar_log_evento(bot: discord.Client, evento, guild: discord.Guild):
    if not evento.log_message_id:
        return
    canal = bot.get_channel(CANAIS["LOG_GATE"])
    msg = await canal.fetch_message(evento.log_message_id)

    view = discord.ui.LayoutView(timeout=None)
    view = LogContainerView(
        titulo=f"📋 {evento.titulo}", linhas=_linhas_log(evento), guild=guild
    )
    await msg.edit(view=view)
