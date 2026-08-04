# src/gate/list_evento_panel.py
import discord

from src.database.connection import async_session
from src.database.models import EventosGate
from src.config import CANAIS, HIERARQUIA_GATE
from src.gate.evento_gate_services import (
    buscar_evento_aberto,
    confirmar_presenca,
    cancelar_presenca,
    listar_presencas,
)


class ModalConfirmarPresenca(discord.ui.Modal, title="Confirmar Presença"):
    id_fivem = discord.ui.TextInput(label="Seu ID FiveM", placeholder="1186", max_length=10)

    def __init__(self, evento_id: int):
        super().__init__()
        self.evento_id = evento_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            id_fivem_int = int(self.id_fivem.value)
        except ValueError:
            await interaction.response.send_message("ID FiveM inválido.", ephemeral=True)
            return

        await confirmar_presenca(self.evento_id, interaction.user.id, id_fivem_int)
        await interaction.response.send_message("✅ Presença confirmada!", ephemeral=True)
        await atualizar_painel_presenca(interaction.client, self.evento_id)


async def _montar_container(bot: discord.Client, evento) -> discord.ui.Container:
    guild = bot.get_guild(evento.guild_id) if hasattr(evento, "guild_id") else bot.guilds[0]

    membros_gate = [
        m for m in guild.members
        if any(role.name in HIERARQUIA_GATE for role in m.roles)
    ]

    presencas = await listar_presencas(evento.id)
    confirmados_ids = {p.discord_id: p for p in presencas}

    nao_confirmados = [m for m in membros_gate if m.id not in confirmados_ids]

    container = discord.ui.Container(accent_colour=discord.Colour.green())
    container.add_item(discord.ui.TextDisplay("# 📋 Lista de Presença"))
    container.add_item(discord.ui.TextDisplay(f"`✅` **Total de confirmados:** {len(confirmados_ids)}"))
    container.add_item(
        discord.ui.TextDisplay(f"`👨‍⚕️` **Responsável pela lista:** <@{evento.responsavel_id}>")
    )
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    linhas_confirmados = "\n".join(
        f"`✅` `{p.id_fivem}` — <@{discord_id}>"
        for discord_id, p in confirmados_ids.items()
    ) or "_ninguém confirmou ainda_"
    container.add_item(discord.ui.TextDisplay(f"**Marcou presença:**\n{linhas_confirmados}"))

    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    linhas_nao_confirmados = "\n".join(
        f"`❓` — <@{m.id}>" for m in nao_confirmados
    ) or "_todos confirmaram_"
    container.add_item(discord.ui.TextDisplay(f"**Não confirmado:**\n{linhas_nao_confirmados}"))

    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    row = discord.ui.ActionRow()
    row.add_item(
        discord.ui.Button(
            label="Confirmar Presença",
            style=discord.ButtonStyle.success,
            custom_id=f"presenca:{evento.id}",
        )
    )
    container.add_item(row)
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    container.add_item(discord.ui.TextDisplay("-# GATE | CENTRO MÉDICO SUL VALLEY •"))

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
    evento = await buscar_evento_aberto()
    if not evento or evento.id != evento_id:
        return

    canal = bot.get_channel(evento.channel_id)
    msg = await canal.fetch_message(evento.message_id)

    view = discord.ui.LayoutView(timeout=None)
    container = await _montar_container(bot, evento)
    view.add_item(container)

    await msg.edit(view=view)


async def registrar_listener_presenca(bot: discord.Client):
    @bot.listen("on_interaction")
    async def _on_presenca_interaction(interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("presenca:"):
            return

        evento_id = int(custom_id.split(":", 1)[1])
        presencas = await listar_presencas(evento_id)
        ja_confirmou = any(p.discord_id == interaction.user.id for p in presencas)

        if ja_confirmou:
            await cancelar_presenca(evento_id, interaction.user.id)
            await interaction.response.send_message("↩️ Presença cancelada.", ephemeral=True)
            await atualizar_painel_presenca(interaction.client, evento_id)
        else:
            await interaction.response.send_modal(ModalConfirmarPresenca(evento_id))