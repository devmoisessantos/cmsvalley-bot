# src/gate/gate_panel.py
"""
Painel persistente de criação/encerramento de eventos GATE.

Os botões usam custom_id gate:* e são tratados em gate_cogs.py.
"""

from __future__ import annotations

import discord

from src.utils.error_handling import LoggingViewMixin


class PainelEventosGate(LoggingViewMixin, discord.ui.LayoutView):
    """View persistente (timeout=None) com Components V2."""

    def __init__(self, guild: discord.Guild = None):
        super().__init__(timeout=None)
        self.guild = guild

        thumbnail_do_servidor = None
        if guild is not None and guild.icon is not None:
            thumbnail_do_servidor = discord.ui.Thumbnail(guild.icon.url)

        container = discord.ui.Container(
            discord.ui.TextDisplay(
                "# 🛡️ Criar Evento GATE\n**> Painel dedicado à criação de eventos.**"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.Section(
                "## Agendamento de eventos",
                (
                    "Utilize os botões abaixo para iniciar ou encerrar algum evento.\n"
                    "**Lembre-se:** você deve ser um membro autorizado!\n\n"
                ),
                accessory=thumbnail_do_servidor,
            ),
        )

        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        linha_dos_botoes = discord.ui.ActionRow()
        linha_dos_botoes.add_item(
            discord.ui.Button(
                label="🚩 Treinamento",
                style=discord.ButtonStyle.secondary,
                custom_id="gate:treino",
            )
        )
        linha_dos_botoes.add_item(
            discord.ui.Button(
                label="☠️ FAC x FAC",
                style=discord.ButtonStyle.success,
                custom_id="gate:facxfac",
            )
        )
        linha_dos_botoes.add_item(
            discord.ui.Button(
                label="⚔️ Dominas",
                style=discord.ButtonStyle.success,
                custom_id="gate:dominas",
            )
        )
        linha_dos_botoes.add_item(
            discord.ui.Button(
                label="❌ Encerrar",
                style=discord.ButtonStyle.danger,
                custom_id="gate:encerrar",
            )
        )

        container.add_item(linha_dos_botoes)
        self.add_item(container)
