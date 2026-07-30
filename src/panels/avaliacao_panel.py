import discord

from src.utils.error_handling import LoggingViewMixin


class PainelAvaliacaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)  # timeout=None = painel permanente

        self.action_row = discord.ui.ActionRow()

        self.botao_iniciar_avaliacao = discord.ui.Button(
            label="Iniciar Avaliação",
            style=discord.ButtonStyle.green,
            custom_id="painel:iniciar_avaliacao",
        )

        self.botao_iniciar_avaliacao.callback = self.iniciar_avaliacao
        self.action_row.add_item(self.botao_iniciar_avaliacao)

        icon_url = guild.icon.url if guild.icon else None

        self.container = discord.ui.Container(
            discord.ui.Section(
                "# 📝 Avaliação de Recrutamento",
                (
                    "Clique no botão abaixo para iniciar sua avaliação.\n\n"
                    "Você terá **11 perguntas de múltipla escolha** e **1 hora** para concluir.\n"
                    "⚠️ A avaliação só pode ser iniciada **uma única vez**."

                ),
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            self.action_row,
            accent_color=discord.Color.gold(),
        )
        self.add_item(self.container)

    async def iniciar_avaliacao(self, interaction: discord.Interaction):
        from src.services.avaliacao_service import iniciar_avaliacao
        await iniciar_avaliacao(interaction)


