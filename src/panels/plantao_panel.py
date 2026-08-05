import discord
import logging

from datetime import datetime, timezone
from sqlalchemy import select

from src.plantao.plantao_service import (
    ligar_servico, desligar_servico, garantir_aware,
    resolver_id_fivem, membro_pode_informar_id_manualmente,
)
from src.config import (
    GUILD_ID, CARGOS, NOMES_CANAIS_PLANTAO, VALOR_MOEDA_INGAME, obter_ids_canais_plantao_em_ordem,
)
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from src.utils.error_handling import LoggingViewMixin
from src.utils.formatacao import formatar_hms, formatar_dinheiro
from src.panels.chamada_panel import PainelCoordenacaoView

logger = logging.getLogger(__name__)

MENSAGEM_SEM_PERMISSAO = (
    "❌ Você não está registrado como membro aprovado do hospital "
    "(Nemhum Recrutamento aprovado). Não é possível iniciar o plantão."
)

class ModalInformarIDFivem(discord.ui.Modal, title="Confirme seu ID FiveM"):
    id_fivem_input = discord.ui.TextInput(
        label="Seu Identificador (ID FiveM)",
        placeholder="Ex: 54623",
        max_length=6,
        min_length=1,
        required=True,
    )

    def __init__(self, membro: discord.Member, origem: str):
        super().__init__()
        self.membro = membro
        self.origem = origem  # "painel" ou "info" — decide o que mostrar depois de ligar

    async def on_submit(self, interaction: discord.Interaction):
        valor = self.id_fivem_input.value.strip()

        if not valor.isdigit() or len(valor) > 6:
            await interaction.response.send_message(
                "❌ ID FiveM inválido. Deve conter apenas números, no máximo 6 dígitos.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        resultado_texto = await ligar_servico(self.membro, valor)

        if not resultado_texto.startswith("✅"):
            await interaction.followup.send(resultado_texto, ephemeral=True)
            return

        if self.origem == "painel":
            card = AcaoServicoView(
                titulo="✅ Entrou em Serviço",
                linhas=["Conecte-se a uma das calls disponíveis para começar a contar tempo."],
                cor=discord.Color.green(),
                incluir_select_call=True,
            )
            await interaction.followup.send(view=card, ephemeral=True)
        else:
            novo_estado = await _buscar_estado(self.membro.id)
            nova_view = InformacoesPlantaoView(self.membro, novo_estado)
            await interaction.followup.send(view=nova_view, ephemeral=True)  # 👈 sem "resultado_texto" no content

class AcaoServicoView(LoggingViewMixin, discord.ui.LayoutView):
    """Card dinâmico mostrado após ligar/desligar o serviço pelo painel fixo.
    Foco em orientar a próxima ação — não é o card completo de status (isso é InformacoesPlantaoView)."""

    def __init__(self, titulo: str, linhas: list[str], cor: discord.Color, incluir_select_call: bool = False):
        super().__init__(timeout=180)

        componentes = [
            discord.ui.TextDisplay(f"# {titulo}"),
            discord.ui.TextDisplay("\n".join(f"`•` {linha}" for linha in linhas)),
        ]

        if incluir_select_call:
            componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
            row = discord.ui.ActionRow()
            row.add_item(self._select_calls())
            componentes.append(row)

        self.container = discord.ui.Container(*componentes, accent_color=cor)
        self.add_item(self.container)

    def _select_calls(self) -> discord.ui.Select:
        opcoes = [
            discord.SelectOption(label=NOMES_CANAIS_PLANTAO[canal_id], value=str(canal_id))
            for canal_id in obter_ids_canais_plantao_em_ordem()
        ]
        select = discord.ui.Select(placeholder="📞 Escolha uma call para se conectar", options=opcoes)
        select.callback = self._callback_selecionar_call
        return select

    async def _callback_selecionar_call(self, interaction: discord.Interaction):
        canal_id = int(interaction.data["values"][0])
        nome_call = NOMES_CANAIS_PLANTAO.get(canal_id, "Call")

        botao_link = discord.ui.Button(
            label=f"🔗 Conectar em {nome_call}",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{GUILD_ID}/{canal_id}",
        )

        row_link = discord.ui.ActionRow()
        row_link.add_item(botao_link)

        container_link = discord.ui.Container(
            discord.ui.TextDisplay(f"Selecionado: **{nome_call}**"),
            row_link,
            accent_color=discord.Color.blurple(),
        )

        view_link = discord.ui.LayoutView(timeout=None)
        view_link.add_item(container_link)

        await interaction.response.edit_message(view=view_link)

class PainelPlantaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        row_botoes = discord.ui.ActionRow()
        row_botoes.add_item(self._botao_toggle())
        row_botoes.add_item(self._botao_informacoes())

        icon_url = guild.icon.url if guild.icon else None

        container = discord.ui.Container(
            discord.ui.TextDisplay(
                "# 🛡️ Central de Plantão\n"
                "> **Gerencie seu status de serviço e acumule recompensas.**"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.Section(
                "## Sistema de Recompensas",
                (
                    "Utilize os botões abaixo para iniciar ou encerrar seu plantão.\n"
                    "**Lembre-se:** você deve estar em uma call de voz para acumular tempo!"
                ),
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),
            discord.ui.TextDisplay(
                "💰 **Recompensa:** 1 Moeda (Valor: $100.000) a cada **30 min**.\n"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row_botoes,
            accent_color=discord.Color.green(),
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
        
        estado_antes = await _buscar_estado(interaction.user.id)
        ja_ligado = estado_antes is not None and estado_antes.toggle_ligado

        if ja_ligado:
            await interaction.response.defer(ephemeral=True)
            resultado_texto = await desligar_servico(interaction.user)

            if resultado_texto.startswith("✅"):
                card = AcaoServicoView(
                    titulo="🔴 Saiu de Serviço",
                    linhas=["Seu cronômetro foi encerrado.", "Obrigado pelo plantão!"],
                    cor=discord.Color.red(),
                )
                await interaction.followup.send(view=card, ephemeral=True)
            else:
                await interaction.followup.send(resultado_texto, ephemeral=True)
            return

        id_fivem = await resolver_id_fivem(interaction.user.id)

        if id_fivem is None:
            if membro_pode_informar_id_manualmente(interaction.user):
                await interaction.response.send_modal(
                    ModalInformarIDFivem(interaction.user, origem="painel")
                )
                return
            await interaction.response.send_message(MENSAGEM_SEM_PERMISSAO, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        resultado_texto = await ligar_servico(interaction.user, id_fivem)

        if resultado_texto.startswith("✅"):
            card = AcaoServicoView(
                titulo="✅ Entrou em Serviço",
                linhas=["Conecte-se a uma das calls disponíveis para começar a contar tempo."],
                cor=discord.Color.green(),
                incluir_select_call=True,
            )
            await interaction.followup.send(view=card, ephemeral=True)
        else:
            await interaction.followup.send(resultado_texto, ephemeral=True)

    def _botao_informacoes(self) -> discord.ui.Button:
        botao = discord.ui.Button(
            label="📊 Ver Informações",
            style=discord.ButtonStyle.secondary,
            custom_id="plantao:ver_info",
        )
        botao.callback = self._callback_ver_informacoes
        return botao


    async def _callback_ver_informacoes(self, interaction: discord.Interaction):
        estado = await _buscar_estado(interaction.user.id)
        view = InformacoesPlantaoView(interaction.user, estado)
        await interaction.response.send_message(view=view, ephemeral=True)


async def _buscar_estado(discord_id: int) -> EstadoPlantao | None:
    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        return resultado.scalar_one_or_none()


class InformacoesPlantaoView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, membro: discord.Member, estado: EstadoPlantao | None):
        super().__init__(timeout=180)
        self.membro = membro

        online = estado is not None and estado.toggle_ligado
        saldo = estado.saldo_moedas if estado else 0

        linha_call = None
        if online and estado.em_call_valida and estado.call_entrada_em:
            entrada = garantir_aware(estado.call_entrada_em)
            decorrido = int((datetime.now(timezone.utc) - entrada).total_seconds())
            status_texto = f"🟢 Em Serviço (Tempo atual: {formatar_hms(decorrido)})"
            nome_call = NOMES_CANAIS_PLANTAO.get(estado.canal_atual_id, "Desconhecida")
            linha_call = f"`📍` Call conectada: {nome_call}"
        elif online:
            status_texto = "🟢 Em Serviço (aguardando conexão em uma call)"
            linha_call = "`📍` Nenhuma call conectada — selecione uma abaixo"
        else:
            status_texto = '🔴 Offline (clique em "Entrar em Serviço" para iniciar o cronômetro)'

        linhas = (
            f"`💰` **Recompensa:** Ganhe 1 moeda (Valor: {formatar_dinheiro(VALOR_MOEDA_INGAME)}) a cada **30 minutos**.\n"
            f"`💰` **Saldo:** {saldo} moedas (Total: {formatar_dinheiro(saldo * VALOR_MOEDA_INGAME)})\n"
            f"`⏱️` **Seu status:** {status_texto}"
        )
        if linha_call:
            linhas += f"\n{linha_call}"


        row_botao = discord.ui.ActionRow()
        if online:
            botao = discord.ui.Button(label="🔴 Sair do Serviço", style=discord.ButtonStyle.danger)
        else:
            botao = discord.ui.Button(label="🟢 Entrar em Serviço", style=discord.ButtonStyle.success)
        botao.callback = self._callback_toggle
        row_botao.add_item(botao)

        componentes = [
            discord.ui.TextDisplay("# 🛡️ Sistema de Recompensas"),
            discord.ui.TextDisplay(linhas),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row_botao,
        ]

        # dentro de InformacoesPlantaoView.__init__, depois de montar row_botao:
        cargo_doutor_id = CARGOS.get("🥼・Doutor")
        if cargo_doutor_id and any(cargo.id == cargo_doutor_id for cargo in membro.roles):
            modo_texto = "🧭 Desativar Modo Coordenação" if (estado and estado.modo_coordenacao) else "🧭 Ativar Modo Coordenação"
            botao_modo = discord.ui.Button(label=modo_texto, style=discord.ButtonStyle.secondary)
            botao_modo.callback = self._callback_alternar_modo_coordenacao
            row_botao.add_item(botao_modo)

        if online:
            row_select = discord.ui.ActionRow()
            row_select.add_item(self._select_calls())
            componentes.append(row_select)

        self.container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.green() if online else discord.Color.red(),
        )
        self.add_item(self.container)

    def _select_calls(self) -> discord.ui.Select:
        opcoes = [
            discord.SelectOption(label=NOMES_CANAIS_PLANTAO[canal_id], value=str(canal_id))
            for canal_id in obter_ids_canais_plantao_em_ordem()
        ]
        select_menu = discord.ui.Select(placeholder="📍 Clique aqui para trocar de call", options=opcoes)
        select_menu.callback = self._callback_selecionar_call
        return select_menu

    async def _callback_toggle(self, interaction: discord.Interaction):
        estado_antes = await _buscar_estado(interaction.user.id)
        ja_ligado = estado_antes is not None and estado_antes.toggle_ligado

        if ja_ligado:
            await interaction.response.defer(ephemeral=True)
            await desligar_servico(interaction.user)
            novo_estado = await _buscar_estado(interaction.user.id)
            nova_view = InformacoesPlantaoView(interaction.user, novo_estado)
            await interaction.edit_original_response(view=nova_view)
            return

        id_fivem = await resolver_id_fivem(interaction.user.id)

        if id_fivem is None:
            if membro_pode_informar_id_manualmente(interaction.user):
                await interaction.response.send_modal(
                    ModalInformarIDFivem(interaction.user, origem="info")
                )
                return
            await interaction.response.send_message(MENSAGEM_SEM_PERMISSAO, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await ligar_servico(interaction.user, id_fivem)
        novo_estado = await _buscar_estado(interaction.user.id)
        nova_view = InformacoesPlantaoView(interaction.user, novo_estado)
        await interaction.edit_original_response(view=nova_view)

    async def _callback_selecionar_call(self, interaction: discord.Interaction):
        canal_id = int(interaction.data["values"][0])
        nome_call = NOMES_CANAIS_PLANTAO.get(canal_id, "Call")

        view_link = discord.ui.View(timeout=None)
        botao_link = discord.ui.Button(
            label=f"🔗 Conectar em {nome_call}",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{GUILD_ID}/{canal_id}",
        )
        view_link.add_item(botao_link)
        await interaction.response.send_message(view=view_link, ephemeral=True)


    async def _callback_alternar_modo_coordenacao(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == interaction.user.id)
            )
            estado = resultado.scalar_one_or_none()
            if estado is None:
                estado = EstadoPlantao(discord_id=interaction.user.id)
                session.add(estado)

            estado.modo_coordenacao = not estado.modo_coordenacao
            ativado = estado.modo_coordenacao
            await session.commit()

        if ativado:
            
            nova_view = await PainelCoordenacaoView.construir(interaction.user)
        else:
            novo_estado = await _buscar_estado(interaction.user.id)
            nova_view = InformacoesPlantaoView(interaction.user, novo_estado)

        await interaction.edit_original_response(view=nova_view)