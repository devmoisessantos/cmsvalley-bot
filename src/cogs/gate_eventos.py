# src/cogs/gate_eventos.py
import discord
from discord.ext import commands

from src.config import CARGOS_CRIACAO_EVENTO_GATE
from src.gate.evento_gate_panel import ModalTreino, ModalFacXFac, ModalDominas
from src.gate.evento_gate_services import listar_eventos_abertos
from src.gate.log_gate_panel import ViewEncerrarEvento
from src.utils.mensagens import responder_ephemera

def _tem_permissao_gate(member: discord.Member) -> bool:
    return any(role.name in CARGOS_CRIACAO_EVENTO_GATE for role in member.roles)


class GateEventosCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("gate:"):
            return

        if not _tem_permissao_gate(interaction.user):
            await responder_ephemera(
                interaction,
                "❌ Você não tem permissão para gerenciar eventos do GATE."
            )
            return

        acao = custom_id.split(":", 1)[1]
        if acao == "treino":
            await interaction.response.send_modal(ModalTreino())
        elif acao == "facxfac":
            await interaction.response.send_modal(ModalFacXFac())
        elif acao == "dominas":
            await interaction.response.send_modal(ModalDominas())
        elif acao == "encerrar":
            eventos = await listar_eventos_abertos()
            
            if not eventos:
                await responder_ephemera(
                    interaction, 
                    "Nenhum evento em aberto encontrado.")
                return
            await interaction.response.send_message(
                "Selecione qual evento deseja encerrar:",
                view=ViewEncerrarEvento(eventos),
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(GateEventosCog(bot))