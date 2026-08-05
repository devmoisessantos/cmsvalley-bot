
import discord
import asyncio
from discord.ext import commands

from src.gate.evento_gate_services import confirmar_presenca, encerrar_evento
from src.gate.gate_logs import atualizar_log_evento
from src.utils.mensagens import CardView, excluir_mensagem


from src.gate.evento_gate_lista import atualizar_painel_presenca
from src.gate.evento_gate_services import listar_presencas, cancelar_presenca
from src.utils.mensagens import responder_card


class GatePresencaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
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
            await responder_card(
                interaction, "↩️ Presença Cancelada",
                ["Sua presença foi cancelada."],
                cor=discord.Color.orange(),
            )
            await atualizar_painel_presenca(interaction.client, evento_id)
        else:
            await interaction.response.send_modal(ModalConfirmarPresenca(evento_id))


class SelectEncerrarEvento(discord.ui.Select):
    def __init__(self, eventos: list):
        options = [
            discord.SelectOption(
                label=f"{ev.titulo} — {ev.data_evento} {ev.horario}",
                value=str(ev.id),
            )
            for ev in eventos
        ]
        super().__init__(placeholder="Selecione o evento para encerrar", options=options)


    async def callback(self, interaction: discord.Interaction):
        evento = await encerrar_evento(int(self.values[0]))

        if not evento:
            view = CardView(
                "Evento Não Encontrado", 
                ["O evento selecionado não existe mais."], 
                cor=discord.Color.red(), timeout=None
            )
        else:
            await atualizar_log_evento(interaction.client, evento, interaction.guild)
            view = CardView(
                "✅ Evento Encerrado", 
                [f"**{evento.titulo}** foi encerrado."], 
                cor=discord.Color.green(), 
                timeout=None
            )

        await interaction.response.edit_message(content=None, view=view)

        msg = await interaction.original_response()
        asyncio.create_task(excluir_mensagem(msg, delay=10))


class ModalConfirmarPresenca(discord.ui.Modal, title="Confirmar Presença"):
    id_fivem = discord.ui.TextInput(label="Seu ID FiveM", placeholder="1186", max_length=10)

    def __init__(self, evento_id: int):
        super().__init__()
        self.evento_id = evento_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            id_fivem_int = int(self.id_fivem.value)
        except ValueError:
            await responder_card(
                interaction, "❌ ID FiveM Inválido",
                ["O ID FiveM fornecido é inválido."],
                cor=discord.Color.red(),
            )
            return

        await confirmar_presenca(self.evento_id, interaction.user.id, id_fivem_int)
        await responder_card(
            interaction, "✅ Presença Confirmada",
            [f"ID FiveM registrado: `{id_fivem_int}`"],
            cor=discord.Color.green(),
        )
        await atualizar_painel_presenca(interaction.client, self.evento_id)


class ViewEncerrarEvento(discord.ui.View):
    def __init__(self, eventos: list):
        super().__init__(timeout=60)
        self.add_item(SelectEncerrarEvento(eventos))


async def setup(bot: commands.Bot):
    await bot.add_cog(GatePresencaCog(bot))