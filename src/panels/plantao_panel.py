import discord

from src.services.plantao_service import ligar_servico, desligar_servico
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from src.utils.error_handling import LoggingViewMixin
from sqlalchemy import select


class PainelPlantaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        row_toggle = discord.ui.ActionRow()
        row_toggle.add_item(self._botao_toggle())

        container = discord.ui.Container(
            discord.ui.TextDisplay("# 🩺 Painel de Plantão"),
            discord.ui.TextDisplay(
                "Use o botão abaixo para entrar/sair de serviço."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row_toggle,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)

    def _botao_toggle(self) -> discord.ui.Button:
        botao = discord.ui.Button(
            label="🔄 Entrar/Sair de Serviço",
            style=discord.ButtonStyle.primary,
            custom_id="plantao:toggle",
        )
        botao.callback = self._callback_toggle
        return botao

    async def _callback_toggle(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em servidores.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == interaction.user.id)
            )
            estado = resultado.scalar_one_or_none()
            ja_ligado = estado is not None and estado.toggle_ligado

        if ja_ligado:
            resultado_texto = await desligar_servico(interaction.user)
        else:
            resultado_texto = await ligar_servico(interaction.user)

        await interaction.followup.send(resultado_texto, ephemeral=True)