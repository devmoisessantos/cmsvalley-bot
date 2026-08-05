# src/gate/evento_gate_panel.py
import discord

from src.utils.error_handling import LoggingViewMixin


# ---------------------------------------------------------------------------
# PAINEL FIXO — EVENTOS GATE
# ---------------------------------------------------------------------------

class PainelEventosGate(LoggingViewMixin, discord.ui.LayoutView):
    """View persistente (timeout=None) renderizada com Container (Components V2)."""
    def __init__(self, guild: discord.Guild = None):
        super().__init__(timeout=None)
        self.guild = guild

        row = discord.ui.ActionRow()
        container = discord.ui.Container(
            discord.ui.TextDisplay(
                "# 🛡️ Criar Evento GATE\n"
                "**> Painel dedicato à criação de eventos.**"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.Section(
                "## Agendamento de eventos",  # ← título
                (
                    "Utilize os botões abaixo para iniciar ou encerrar algum evento.\n"
                    "**Lembre-se:** você deve ser um membro autorizado!\n\n"
                ),  # ← descrição
                accessory=discord.ui.Thumbnail(guild.icon.url) if guild and guild.icon else None,
            ),
        )
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        row.add_item(
            discord.ui.Button(
                label="🚩 Treinamento", 
                style=discord.ButtonStyle.secondary, 
                custom_id="gate:treino"
            )
        )
        row.add_item(
            discord.ui.Button(
                label="☠️ FAC x FAC", 
                style=discord.ButtonStyle.success, 
                custom_id="gate:facxfac"
            )
        )
        row.add_item(
            discord.ui.Button(
                label="⚔️ Dominas", 
                style=discord.ButtonStyle.green, 
                custom_id="gate:dominas"
            )
        )
        row.add_item(
            discord.ui.Button(
                label="❌ Encerrar", 
                style=discord.ButtonStyle.danger, 
                custom_id="gate:encerrar"
            )
        )
        container.add_item(row)
        self.add_item(container)


