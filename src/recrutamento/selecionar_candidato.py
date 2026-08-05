# src/recrutamento/selecionar_candidato.py
import discord

from src.recrutamento.recrutamento_service import validar_e_iniciar_recrutamento
from src.utils.error_handling import LoggingViewMixin, LoggingModalMixin


class SelecionarCandidatoView(LoggingViewMixin, discord.ui.View):
    def __init__(self, recrutador: discord.Member):
        super().__init__(timeout=120)
        self.recrutador = recrutador

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Selecione o candidato")
    async def selecionar_usuario(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        candidato = select.values[0]
        await interaction.response.send_modal(
            ModalIdFiveM(candidato=candidato, recrutador=self.recrutador)
        )

    @discord.ui.button(label="Usar Discord ID", style=discord.ButtonStyle.secondary)
    async def usar_id_manual(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ModalDiscordID(recrutador=self.recrutador))


class ModalDiscordID(LoggingModalMixin, discord.ui.Modal, title="Informar Discord ID e ID FiveM"):
    def __init__(self, recrutador: discord.Member):
        super().__init__()
        self.recrutador = recrutador

    discord_id = discord.ui.TextInput(
        label="ID do Discord do candidato",
        placeholder="Ex: 123456789012345678",
        required=True,
        max_length=20,
    )
    id_fivem = discord.ui.TextInput(
        label="ID FiveM do candidato",
        placeholder="Ex: 49973",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            id_convertido = int(self.discord_id.value.strip())
        except ValueError:
            await interaction.response.send_message("ID inválido. Deve conter apenas números.", ephemeral=True)
            return

        candidato = interaction.guild.get_member(id_convertido)
        if candidato is None:
            await interaction.response.send_message("Membro não encontrado neste servidor.", ephemeral=True)
            return

        await validar_e_iniciar_recrutamento(
            interaction, candidato, self.recrutador, self.id_fivem.value.strip()
        )

class ModalIdFiveM(LoggingModalMixin, discord.ui.Modal, title="Informar ID FiveM"):
    def __init__(self, candidato: discord.Member, recrutador: discord.Member):
        super().__init__()
        self.candidato = candidato
        self.recrutador = recrutador

    id_fivem = discord.ui.TextInput(
        label="ID FiveM do candidato",
        placeholder="Ex: 49973",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await validar_e_iniciar_recrutamento(
            interaction, self.candidato, self.recrutador, self.id_fivem.value.strip()
        )







