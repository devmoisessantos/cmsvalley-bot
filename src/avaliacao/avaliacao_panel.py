"""
Painel fixo que convida o membro a avaliar o atendimento recebido.

E um card so, com um botao. Quem clica cai no fluxo de perguntas montado em
avaliacao_service.py. Este arquivo cuida apenas da aparencia do convite.
"""

import discord

from src.utils.error_handling import LoggingViewMixin


class PainelAvaliacaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)

        url_icone = None
        if guild is not None and guild.icon is not None:
            url_icone = guild.icon.url

        componentes: list = []

        # Bloco 1: cabeçalho com ícone do servidor (quando existir)
        texto_cabecalho = (
            "# 📝 Avaliação de Recrutamento\n"
            "> 🎯 Processo Seletivo – CMS Valley\n"
            "Esta é a etapa final do seu processo seletivo para ingressar "
            "no **CMS Valley**."
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

        # Bloco 3: atenção e regras da prova
        componentes.append(
            discord.ui.TextDisplay(
                "## ⚠️ Atenção!\n\n"
                "> 🛑 **A avaliação só pode ser iniciada uma vez.**\n"
                "> ⏳ Não feche a janela ou saia durante a prova.\n"
                "> 🔒 Mantenha uma conexão estável com a internet.\n"
                "> 📵 Não utilize materiais externos durante a avaliação.\n\n"
                "- | 📊 **Total de perguntas:** 11 questões de múltipla escolha\n"
                "- | ⏱️ **Tempo limite:** 1 hora (60 minutos)\n"
                "- | 🔄 **Tentativas:** ⚠️ Apenas **uma única tentativa**\n"
                "- | ✅ **Critério de aprovação:** 70% de acertos (8/11)"
            )
        )

        # Bloco 4: separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Bloco 5: dicas
        componentes.append(
            discord.ui.TextDisplay(
                "### 📌 Dicas para a prova\n\n"
                "- ✅ Leia cada pergunta com atenção\n"
                "- ✅ Revise suas respostas antes de avançar\n"
                "- ✅ Gerencie bem seu tempo (cerca de 5 min por pergunta)\n"
                "- ❌ Não atualize a página durante a prova\n"
                "- ❌ Não utilize abas ou dispositivos externos\n\n"
                "Em caso de problemas técnicos, entre em contato imediatamente "
                "com o **Responsável pelo Recrutamento**."
            )
        )

        # Bloco 6: separador antes do botão
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Botão (inalterado)
        linha_botoes = discord.ui.ActionRow()
        botao_iniciar_avaliacao = discord.ui.Button(
            label="Iniciar Avaliação",
            style=discord.ButtonStyle.green,
            custom_id="painel:iniciar_avaliacao",
        )
        botao_iniciar_avaliacao.callback = self.iniciar_avaliacao
        linha_botoes.add_item(botao_iniciar_avaliacao)
        componentes.append(linha_botoes)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.gold(),
            )
        )

    async def iniciar_avaliacao(self, interaction: discord.Interaction):
        """Encaminha o clique persistente ao serviço que valida e inicia a prova."""
        from src.avaliacao.avaliacao_service import iniciar_avaliacao

        await iniciar_avaliacao(interaction)
