"""
Painel e formulario da whitelist que o membro preenche.

`PainelWhitelistLayout` e o card fixo do canal. `ModalWhitelist` e o formulario
com os dados pedidos (nome, telefone e demais campos).

Este arquivo so coleta. Conferir e gravar e trabalho de whitelist_service.py.
"""

import discord

from src.utils.error_handling import LoggingModalMixin, LoggingViewMixin
from src.whitelist.whitelist_service import processar_whitelist


class PainelWhitelistLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.action_row = discord.ui.ActionRow()

        self.botao_identificar = discord.ui.Button(
            label="Realizar WhiteList",
            style=discord.ButtonStyle.success,
            emoji="🪪",
            custom_id="painel:whitelist_identificar",
        )
        self.botao_identificar.callback = self.identificar
        self.action_row.add_item(self.botao_identificar)

        icon_url = guild.icon.url if guild.icon else None

        self.container = discord.ui.Container(
            # ────────────────────────────────────────────────
            # Section (Título + descrição + Thumbnail)
            # ────────────────────────────────────────────────
            discord.ui.TextDisplay("# Centro Médico Sul Valley"),
            # ────────────────────────────────────────────────
            # Separator
            # ────────────────────────────────────────────────
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            # ────────────────────────────────────────────────
            # TextDisplay
            # ────────────────────────────────────────────────
            discord.ui.Section(
                "> **Sistema de Liberação do Servidor**\n",
                (
                    "- Conecte-se à cidade primeiro e tenha sua identificação em "
                    "mãos.\n"
                    "- As informações solicitadas são encontradas pressionando `F11` "
                    "dentro do FiveM.\n"
                    "( Já conectado na cidade. )\n"
                    "- **Elas NÃO são informações da sua conta do Discord ou de fora "
                    "do jogo.**\n"
                    "- Clique no botão abaixo para informar sua identidade.\n"
                    "- Após a validação, o sistema liberará seu acesso e ajustará seus "
                    "cargos automaticamente."
                ),
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),
            # ────────────────────────────────────────────────
            # Separator
            # ────────────────────────────────────────────────
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            # ────────────────────────────────────────────────
            # ActionRow
            # ────────────────────────────────────────────────
            self.action_row,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(
            self.container
        )  # 👈 não esquecer disso // sem isso não aparece mensagem nenhuma

    async def identificar(self, interaction: discord.Interaction):
        """Abre o formulário que coleta a identidade usada na validação de acesso."""
        await interaction.response.send_modal(ModalWhitelist())


class ModalWhitelist(
    LoggingModalMixin, discord.ui.Modal, title="Whitelist - Identificação"
):
    nome = discord.ui.TextInput(
        label="Nome", placeholder="Ex: Eduardo", required=True, max_length=30
    )
    sobrenome = discord.ui.TextInput(
        label="Sobrenome", placeholder="Ex: Alves", required=True, max_length=30
    )
    idade = discord.ui.TextInput(
        label="Idade", placeholder="Ex: 18", required=True, max_length=3
    )
    telefone = discord.ui.TextInput(
        label="Telefone", placeholder="Ex: 427-282", required=True, max_length=15
    )
    id_fivem = discord.ui.TextInput(
        label="Identificador (ID FiveM)",
        placeholder="Ex: 107250",
        required=True,
        max_length=6,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Encaminha os dados informados ao serviço que valida e libera o membro.

        Remove espaços antes do processamento para evitar rejeições causadas por
        cópia ou digitação. O serviço executa as mudanças de cargos e persistência
        necessárias depois que os dados da identificação são conferidos.
        """
        await processar_whitelist(
            interaction,
            nome=self.nome.value.strip(),
            sobrenome=self.sobrenome.value.strip(),
            idade=self.idade.value.strip(),
            telefone=self.telefone.value.strip(),
            id_fivem=self.id_fivem.value.strip(),
        )
