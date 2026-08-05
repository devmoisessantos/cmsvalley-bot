# src\gate\gate_eventos.py
import discord
from discord.ext import commands

from src.config import CARGOS_CRIACAO_EVENTO_GATE
from src.gate.evento_gate_modal import (
    ModalDominas,
    ModalFacXFac,
    ModalTreino,
)
from src.gate.evento_gate_services import listar_eventos_abertos
from src.gate.gate_class import SelectEncerrarEvento
from src.utils.mensagens import responder_card


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
            await responder_card(
                interaction,
                "❌ Sem Permissão",
                ["Você não tem permissão para gerenciar eventos do GATE."],
                cor=discord.Color.red(),
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
                await responder_card(
                    interaction,
                    "Nenhum Evento Aberto",
                    ["Não há eventos em aberto no momento."],
                    cor=discord.Color.orange(),
                )
                return

            row = discord.ui.ActionRow()
            row.add_item(SelectEncerrarEvento(eventos))

            await responder_card(
                interaction,
                "Encerrar Evento",
                ["Selecione qual evento deseja encerrar:"],
                cor=discord.Color.orange(),
                extra_row=row,  # 👈 agora usando o parâmetro certo, que já existe
                delay=None,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(GateEventosCog(bot))
