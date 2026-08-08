import discord

from src.membros.cargos_service import (
    adicionar_cargo,
    determinar_escopos,
    listar_cargos_do_escopo,
    remover_cargo,
)
from src.utils.error_handling import LoggingViewMixin


class GerenciarCargosView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Painel interativo para gerenciamento de cargos.
    O executor seleciona um membro e um cargo, depois clica em Adicionar ou Remover.
    Os cargos exibidos no select são apenas aqueles que o executor pode gerenciar.
    """

    def __init__(self, membro_executor: discord.Member):
        super().__init__(timeout=180)

        # Descobre quais escopos o executor pertence
        escopos_do_executor = determinar_escopos(membro_executor)

        # Monta a lista de cargos que o executor pode gerenciar,
        # percorrendo cada escopo e pegando os cargos específicos do cargo dele
        self.nomes_dos_cargos_permitidos: list[str] = []

        for escopo in escopos_do_executor:
            # Agora passamos o membro_executor para filtrar corretamente
            cargos_deste_escopo = listar_cargos_do_escopo(escopo, membro_executor)

            for nome_do_cargo in cargos_deste_escopo:
                # Evita duplicatas (um mesmo cargo pode aparecer em mais de um escopo)
                if nome_do_cargo not in self.nomes_dos_cargos_permitidos:
                    self.nomes_dos_cargos_permitidos.append(nome_do_cargo)

        # Estado da seleção atual
        self.candidato_selecionado: discord.Member | None = None
        self.cargo_selecionado: str | None = None

        # Monta os componentes visuais do painel
        self._montar_componentes()

    def _montar_componentes(self):
        """
        Reconstrói todos os componentes do painel com base no estado atual.
        Chamado sempre que o executor seleciona um membro ou cargo,
        para atualizar o resumo exibido.
        """
        # Select para escolher o membro
        select_membro = discord.ui.UserSelect(placeholder="1. Selecione o membro")
        select_membro.callback = self._ao_selecionar_membro

        # Select para escolher o cargo (apenas os permitidos)
        select_cargo = discord.ui.Select(
            placeholder="2. Selecione o cargo",
            options=[
                discord.SelectOption(label=nome_do_cargo, value=nome_do_cargo)
                for nome_do_cargo in self.nomes_dos_cargos_permitidos
            ],
        )
        select_cargo.callback = self._ao_selecionar_cargo

        # Botão de adicionar
        botao_adicionar = discord.ui.Button(
            label="Adicionar Cargo", style=discord.ButtonStyle.success
        )
        botao_adicionar.callback = self._ao_clicar_adicionar

        # Botão de remover
        botao_remover = discord.ui.Button(
            label="Remover Cargo", style=discord.ButtonStyle.danger
        )
        botao_remover.callback = self._ao_clicar_remover

        # Monta as linhas de componentes
        linha_do_membro = discord.ui.ActionRow()
        linha_do_membro.add_item(select_membro)

        linha_do_cargo = discord.ui.ActionRow()
        linha_do_cargo.add_item(select_cargo)

        linha_dos_botoes = discord.ui.ActionRow()
        linha_dos_botoes.add_item(botao_adicionar)
        linha_dos_botoes.add_item(botao_remover)

        # Texto do resumo (mostra o que está selecionado no momento)
        resumo_membro = (
            self.candidato_selecionado.mention
            if self.candidato_selecionado
            else "*nenhum*"
        )
        resumo_cargo = self.cargo_selecionado if self.cargo_selecionado else "*nenhum*"

        # Container que agrupa tudo visualmente
        container = discord.ui.Container(
            discord.ui.TextDisplay("# 🛠️ Gerenciamento de Cargos"),
            discord.ui.TextDisplay(
                f"- **Membro selecionado:** {resumo_membro}\n"
                f"- **Cargo selecionado:** {resumo_cargo}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            linha_do_membro,
            linha_do_cargo,
            linha_dos_botoes,
            accent_color=discord.Color.blurple(),
        )

        # Remove o container antigo se já existir, para evitar duplicação
        if hasattr(self, "container"):
            self.remove_item(self.container)

        self.container = container
        self.add_item(self.container)

    async def _ao_selecionar_membro(self, interaction: discord.Interaction):
        """Callback quando o executor seleciona um membro no UserSelect."""
        # O valor vem como string, precisamos converter para int e buscar o membro
        id_do_membro_selecionado = interaction.data["values"][0]
        self.candidato_selecionado = interaction.guild.get_member(
            int(id_do_membro_selecionado)
        )

        # Reconstroi o painel para atualizar o resumo
        self._montar_componentes()
        await interaction.response.edit_message(view=self)

    async def _ao_selecionar_cargo(self, interaction: discord.Interaction):
        """Callback quando o executor seleciona um cargo no Select."""
        self.cargo_selecionado = interaction.data["values"][0]

        # Reconstroi o painel para atualizar o resumo
        self._montar_componentes()
        await interaction.response.edit_message(view=self)

    async def _validar_selecoes(self, interaction: discord.Interaction) -> bool:
        """
        Verifica se o executor já selecionou membro E cargo.
        Retorna True se ambos estão selecionados, False caso contrário.
        """
        if self.candidato_selecionado is None or self.cargo_selecionado is None:
            await interaction.response.send_message(
                "❌ Selecione um membro e um cargo antes de continuar.", ephemeral=True
            )
            return False
        return True

    async def _ao_clicar_adicionar(self, interaction: discord.Interaction):
        """Callback do botão Adicionar Cargo."""
        if not await self._validar_selecoes(interaction):
            return

        # Defer precisar ser feito ANTES de chamar o service,
        # pois o service usa followup.send
        await interaction.response.defer(ephemeral=True)
        await adicionar_cargo(
            interaction, self.candidato_selecionado, self.cargo_selecionado
        )

    async def _ao_clicar_remover(self, interaction: discord.Interaction):
        """Callback do botão Remover Cargo."""
        if not await self._validar_selecoes(interaction):
            return

        # Defer precisar ser feito ANTES de chamar o service,
        # pois o service usa followup.send
        await interaction.response.defer(ephemeral=True)
        await remover_cargo(
            interaction, self.candidato_selecionado, self.cargo_selecionado
        )


class PainelGerenciarCargoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """
    Painel fixo que fica exposto no canal de gerenciamento de cargos.
    Contém um botão que, ao ser clicado, abre a view interativa
    GerenciarCargosView para o usuário que clicou.
    """

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)  # timeout=None = painel permanente

        # Linha de ação com o botão de abrir o gerenciador
        self.linha_do_botao = discord.ui.ActionRow()

        self.botao_abrir_gerenciador = discord.ui.Button(
            label="Gerenciar Cargos",
            style=discord.ButtonStyle.primary,  # CORRIGIDO: azul combina com blurple
            emoji="⚙️",
            custom_id="painel:gerenciar_cargos",
        )

        # Conecta o callback (método que será chamado ao clicar)
        self.botao_abrir_gerenciador.callback = self._ao_clicar_gerenciar
        self.linha_do_botao.add_item(self.botao_abrir_gerenciador)

        # Ícone do servidor para o Thumbnail
        url_do_icone = guild.icon.url if guild.icon else None

        self.container = discord.ui.Container(
            # Título
            discord.ui.TextDisplay("# ⚙️ Painel de Gerenciamento de Cargos"),
            # Separador
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            # Seção com descrição e thumbnail
            discord.ui.Section(
                "> **Sistema para Gerenciar Cargos do Servidor**\n",
                (
                    "- Esse painel é dedicado e de uso exclusivo da Diretoria e GATE.\n"
                    "- Utilizado para **Adicionar** e **Remover** cargos de membros.\n"
                    "- Todo e qualquer uso abusivo desse Sistema pode gerar punições ao executor.\n"
                    "- Toda atividade é registrada, o sistema liberará seu acesso e ajustará os cargos automaticamente.\n"
                    "- Clique no botão abaixo para iniciar um novo gerenciamento.\n"
                ),
                accessory=discord.ui.Thumbnail(url_do_icone) if url_do_icone else None,
            ),
            # Separador
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            # Botão — CORRIGIDO: nome da variável
            self.linha_do_botao,
            accent_color=discord.Color.blurple(),
        )

        # Adiciona o container à view (obrigatório para exibir)
        self.add_item(self.container)

    async def _ao_clicar_gerenciar(self, interaction: discord.Interaction):
        """
        Callback do botão do painel fixo.
        Abre a GerenciarCargosView de forma ephemeral para o usuário que clicou.
        Se o usuário não tiver permissão (nenhum escopo), avisa e não abre.
        """
        # Defer PRIMEIRO — avisa ao Discord que vamos responder,
        # evitando o timeout de 3 segundos (Unknown interaction)
        await interaction.response.defer(ephemeral=True)

        # Verifica se o usuário tem pelo menos um escopo de gerenciamento
        escopos_do_usuario = determinar_escopos(interaction.user)

        if not escopos_do_usuario:
            # Usuário sem permissão — avisa e não abre a view
            await interaction.followup.send(
                "❌ Você não possui permissão para gerenciar cargos.",
                ephemeral=True,
            )
            return

        # Agora sim faz o processamento pesado:
        # criar a view percorre escopos, cargos, etc.
        view_de_gerenciamento = GerenciarCargosView(membro_executor=interaction.user)

        # Usa followup.send porque a resposta inicial já foi usada pelo defer
        await interaction.followup.send(
            view=view_de_gerenciamento,
            ephemeral=True,
        )
