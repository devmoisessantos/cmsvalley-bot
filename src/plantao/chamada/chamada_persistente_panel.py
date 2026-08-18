"""Painel persistente em #fazer-chamada — iniciar chamada (Doutor+)."""

from __future__ import annotations

import discord

from src.plantao.chamada.chamada_panel import PainelChamadaView
from src.plantao.plantao_permissoes import (
    e_doutor_ou_acima,
    mensagem_sem_permissao,
)
from src.utils.error_handling import LoggingViewMixin
from src.utils.mensagens import (
    responder_erro,
    responder_view,
)


class PainelFazerChamadaLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Canal dedicado: qualquer Doutor+ inicia o fluxo de chamada."""

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        icon_url = guild.icon.url if guild.icon else None
        row = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="🩺 Realizar Chamada",
            style=discord.ButtonStyle.success,
            custom_id="chamada:iniciar_painel",
            emoji="📋",
        )
        botao.callback = self._ao_iniciar
        row.add_item(botao)

        self.container = discord.ui.Container(
            discord.ui.Section(
                "# 🩺 Central de Chamadas",
                ("> Área dos **Doutores** — controle de presença do plantão."),
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                "Apenas **Doutor ou acima** pode iniciar uma chamada.\n\n"
                "Ao iniciar, envie o print do `/ems` no chat deste canal "
                "e o sistema cruzará com quem está de plantão."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row,
            accent_color=discord.Color.blue(),
        )
        self.add_item(self.container)

    async def _ao_iniciar(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await responder_erro(
                interaction,
                titulo="Comando indisponível aqui",
                linhas=[
                    "Use este painel em um servidor.",
                ],
            )
            return

        if not e_doutor_ou_acima(interaction.user):
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    mensagem_sem_permissao("realizar chamadas (Doutor ou acima)"),
                ],
            )
            return

        # Reutiliza o fluxo existente (cooldown + print EMS + etapas)
        view = await PainelChamadaView.construir(interaction.user)
        await responder_view(
            interaction,
            view,
            ephemeral=True,
        )
