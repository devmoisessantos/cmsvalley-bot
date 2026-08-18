# src/recrutamento/selecionar_candidato.py
"""
Formularios para achar um candidato pelo ID do Discord ou do FiveM.

Quando a equipe precisa mexer numa candidatura, precisa dizer de quem ela e.
`SelecionarCandidatoView` oferece a lista, e os dois formularios
(`ModalDiscordID` e `ModalIdFiveM`) atendem quem prefere digitar o numero.

Os dois formularios conferem se o que foi digitado e realmente um numero antes
de consultar o banco, para um erro de digitacao virar um aviso amigavel em vez
de um erro feio.
"""

import discord

from src.recrutamento.recrutamento_service import validar_e_iniciar_recrutamento
from src.utils.error_handling import LoggingModalMixin, LoggingViewMixin
from src.utils.mensagens import (
    responder_erro,
)


class LinhaDeEscolhaDoCandidato(discord.ui.ActionRow):
    """A lista suspensa onde o recrutador escolhe o candidato."""

    def __init__(self, recrutador: discord.Member) -> None:
        super().__init__()
        self.recrutador = recrutador

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Selecione o candidato")
    async def ao_escolher_candidato(
        self,
        interacao: discord.Interaction,
        selecao_de_membro: discord.ui.UserSelect,
    ) -> None:
        """Leva o membro escolhido ao formulario que pede o identificador FiveM."""
        candidato_escolhido = selecao_de_membro.values[0]
        await interacao.response.send_modal(
            ModalIdFiveM(candidato=candidato_escolhido, recrutador=self.recrutador)
        )


class LinhaDoBotaoDeIdManual(discord.ui.ActionRow):
    """O botao de escape para quando o candidato nao aparece na lista."""

    def __init__(self, recrutador: discord.Member) -> None:
        super().__init__()
        self.recrutador = recrutador

    @discord.ui.button(label="Usar Discord ID", style=discord.ButtonStyle.secondary)
    async def ao_pedir_id_manual(
        self,
        interacao: discord.Interaction,
        botao_de_id_manual: discord.ui.Button,
    ) -> None:
        """
        Oferece o formulario manual quando o candidato nao aparece no seletor.

        A lista suspensa de membros do Discord nem sempre mostra todo mundo,
        principalmente em servidor grande. Sem este botao, o recrutador ficaria
        travado sem conseguir cadastrar quem nao apareceu na lista.
        """
        await interacao.response.send_modal(ModalDiscordID(recrutador=self.recrutador))


class SelecionarCandidatoView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Card privado onde o recrutador diz de qual candidato ele esta falando.

    Tem dois caminhos: escolher na lista de membros ou digitar o Discord ID na
    mao. O card some depois de 120 segundos sem uso, para nao ficar seletor
    velho aberto no canal.
    """

    def __init__(self, recrutador: discord.Member) -> None:
        super().__init__(timeout=120)

        self.recrutador = recrutador

        self.container_do_seletor = discord.ui.Container(
            accent_colour=discord.Color.brand_red()
        )
        self.container_do_seletor.add_item(
            discord.ui.TextDisplay("## 🧑‍⚕️ Selecione o candidato")
        )
        self.container_do_seletor.add_item(
            discord.ui.TextDisplay(
                "Escolha na lista abaixo ou informe o Discord ID na mao."
            )
        )
        self.container_do_seletor.add_item(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
        )
        self.container_do_seletor.add_item(LinhaDeEscolhaDoCandidato(recrutador))
        self.container_do_seletor.add_item(LinhaDoBotaoDeIdManual(recrutador))

        self.add_item(self.container_do_seletor)


class ModalDiscordID(
    LoggingModalMixin, discord.ui.Modal, title="Informar Discord ID e ID FiveM"
):
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
        """Valida o ID digitado e inicia o fluxo para o membro localizado."""
        try:
            id_convertido = int(self.discord_id.value.strip())
        except ValueError:
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    "ID inválido. Deve conter apenas números.",
                ],
            )
            return

        candidato = interaction.guild.get_member(id_convertido)
        if candidato is None:
            await responder_erro(
                interaction,
                titulo="Não encontrado",
                linhas=[
                    "Membro não encontrado neste servidor.",
                ],
            )
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
        """Encaminha o ID FiveM informado para iniciar o recrutamento selecionado."""
        await validar_e_iniciar_recrutamento(
            interaction, self.candidato, self.recrutador, self.id_fivem.value.strip()
        )
