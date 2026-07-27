import discord
from src.config import LOGO_PATH
from src.panels.selecionar_candidato import SelecionarCandidatoView
from src.utils.error_handling import LoggingViewMixin
class PainelRecrutamentoLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.action_row = discord.ui.ActionRow()

        self.botao_iniciar_recrutamento = discord.ui.Button(
            label="Iniciar Recrutamento",
            style=discord.ButtonStyle.success,
            emoji="📋",
            custom_id="painel:iniciar_recrutamento",
        )

        self.botao_iniciar_recrutamento.callback = self.iniciar_recrutamento
        self.action_row.add_item(self.botao_iniciar_recrutamento)

        self.botao_liberar_avaliacao = discord.ui.Button(
            label="Liberar Avaliação",
            style=discord.ButtonStyle.danger,
            emoji="🔓",
            custom_id="painel:liberar_avaliacao",
        )

        self.botao_liberar_avaliacao.callback = self.liberar_avaliacao
        self.action_row.add_item(self.botao_liberar_avaliacao)

        icon_url = guild.icon.url if guild.icon else None

        self.container = discord.ui.Container(
            # ────────────────────────────────────────────────
            # Section (Título + descrição + Thumbnail)
            # ────────────────────────────────────────────────
            discord.ui.Section(
                "# 📋 Painel de Recrutamento",
                (
                    "Este painel é destinado exclusivamente aos recrutadores autorizados.\n\n"
                    "Ao iniciar o processo, o sistema registrará automaticamente todas as alterações "
                    "de cargos e manterá o histórico do candidato."
                ),
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),

            # ────────────────────────────────────────────────
            # Separator
            # ────────────────────────────────────────────────
            discord.ui.Separator(
                spacing=discord.SeparatorSpacing.large
            ),

            # ────────────────────────────────────────────────
            # TextDisplay
            # ────────────────────────────────────────────────
            discord.ui.TextDisplay(
                "## 📌 Antes de iniciar\n\n"
                "✅ O candidato deve possuir o cargo **Visitante**.\n"
                "✅ A WhiteList deve estar aprovada.\n"
                "✅ Tenha o **ID do Discord** do candidato em mãos.\n"
                "✅ Certifique-se de que o candidato está presente na call de recrutamento."
            ),

            # ────────────────────────────────────────────────
            # Separator
            # ────────────────────────────────────────────────
            discord.ui.Separator(
                spacing=discord.SeparatorSpacing.large
            ),

            # ────────────────────────────────────────────────
            # ActionRow
            # ────────────────────────────────────────────────
            self.action_row,
            accent_color=discord.Color.brand_red(),
        )
        self.add_item(self.container)

    async def iniciar_recrutamento(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Selecione o candidato:",
            view=SelecionarCandidatoView(recrutador=interaction.user),
            ephemeral=True,
        )

    async def liberar_avaliacao(self, interaction: discord.Interaction):
            from src.services.liberacao_service import liberar_avaliacao_click
            await liberar_avaliacao_click(interaction)