from datetime import (
    datetime,
    timezone,
)

import discord

from src.config import (
    CANAIS,
    HIERARQUIA_GATE,
)
from src.database.connection import async_session
from src.database.models import EventosGate
from src.gate.evento_gate_services import (
    buscar_evento_por_id,
    listar_presencas,
)


async def _montar_container(bot: discord.Client, evento) -> discord.ui.Container:
    guild = (
        bot.get_guild(evento.guild_id) if hasattr(evento, "guild_id") else bot.guilds[0]
    )

    membros_gate = [
        m
        for m in guild.members
        if any(role.name in HIERARQUIA_GATE for role in m.roles)
    ]

    presencas = await listar_presencas(evento.id)
    confirmados_ids = {p.discord_id: p for p in presencas}

    nao_confirmados = [m for m in membros_gate if m.id not in confirmados_ids]

    icon_url = guild.icon.url if guild.icon else None

    texto_cabecalho = (
        "# 📋 Lista de Presença\n"
        f"`✅` **Total de confirmados:** {len(confirmados_ids)}\n"
        f"`👨‍⚕️` **Responsável pela lista:** <@{evento.responsavel_id}>"
    )

    container = discord.ui.Container(accent_colour=discord.Colour.green())

    if icon_url:
        container.add_item(
            discord.ui.Section(
                texto_cabecalho, accessory=discord.ui.Thumbnail(icon_url)
            )
        )
    else:
        container.add_item(discord.ui.TextDisplay(texto_cabecalho))

    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    linhas_confirmados = (
        "\n".join(
            f"`✅` `{p.id_fivem}` — <@{discord_id}>"
            for discord_id, p in confirmados_ids.items()
        )
        or "_ninguém confirmou ainda_"
    )
    container.add_item(
        discord.ui.TextDisplay(f"**Marcou presença:**\n{linhas_confirmados}")
    )

    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    linhas_nao_confirmados = (
        "\n".join(f"`❓` — <@{m.id}>" for m in nao_confirmados) or "_todos confirmaram_"
    )
    container.add_item(
        discord.ui.TextDisplay(f"**Não confirmado:**\n{linhas_nao_confirmados}")
    )

    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    row = discord.ui.ActionRow()
    row.add_item(
        discord.ui.Button(
            label="✅ Confirmar Presença",
            style=discord.ButtonStyle.success,
            custom_id=f"presenca:{evento.id}",
        )
    )
    container.add_item(row)
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    agora = int(datetime.now(timezone.utc).timestamp())
    rodape = f"-# {guild.name} • <t:{agora}:f>"
    container.add_item(discord.ui.TextDisplay(rodape))

    return container


async def enviar_painel_presenca(bot: discord.Client, evento):
    canal = bot.get_channel(CANAIS["CANAL_MARCAR_PRESENCA_GATE"])
    view = discord.ui.LayoutView(timeout=None)
    container = await _montar_container(bot, evento)
    view.add_item(container)

    msg = await canal.send(view=view)

    async with async_session() as session:
        evento_db = await session.get(EventosGate, evento.id)
        evento_db.message_id = msg.id
        evento_db.channel_id = canal.id
        await session.commit()


async def atualizar_painel_presenca(bot: discord.Client, evento_id: int):
    evento = await buscar_evento_por_id(evento_id)
    if not evento:
        return

    canal = bot.get_channel(evento.channel_id)
    msg = await canal.fetch_message(evento.message_id)

    view = discord.ui.LayoutView(timeout=None)
    container = await _montar_container(bot, evento)
    view.add_item(container)

    await msg.edit(view=view)
