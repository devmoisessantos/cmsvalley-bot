import discord

from src.config import GUILD_ID, CANAIS_PLANTAO, NOMES_CANAIS_PLANTAO
from src.services.plantao_service import ligar_servico, desligar_servico
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from src.utils.error_handling import LoggingViewMixin
from sqlalchemy import select


def _todos_os_canais_em_ordem() -> list[int]:
    """Achata CANAIS_PLANTAO preservando a ordem de exibição desejada."""
    return [
        CANAIS_PLANTAO["CALL_INTERNA"],
        CANAIS_PLANTAO["CALL_EXTERNA"],
        CANAIS_PLANTAO["BATE_PAPO_1"],
        CANAIS_PLANTAO["BATE_PAPO_2"],
        CANAIS_PLANTAO["BATE_PAPO_3"],
        *CANAIS_PLANTAO["CONSULTORIOS"],
        *CANAIS_PLANTAO["SALA_CURSOS"],
        CANAIS_PLANTAO["DIRETORIA"],
        CANAIS_PLANTAO["DIRETORIA_GERAL"],
        *CANAIS_PLANTAO["RECRUTAMENTO"],
    ]


class PainelPlantaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        # Linha 0: toggle
        row_toggle = discord.ui.ActionRow()
        row_toggle.add_item(self._botao_toggle())

        # Linha 1: select com todas as calls
        row_select = discord.ui.ActionRow()
        row_select.add_item(self._select_calls())

        container = discord.ui.Container(
            discord.ui.TextDisplay("# 🩺 Painel de Plantão"),
            discord.ui.TextDisplay(
                "Use o botão abaixo para entrar/sair de serviço.\n"
                "Depois, selecione uma call para receber o link de acesso."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            row_toggle,
            row_select,
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

    def _select_calls(self) -> discord.ui.Select:
        opcoes = [
            discord.SelectOption(label=NOMES_CANAIS_PLANTAO[canal_id], value=str(canal_id))
            for canal_id in _todos_os_canais_em_ordem()
        ]
        select = discord.ui.Select(placeholder="📞 Escolha uma call para se conectar", options=opcoes)
        select.callback = self._callback_selecionar_call
        return select

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

    async def _callback_selecionar_call(self, interaction: discord.Interaction):
        canal_id = int(interaction.data["values"][0])
        nome_call = NOMES_CANAIS_PLANTAO.get(canal_id, "Call")

        view_link = discord.ui.LayoutView(timeout=None)
        botao_link = discord.ui.Button(
            label=f"🔗 Conectar em {nome_call}",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{GUILD_ID}/{canal_id}",
        )
        row = discord.ui.ActionRow()
        row.add_item(botao_link)
        view_link.add_item(row)

        await interaction.response.send_message(view=view_link, ephemeral=True)