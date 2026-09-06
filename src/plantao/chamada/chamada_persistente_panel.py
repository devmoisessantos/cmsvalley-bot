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

        url_icone = None
        if guild is not None and guild.icon is not None:
            url_icone = guild.icon.url

        componentes: list = []

        # Bloco 1: cabeçalho com ícone do servidor (quando existir)
        texto_cabecalho = (
            "# 🩺 Central de Chamadas\n"
            "> 📋 Controle de Presença – Plantão Médico\n"
            "Esta é a área exclusiva dos **Doutores** para controle de "
            "presença durante o plantão."
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

        # Bloco 3: acesso, comprovação e passo a passo
        componentes.append(
            discord.ui.TextDisplay(
                "> 🔒 **Acesso restrito:** Apenas **Doutor ou superior** "
                "pode iniciar uma chamada.\n"
                "> 📸 **Comprovação obrigatória:** Envie o print do `/ems` "
                "no chat deste canal.\n\n"
                "## ✏️ Como funciona\n\n"
                "1. Digite `/ems` no jogo\n"
                "2. Tire um **print do ems** (Win+Shift+S ou Print Screen)\n"
                "3. Clique em **Realizar Chamada**\n"
                "4. Cole a imagem neste canal\n"
                "5. Aguarde a confirmação do sistema"
            )
        )

        # Bloco 4: separador antes do botão
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Botão (inalterado)
        linha_botoes = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="🩺 Realizar Chamada",
            style=discord.ButtonStyle.success,
            custom_id="chamada:iniciar_painel",
            emoji="📋",
        )
        botao.callback = self._ao_iniciar
        linha_botoes.add_item(botao)
        componentes.append(linha_botoes)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.blue(),
            )
        )

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
