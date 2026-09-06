"""
Painel e formulario da whitelist que o membro preenche.

`PainelWhitelistLayout` e o card fixo do canal. `ModalWhitelist` e o formulario
com os dados pedidos (nome, telefone e demais campos).

Este arquivo so coleta. Conferir e gravar e trabalho de whitelist_service.py.
"""

from __future__ import annotations

import discord

from src.config import WHITELIST_GALLERY
from src.utils.error_handling import LoggingModalMixin, LoggingViewMixin
from src.whitelist.whitelist_service import processar_whitelist


class PainelWhitelistLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel fixo no canal de whitelist."""

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)

        url_icone = None
        if guild is not None and guild.icon is not None:
            url_icone = guild.icon.url

        componentes: list = []

        # Bloco 1: cabeçalho com ícone do servidor (quando existir)
        texto_cabecalho = (
            "# 🏥 Centro Médico Sul Valley\n"
            "> 🔐 Sistema de Liberação do Servidor\n"
            "## 📌 Instruções obrigatórias\n"
            "⚠️ Antes de prosseguir, certifique-se de que está conectado à cidade "
            "e possui sua identificação em mãos."
        )
        if url_icone:
            componentes.append(
                discord.ui.Section(
                    texto_cabecalho,
                    accessory=discord.ui.Thumbnail(url_icone),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(texto_cabecalho))

        # Bloco 2: separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Bloco 3: como obter os dados
        componentes.append(
            discord.ui.TextDisplay(
                "## 🎮 Como obter seus dados\n\n"
                "As informações solicitadas são encontradas pressionando a tecla "
                "**`F11`** dentro do FiveM.\n\n"
                "> ❗ **Atenção:** Estas são informações do **seu personagem no "
                "jogo**, **NÃO** da sua conta Discord ou de fora do servidor."
            )
        )

        # Bloco 4: separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Bloco 5: após a validação
        componentes.append(
            discord.ui.TextDisplay(
                "### 🔄 Após a validação\n\n"
                "- O sistema verificará suas informações\n"
                "- Seu acesso será **liberado automaticamente**\n"
                "- Seus cargos serão **ajustados conforme sua permissão**"
            )
        )

        # Bloco 6: galeria (só se houver URL configurada)
        urls_da_galeria = [url for url in WHITELIST_GALLERY if url]
        if urls_da_galeria:
            componentes.append(
                discord.ui.MediaGallery(
                    *[discord.MediaGalleryItem(url) for url in urls_da_galeria[:10]]
                )
            )

        # Bloco 7: separador antes do botão
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Botão (inalterado)
        linha_botoes = discord.ui.ActionRow()
        botao_identificar = discord.ui.Button(
            label="Realizar WhiteList",
            style=discord.ButtonStyle.success,
            emoji="🪪",
            custom_id="painel:whitelist_identificar",
        )
        botao_identificar.callback = self.identificar
        linha_botoes.add_item(botao_identificar)
        componentes.append(linha_botoes)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.blurple(),
            )
        )

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
