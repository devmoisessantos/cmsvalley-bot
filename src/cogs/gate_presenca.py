# src/cogs/gate_presenca.py
import discord
from discord.ext import commands

from src.gate.list_evento_panel import ModalConfirmarPresenca, atualizar_painel_presenca
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


async def setup(bot: commands.Bot):
    await bot.add_cog(GatePresencaCog(bot))