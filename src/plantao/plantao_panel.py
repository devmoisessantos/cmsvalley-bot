import logging
from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select

from src.config import (
    GUILD_ID,
    NOMES_CANAIS_PLANTAO,
    VALOR_MOEDA_INGAME,
    obter_ids_canais_plantao_em_ordem,
)
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from src.plantao.plantao_service import (
    desligar_servico,
    garantir_aware,
    ligar_servico,
    membro_pode_informar_id_manualmente,
    solicitar_troca_moedas,
)
from src.recrutamento.recrutamento_service import resolver_id_fivem
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
    enviar_erro_para_log_erros,
)
from src.utils.formatacao import (
    formatar_dinheiro,
    formatar_hms,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)

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
        self.origem = (
            origem  # "painel" ou "info" — decide o que mostrar depois de ligar
        )

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
                linhas=[
                    "Conecte-se a uma das calls disponíveis para começar a contar tempo."
                ],
                cor=discord.Color.green(),
                incluir_select_call=True,
            )
            await interaction.followup.send(view=card, ephemeral=True)
        else:
            novo_estado = await _buscar_estado(self.membro.id)
            nova_view = InformacoesPlantaoView(self.membro, novo_estado)
            await interaction.followup.send(
                view=nova_view, ephemeral=True
            )  # 👈 sem "resultado_texto" no content


class AcaoServicoView(LoggingViewMixin, discord.ui.LayoutView):
    """Card dinâmico mostrado após ligar/desligar o serviço pelo painel fixo.
    Foco em orientar a próxima ação — não é o card completo de status (isso é InformacoesPlantaoView)."""

    def __init__(
        self,
        titulo: str,
        linhas: list[str],
        cor: discord.Color,
        incluir_select_call: bool = False,
    ):
        super().__init__(timeout=180)

        componentes = [
            discord.ui.TextDisplay(f"# {titulo}"),
            discord.ui.TextDisplay("\n".join(f"`•` {linha}" for linha in linhas)),
        ]

        if incluir_select_call:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
            )
            row = discord.ui.ActionRow()
            row.add_item(self._select_calls())
            componentes.append(row)

        self.container = discord.ui.Container(*componentes, accent_color=cor)
        self.add_item(self.container)

    def _select_calls(self) -> discord.ui.Select:
        opcoes = [
            discord.SelectOption(
                label=NOMES_CANAIS_PLANTAO[canal_id], value=str(canal_id)
            )
            for canal_id in obter_ids_canais_plantao_em_ordem()
        ]
        select = discord.ui.Select(
            placeholder="📞 Escolha uma call para se conectar", options=opcoes
        )
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
            discord.ui.Section(
                "# 🛡️ Central de Plantão\n",
                ("> **Gerencie seu status de serviço e acumule recompensas.**"),
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                "## Sistema de Recompensas\n\n"
                "Utilize os botões abaixo para iniciar ou encerrar seu plantão.\n"
                "**Lembre-se:** você deve estar em uma call de voz para acumular tempo!\n\n\n"
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
            await interaction.response.send_message(
                MENSAGEM_SEM_PERMISSAO, ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        resultado_texto = await ligar_servico(interaction.user, id_fivem)

        if resultado_texto.startswith("✅"):
            card = AcaoServicoView(
                titulo="✅ Entrou em Serviço",
                linhas=[
                    "Conecte-se a uma das calls disponíveis para começar a contar tempo."
                ],
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
        from sqlalchemy import func

        from src.database.models import LogPlantao

        estado = await _buscar_estado(interaction.user.id)
        async with async_session() as session:
            r = await session.execute(
                select(func.coalesce(func.sum(LogPlantao.duracao_segundos), 0)).where(
                    LogPlantao.discord_id == interaction.user.id,
                    LogPlantao.duracao_segundos.is_not(None),
                )
            )
            tempo_total = int(r.scalar_one() or 0)
        view = InformacoesPlantaoView(interaction.user, estado, tempo_total)
        await interaction.response.send_message(view=view, ephemeral=True)


async def _buscar_estado(discord_id: int) -> EstadoPlantao | None:
    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        return resultado.scalar_one_or_none()


class InformacoesPlantaoView(LoggingViewMixin, discord.ui.LayoutView):
    """Status pessoal do plantão — sem coordenação/chamada (isso vive em outros canais)."""

    def __init__(
        self,
        membro: discord.Member,
        estado: EstadoPlantao | None,
        tempo_total_segundos: int = 0,
    ):
        super().__init__(timeout=180)
        self.membro = membro

        online = estado is not None and estado.toggle_ligado
        saldo = estado.saldo_moedas if estado else 0
        segundos_ciclo = estado.segundos_acumulados if estado else 0

        linha_call = None
        tempo_call = 0
        if online and estado.em_call_valida and estado.call_entrada_em:
            entrada = garantir_aware(estado.call_entrada_em)
            tempo_call = int((datetime.now(timezone.utc) - entrada).total_seconds())
            status_texto = f"🟢 Em Serviço (nesta call: {formatar_hms(tempo_call)})"
            nome_call = NOMES_CANAIS_PLANTAO.get(estado.canal_atual_id, "Desconhecida")
            linha_call = f"`📍` Você está em call de plantão: **{nome_call}**"
        elif online:
            status_texto = "🟢 Em Serviço (aguardando conexão em uma call)"
            linha_call = "`📍` Nenhuma call conectada — selecione uma abaixo"
        else:
            status_texto = (
                '🔴 Offline (clique em "Entrar em Serviço" para iniciar o cronômetro)'
            )

        linhas = (
            f"`⏱️` **Status:** {status_texto}\n"
            f"`⏳` **Tempo do ciclo:** `{formatar_hms(segundos_ciclo + tempo_call)}`\n"
            f"`🗓️` **Tempo total (histórico):** `{formatar_hms(tempo_total_segundos)}`\n"
            f"`💰` **Moedas (saldo):** **{saldo}** "
            f"({formatar_dinheiro(saldo * VALOR_MOEDA_INGAME)})\n"
            f"`💵` **Valor por moeda:** {formatar_dinheiro(VALOR_MOEDA_INGAME)} / 30 min"
        )
        if linha_call:
            linhas += f"\n{linha_call}"

        row_botao = discord.ui.ActionRow()
        if online:
            botao = discord.ui.Button(
                label="🔴 Sair do Serviço", style=discord.ButtonStyle.danger
            )
        else:
            botao = discord.ui.Button(
                label="🟢 Entrar em Serviço", style=discord.ButtonStyle.success
            )
        botao.callback = self._callback_toggle
        row_botao.add_item(botao)

        botao_troca = discord.ui.Button(
            label="Trocar moedas",
            style=discord.ButtonStyle.primary,
            emoji="💵",
            disabled=(saldo <= 0),
        )
        botao_troca.callback = self._callback_trocar_moedas
        row_botao.add_item(botao_troca)

        avatar_url = membro.display_avatar.url
        componentes = [
            discord.ui.Section(
                f"# 🛡️ Plantão — {membro.display_name}\n{linhas}",
                accessory=discord.ui.Thumbnail(avatar_url),
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row_botao,
        ]

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
            discord.SelectOption(
                label=NOMES_CANAIS_PLANTAO[canal_id], value=str(canal_id)
            )
            for canal_id in obter_ids_canais_plantao_em_ordem()
        ]
        select_menu = discord.ui.Select(
            placeholder="📍 Clique aqui para trocar de call", options=opcoes
        )
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
            await interaction.response.send_message(
                MENSAGEM_SEM_PERMISSAO, ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await ligar_servico(interaction.user, id_fivem)
        novo_estado = await _buscar_estado(interaction.user.id)
        nova_view = InformacoesPlantaoView(interaction.user, novo_estado)
        await interaction.edit_original_response(view=nova_view)

    async def _callback_trocar_moedas(self, interaction: discord.Interaction):
        estado = await _buscar_estado(interaction.user.id)
        saldo = estado.saldo_moedas if estado else 0
        if saldo <= 0:
            await responder_aviso(
                interaction,
                titulo="Sem moedas",
                linhas=["Você não tem moedas para trocar."],
            )
            return
        await interaction.response.send_modal(
            ModalTrocarMoedasPlantao(saldo_disponivel=saldo)
        )

    async def _callback_selecionar_call(self, interaction: discord.Interaction):
        canal_id = int(interaction.data["values"][0])
        nome_call = NOMES_CANAIS_PLANTAO.get(canal_id, "Call")

        row_link = discord.ui.ActionRow()
        row_link.add_item(
            discord.ui.Button(
                label=f"🔗 Conectar em {nome_call}",
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/channels/{GUILD_ID}/{canal_id}",
            )
        )
        view_link = discord.ui.LayoutView(timeout=120)
        view_link.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"`📍` Call selecionada: **{nome_call}**\n"
                    "Entre na call pelo botão abaixo para começar a contar tempo."
                ),
                row_link,
                accent_color=discord.Color.blurple(),
            )
        )
        await interaction.response.send_message(view=view_link, ephemeral=True)


class ModalTrocarMoedasPlantao(
    LoggingModalMixin, discord.ui.Modal, title="💵 Trocar moedas por dinheiro"
):
    quantidade_input = discord.ui.TextInput(
        label="Quantidade de moedas",
        placeholder="Ex: 5",
        required=True,
        max_length=4,
    )

    def __init__(self, saldo_disponivel: int):
        super().__init__()
        self.saldo_disponivel = saldo_disponivel
        self.quantidade_input.placeholder = f"Saldo disponível: {saldo_disponivel}"

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self._executar_troca(interaction)
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interaction.guild,
                "ModalTrocarMoedasPlantao — falha no on_submit",
                erro,
                contexto="ModalTrocarMoedasPlantao.on_submit",
                usuario=interaction.user,
            )
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            await responder_erro(
                interaction,
                titulo="Erro na troca de moedas",
                linhas=[
                    "Ocorreu um erro inesperado. A equipe foi notificada no log de erros.",
                    f"`{type(erro).__name__}: {erro}`",
                ],
                delay=20,
            )

    async def _executar_troca(self, interaction: discord.Interaction):
        bruto = self.quantidade_input.value.strip()
        if not bruto.isdigit():
            await responder_erro(
                interaction,
                titulo="Quantidade inválida",
                linhas=["Informe um número inteiro de moedas."],
            )
            return

        quantidade = int(bruto)
        if not isinstance(interaction.user, discord.Member):
            await responder_erro(
                interaction,
                titulo="Contexto inválido",
                linhas=["Use este recurso dentro do servidor."],
            )
            return

        await interaction.response.defer(ephemeral=True)

        ok, mensagem, saldo_restante, valor_ingame = await solicitar_troca_moedas(
            interaction.user,
            quantidade,
        )
        if not ok:
            await responder_erro(
                interaction,
                titulo="Troca não realizada",
                linhas=[mensagem],
            )
            return

        estado = await _buscar_estado(interaction.user.id)
        id_fivem = estado.id_fivem if estado else None
        if not id_fivem:
            id_fivem = await resolver_id_fivem(interaction.user.id)

        postou = False
        if interaction.guild is not None:
            from src.financas.financas_service import publicar_solicitacao_troca_moedas

            postou = await publicar_solicitacao_troca_moedas(
                interaction.guild,
                membro=interaction.user,
                id_fivem=id_fivem,
                quantidade_moedas=quantidade,
                valor_ingame=valor_ingame,
            )

        if postou:
            await responder_sucesso(
                interaction,
                titulo="Solicitação enviada",
                linhas=[
                    mensagem,
                    "Pedido publicado no **canal de finanças** (com botão de confirmação).",
                    "Aguarde a equipe processar o pagamento in-game.",
                ],
                delay=15,
            )
        else:
            await responder_aviso(
                interaction,
                titulo="Moedas debitadas — finanças offline",
                linhas=[
                    mensagem,
                    "Não foi possível postar no canal de finanças.",
                    "Detalhes foram enviados ao **log de erros**.",
                    "Avise a diretoria manualmente se precisar.",
                ],
                delay=20,
            )

        try:
            novo_estado = await _buscar_estado(interaction.user.id)
            nova_view = InformacoesPlantaoView(interaction.user, novo_estado)
            await interaction.edit_original_response(view=nova_view)
        except discord.HTTPException:
            pass
