"""
Painel de plantao: bater ponto de entrada e de saida.

E o painel que o membro mais usa no servidor. Tem tres partes:
- `PainelPlantaoLayout`: o card fixo do canal, com os botoes.
- `AcaoServicoView`: a confirmacao depois do clique.
- `InformacoesPlantaoView`: o resumo de horas da pessoa, com tempo do
  plantao atual atualizado ao vivo enquanto o card estiver aberto.

`ModalInformarIDFivem` aparece quando o bot ainda nao sabe o ID FiveM de quem
esta batendo ponto. Sem esse ID, a hora nao pode ser lancada, por isso o
formulario e obrigatorio antes de continuar.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from sqlalchemy import select

from src.config import (
    GUILD_ID,
    NOMES_CANAIS_PLANTAO,
    VALOR_MOEDA_INGAME,
    obter_ids_canais_plantao_em_ordem,
)
from src.database.conexao import async_session
from src.database.models import EstadoPlantao
from src.plantao.plantao_service import (
    calcular_segundos_historico_fechado,
    calcular_segundos_plantao_atual,
    desligar_servico,
    ligar_servico,
    membro_pode_informar_id_manualmente,
    solicitar_troca_moedas,
)
from src.recrutamento.recrutamento_service import resolver_id_fivem
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
    enviar_erro_para_log_erros,
    ignorar_falha_cosmetica,
)
from src.utils.formatacao import (
    formatar_dinheiro,
    formatar_hms,
)
from src.utils.mensagens import (
    editar_mensagem_original,
    responder_aviso,
    responder_erro,
    responder_info,
    responder_sucesso,
    responder_view,
)

# Intervalo do refresh ao vivo do card de informações (segundos).
# 1s atende o pedido de "segundo a segundo"; se o Discord limitar a edição,
# o loop engole o 429 e tenta de novo no próximo ciclo.
INTERVALO_ATUALIZACAO_AO_VIVO_SEGUNDOS = 1

logger = logging.getLogger(__name__)

MENSAGEM_SEM_PERMISSAO = (
    "❌ Você não está registrado como membro aprovado do hospital "
    "(Nemhum Recrutamento aprovado). Não é possível iniciar o plantão."
)


async def _adiar_interacao_efemera(interacao: discord.Interaction) -> bool:
    """
    Confirma a interação no Discord o mais cedo possível.

    O Discord cancela a interação se ninguém responder em cerca de 3 segundos
    (erro 10062 Unknown interaction). Qualquer consulta ao banco ou FiveM
    precisa acontecer DEPOIS deste defer.

    Devolve False quando a interação já expirou ou já foi respondida de um
    jeito que impede continuar — nesse caso o callback deve só sair.
    """
    if interacao.response.is_done():
        return True
    try:
        await interacao.response.defer(ephemeral=True)
        return True
    except discord.NotFound:
        # Token já inválido (lentidão, clique duplicado, reinício no meio).
        logger.warning(
            "Interação expirada antes do defer (usuário %s).",
            getattr(interacao.user, "id", "?"),
        )
        return False
    except discord.HTTPException as erro_http:
        logger.warning(
            "Falha ao adiar interação de plantão: %s",
            erro_http,
        )
        return False


class _ViewPedirIdFivem(LoggingViewMixin, discord.ui.LayoutView):
    """
    Botão que abre o modal de ID FiveM.

    Usado depois de um defer: não dá para abrir modal na mesma interação
    já adiada, então mandamos este card e o membro clica de novo.
    """

    def __init__(self, origem: str):
        super().__init__(timeout=120)
        self.origem = origem
        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Informar ID FiveM",
            style=discord.ButtonStyle.primary,
            emoji="🆔",
        )
        botao.callback = self._ao_abrir_modal
        linha.add_item(botao)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# ID FiveM necessário\n"
                    "Ainda não temos o seu identificador no banco.\n"
                    "Clique no botão abaixo e informe o ID para entrar em serviço."
                ),
                linha,
                accent_color=discord.Color.orange(),
            )
        )

    async def _ao_abrir_modal(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Só no servidor",
                linhas=["Use este botão dentro do Discord do hospital."],
            )
            return
        await interacao.response.send_modal(
            ModalInformarIDFivem(membro, origem=self.origem)
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
        """Valida o FiveM informado manualmente antes de iniciar o plantão.

        Só permite números curtos e entrega a ativação ao serviço central, que grava
        o estado necessário. A origem define qual painel será atualizado, para que o
        membro continue no fluxo que iniciou em vez de receber uma resposta desconexa.
        """
        valor = self.id_fivem_input.value.strip()

        if not valor.isdigit() or len(valor) > 6:
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    "ID FiveM inválido. Deve conter apenas números, no máximo 6 "
                    "dígitos.",
                ],
            )
            return

        await interaction.response.defer(ephemeral=True)
        resultado_texto = await ligar_servico(self.membro, valor)

        if not resultado_texto.startswith("✅"):
            await responder_info(
                interaction,
                titulo="Resultado do plantão",
                linhas=[
                    resultado_texto,
                ],
            )
            return

        if self.origem == "painel":
            card = AcaoServicoView(
                titulo="✅ Entrou em Serviço",
                linhas=[
                    "Conecte-se a uma das calls disponíveis para começar a contar "
                    "tempo."
                ],
                cor=discord.Color.green(),
                incluir_select_call=True,
            )
            await responder_view(
                interaction,
                card,
                ephemeral=True,
            )
        else:
            novo_estado = await _buscar_estado(self.membro.id)
            tempo_ciclo = await calcular_segundos_plantao_atual(
                self.membro.id,
                novo_estado,
            )
            tempo_total = await calcular_segundos_historico_fechado(self.membro.id)
            nova_view = InformacoesPlantaoView(
                self.membro,
                novo_estado,
                tempo_total_segundos=tempo_total,
                tempo_ciclo_segundos=tempo_ciclo,
            )
            mensagem_efemera = await responder_view(
                interaction,
                nova_view,
                ephemeral=True,
            )
            nova_view.iniciar_atualizacao_ao_vivo(mensagem_efemera)


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
        select.callback = self._ao_selecionar_call
        return select

    async def _ao_selecionar_call(self, interaction: discord.Interaction):
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

        await editar_mensagem_original(
            interaction,
            view=view_link,
        )


class PainelPlantaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        url_icone = None
        if guild is not None and guild.icon is not None:
            url_icone = guild.icon.url

        componentes: list = []

        # Bloco 1: cabeçalho com ícone do servidor (quando existir)
        texto_cabecalho = (
            "# 🛡️ Central de Plantão\n"
            "> ⏱️ Sistema de Recompensas por Serviço\n"
            "Gerencie seu status de serviço e acumule recompensas enquanto "
            "estiver ativo no plantão."
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

        # Bloco 3: regras do plantão
        componentes.append(
            discord.ui.TextDisplay(
                "### 📌 Regras do Plantão\n"
                "> ⚠️ **Regra obrigatória:** Você deve estar em uma "
                "**call de voz** para acumular tempo de serviço!\n"
                "- ✅ Permaneça na **call de voz** durante todo o período\n"
                "- ✅ Acumule tempo continuamente para receber recompensas\n"
                "- ❌ Não saia da call sem encerrar o plantão\n"
                "- ❌ Tempo ocioso sem interação pode ser desconsiderado"
            )
        )

        # Bloco 4: separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Bloco 5: sistema de recompensas
        componentes.append(
            discord.ui.TextDisplay(
                "## 💰 Sistema de Recompensas\n"
                "- | ⏱️ 30 minutos · 🪙 **1 Moeda** (Valor: $100.000)\n"
                "- | ⏱️ 1 hora · 🪙 **2 Moedas** (Valor: $200.000)\n"
                "- | ⏱️ 2 horas · 🪙 **4 Moedas** (Valor: $400.000)\n\n"
                "> 📈 **Bônus acumulativo:** Quanto mais tempo, maior sua "
                "recompensa!\n"
                "-# Utilize os botões abaixo para gerenciar seu serviço."
            )
        )

        # Bloco 6: separador antes dos botões
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Botões (inalterados)
        linha_botoes = discord.ui.ActionRow()
        linha_botoes.add_item(self._botao_toggle())
        linha_botoes.add_item(self._botao_informacoes())
        componentes.append(linha_botoes)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.green(),
            )
        )

    def _botao_toggle(self) -> discord.ui.Button:
        botao = discord.ui.Button(
            label="🔄 Entrar/Sair de Serviço",
            style=discord.ButtonStyle.primary,
            custom_id="plantao:toggle",
        )
        botao.callback = self._ao_toggle
        return botao

    async def _ao_toggle(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await responder_erro(
                interaction,
                titulo="Comando indisponível aqui",
                linhas=[
                    "Este comando só pode ser usado em servidores.",
                ],
            )
            return

        # Responde ao Discord antes de qualquer consulta ao banco/FiveM.
        # Sem isso, em lentidão aparece erro 10062 (Unknown interaction).
        if not await _adiar_interacao_efemera(interaction):
            return

        estado_antes = await _buscar_estado(interaction.user.id)
        ja_ligado = estado_antes is not None and estado_antes.toggle_ligado

        if ja_ligado:
            resultado_texto = await desligar_servico(interaction.user)

            if resultado_texto.startswith("✅"):
                card = AcaoServicoView(
                    titulo="🔴 Saiu de Serviço",
                    linhas=["Seu cronômetro foi encerrado.", "Obrigado pelo plantão!"],
                    cor=discord.Color.red(),
                )
                await responder_view(
                    interaction,
                    card,
                    ephemeral=True,
                )
            else:
                await responder_info(
                    interaction,
                    titulo="Resultado do plantão",
                    linhas=[
                        resultado_texto,
                    ],
                )
            return

        id_fivem = await resolver_id_fivem(interaction.user.id)

        if id_fivem is None:
            if membro_pode_informar_id_manualmente(interaction.user):
                # Depois do defer não dá para abrir modal na mesma interação.
                await responder_view(
                    interaction,
                    _ViewPedirIdFivem(origem="painel"),
                    ephemeral=True,
                )
                return
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    MENSAGEM_SEM_PERMISSAO,
                ],
            )
            return

        resultado_texto = await ligar_servico(interaction.user, id_fivem)

        if resultado_texto.startswith("✅"):
            card = AcaoServicoView(
                titulo="✅ Entrou em Serviço",
                linhas=[
                    "Conecte-se a uma das calls disponíveis para começar a contar "
                    "tempo."
                ],
                cor=discord.Color.green(),
                incluir_select_call=True,
            )
            await responder_view(
                interaction,
                card,
                ephemeral=True,
            )
        else:
            await responder_info(
                interaction,
                titulo="Resultado do plantão",
                linhas=[
                    resultado_texto,
                ],
            )

    def _botao_informacoes(self) -> discord.ui.Button:
        botao = discord.ui.Button(
            label="📊 Ver Informações",
            style=discord.ButtonStyle.secondary,
            custom_id="plantao:ver_info",
        )
        botao.callback = self._ao_ver_informacoes
        return botao

    async def _ao_ver_informacoes(self, interaction: discord.Interaction):
        if not await _adiar_interacao_efemera(interaction):
            return
        estado = await _buscar_estado(interaction.user.id)
        tempo_ciclo = await calcular_segundos_plantao_atual(
            interaction.user.id,
            estado,
        )
        tempo_total = await calcular_segundos_historico_fechado(interaction.user.id)
        view = InformacoesPlantaoView(
            interaction.user,
            estado,
            tempo_total_segundos=tempo_total,
            tempo_ciclo_segundos=tempo_ciclo,
        )
        # A mensagem efêmera do followup é o único alvo seguro de edição.
        # edit_original_response nessa interação reescreveria o painel público:
        # o defer em botão de componente (sem thinking) trata a mensagem do
        # componente como "original".
        mensagem_efemera = await responder_view(
            interaction,
            view,
            ephemeral=True,
        )
        view.iniciar_atualizacao_ao_vivo(mensagem_efemera)


async def _buscar_estado(discord_id: int) -> EstadoPlantao | None:
    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        return resultado.scalar_one_or_none()


class InformacoesPlantaoView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Status pessoal do plantão — sem coordenação/chamada (isso vive em outros canais).

    Enquanto o membro está em serviço, o card é republicado a cada segundo com
    o tempo do plantão atual (segmento aberto + logs do serviço). O histórico
    só muda quando um segmento fecha (sair da call ou do serviço).

    O loop ao vivo edita a mensagem efêmera do card (a do followup), nunca a
    mensagem do painel público. Usar edit_original_response da interação do
    botão do painel reescreveria o painel fixo do canal.
    """

    def __init__(
        self,
        membro: discord.Member,
        estado: EstadoPlantao | None,
        tempo_total_segundos: int = 0,
        tempo_ciclo_segundos: int = 0,
    ):
        super().__init__(timeout=300)
        self.membro = membro
        self.estado = estado
        self.tempo_total_segundos = int(tempo_total_segundos or 0)
        self.tempo_ciclo_segundos = int(tempo_ciclo_segundos or 0)
        self._tarefa_ao_vivo: asyncio.Task | None = None
        # Mensagem efêmera do card de informações — nunca a mensagem do painel.
        self._mensagem_ao_vivo: discord.Message | None = None
        self._parar_atualizacao = False

        self._montar_conteudo()

    def _montar_conteudo(self) -> None:
        """
        Reconstrói o container com os tempos atuais.

        LayoutView não permite trocar itens depois de criado com a mesma
        facilidade de um View clássico; por isso o loop ao vivo monta uma
        view nova a cada tick. Este método serve à montagem inicial.
        """
        # Limpa itens anteriores se houver reuso
        for item in list(self.children):
            self.remove_item(item)

        estado = self.estado
        online = estado is not None and estado.toggle_ligado
        saldo = estado.saldo_moedas if estado else 0

        linha_call = None
        if online and estado.em_call_valida:
            cronometro_rodando = estado.segmento_iniciado_em is not None
            if cronometro_rodando:
                status_texto = "🟢 Em Serviço (cronômetro rodando nesta call)"
            else:
                status_texto = "🟡 Em Serviço (cronômetro pausado — surdo / AFK)"
            nome_call = NOMES_CANAIS_PLANTAO.get(
                estado.canal_atual_id,
                "Desconhecida",
            )
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
            f"`⏳` **Tempo do ciclo:** "
            f"`{formatar_hms(self.tempo_ciclo_segundos)}`\n"
            f"`🗓️` **Tempo total (histórico):** "
            f"`{formatar_hms(self.tempo_total_segundos)}`\n"
            f"`💰` **Moedas (saldo):** **{saldo}** "
            f"({formatar_dinheiro(saldo * VALOR_MOEDA_INGAME)})\n"
            f"`💵` **Valor por moeda:** "
            f"{formatar_dinheiro(VALOR_MOEDA_INGAME)} / 30 min"
        )
        if linha_call:
            linhas += f"\n{linha_call}"

        row_botao = discord.ui.ActionRow()
        if online:
            botao = discord.ui.Button(
                label="🔴 Sair do Serviço",
                style=discord.ButtonStyle.danger,
            )
        else:
            botao = discord.ui.Button(
                label="🟢 Entrar em Serviço",
                style=discord.ButtonStyle.success,
            )
        botao.callback = self._ao_toggle
        row_botao.add_item(botao)

        botao_carteira = discord.ui.Button(
            label="Carteira",
            style=discord.ButtonStyle.primary,
            emoji="💰",
        )
        botao_carteira.callback = self._ao_carteira
        row_botao.add_item(botao_carteira)

        url_do_avatar = self.membro.display_avatar.url
        secao = discord.ui.Section(
            f"# 🛡️ Plantão — {self.membro.display_name}\n{linhas}",
            accessory=discord.ui.Thumbnail(url_do_avatar),
        )

        componentes: list = [
            secao,
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
                label=NOMES_CANAIS_PLANTAO[canal_id],
                value=str(canal_id),
            )
            for canal_id in obter_ids_canais_plantao_em_ordem()
        ]
        select_menu = discord.ui.Select(
            placeholder="📍 Clique aqui para trocar de call",
            options=opcoes,
        )
        select_menu.callback = self._ao_selecionar_call
        return select_menu

    def iniciar_atualizacao_ao_vivo(
        self,
        mensagem_do_card: discord.Message,
    ) -> None:
        """
        Liga o loop que republica o card a cada segundo enquanto em serviço.

        Recebe a mensagem efêmera do card de informações (a do followup),
        nunca a interação do botão do painel público. Editar a resposta
        "original" daquela interação reescreveria o painel fixo do canal.

        Fora de serviço o ciclo já está fechado; não há o que atualizar ao vivo.
        """
        em_servico = self.estado is not None and self.estado.toggle_ligado
        if not em_servico:
            return

        self._mensagem_ao_vivo = mensagem_do_card
        self._parar_atualizacao = False
        if self._tarefa_ao_vivo is not None and not self._tarefa_ao_vivo.done():
            self._tarefa_ao_vivo.cancel()
        self._tarefa_ao_vivo = asyncio.create_task(self._loop_atualizacao_ao_vivo())

    def _parar_loop_ao_vivo(self) -> None:
        self._parar_atualizacao = True
        if self._tarefa_ao_vivo is not None and not self._tarefa_ao_vivo.done():
            self._tarefa_ao_vivo.cancel()
        self._tarefa_ao_vivo = None

    async def _republicar_card_ao_vivo(
        self,
        nova_view: "InformacoesPlantaoView",
    ) -> bool:
        """
        Troca o conteúdo da mensagem efêmera do card.

        Devolve True se a edição funcionou, False se a mensagem sumiu ou
        a edição falhou de forma definitiva (aí o loop deve parar).
        """
        mensagem = self._mensagem_ao_vivo
        if mensagem is None:
            return False
        try:
            await mensagem.edit(view=nova_view)
            return True
        except discord.NotFound:
            return False
        except discord.HTTPException as erro_http:
            logger.warning(
                "Falha ao atualizar card de plantao ao vivo de %s: %s",
                self.membro.id,
                erro_http,
            )
            # Rate limit ou falha transitória: o loop tenta de novo no próximo tick
            return True

    async def _loop_atualizacao_ao_vivo(self) -> None:
        """
        Atualiza o card enquanto o membro está em serviço e a view viva.

        Fora de serviço o histórico já está fechado e o ciclo fica em zero;
        o loop para sozinho. Em serviço, relê o estado e recalcula os tempos.

        Sempre edita a mensagem efêmera guardada em `_mensagem_ao_vivo`,
        nunca a mensagem do painel público do canal.
        """
        try:
            while not self._parar_atualizacao:
                await asyncio.sleep(INTERVALO_ATUALIZACAO_AO_VIVO_SEGUNDOS)

                if self._parar_atualizacao:
                    return
                if self._mensagem_ao_vivo is None:
                    return

                estado = await _buscar_estado(self.membro.id)
                if estado is None or not estado.toggle_ligado:
                    # Saiu de serviço por outro caminho: um último refresh e para
                    tempo_total = await calcular_segundos_historico_fechado(
                        self.membro.id
                    )
                    nova_view = InformacoesPlantaoView(
                        self.membro,
                        estado,
                        tempo_total_segundos=tempo_total,
                        tempo_ciclo_segundos=0,
                    )
                    nova_view._mensagem_ao_vivo = self._mensagem_ao_vivo
                    await self._republicar_card_ao_vivo(nova_view)
                    return

                tempo_ciclo = await calcular_segundos_plantao_atual(
                    self.membro.id,
                    estado,
                )
                # Histórico só com segmentos fechados (não cresce no segundo)
                tempo_total = await calcular_segundos_historico_fechado(self.membro.id)
                nova_view = InformacoesPlantaoView(
                    self.membro,
                    estado,
                    tempo_total_segundos=tempo_total,
                    tempo_ciclo_segundos=tempo_ciclo,
                )
                # Transfere o controle do loop para a view nova
                nova_view._mensagem_ao_vivo = self._mensagem_ao_vivo
                nova_view._tarefa_ao_vivo = self._tarefa_ao_vivo
                conseguiu_editar = await self._republicar_card_ao_vivo(nova_view)
                if not conseguiu_editar:
                    return

                # Continua o loop na view antiga (mesma task); a mensagem já
                # mostra a view nova, mas o callback dos botões dela é que vale.
                # Para o próximo tick, lemos de novo do banco — não precisamos
                # trocar a task.
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception(
                "Loop ao vivo do plantao encerrou com erro para %s",
                self.membro.id,
            )

    async def on_timeout(self) -> None:
        self._parar_loop_ao_vivo()

    async def _ao_toggle(self, interaction: discord.Interaction):
        # Adia na hora — evita 10062 se o banco demorar.
        if not await _adiar_interacao_efemera(interaction):
            return

        estado_antes = await _buscar_estado(interaction.user.id)
        ja_ligado = estado_antes is not None and estado_antes.toggle_ligado

        if ja_ligado:
            self._parar_loop_ao_vivo()
            await desligar_servico(interaction.user)
            novo_estado = await _buscar_estado(interaction.user.id)
            tempo_total = await calcular_segundos_historico_fechado(interaction.user.id)
            nova_view = InformacoesPlantaoView(
                interaction.user,
                novo_estado,
                tempo_total_segundos=tempo_total,
                tempo_ciclo_segundos=0,
            )
            await editar_mensagem_original(
                interaction,
                view=nova_view,
            )
            return

        id_fivem = await resolver_id_fivem(interaction.user.id)

        if id_fivem is None:
            if membro_pode_informar_id_manualmente(interaction.user):
                await responder_view(
                    interaction,
                    _ViewPedirIdFivem(origem="info"),
                    ephemeral=True,
                )
                return
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    MENSAGEM_SEM_PERMISSAO,
                ],
            )
            return

        self._parar_loop_ao_vivo()
        await ligar_servico(interaction.user, id_fivem)
        novo_estado = await _buscar_estado(interaction.user.id)
        tempo_ciclo = await calcular_segundos_plantao_atual(
            interaction.user.id,
            novo_estado,
        )
        tempo_total = await calcular_segundos_historico_fechado(interaction.user.id)
        nova_view = InformacoesPlantaoView(
            interaction.user,
            novo_estado,
            tempo_total_segundos=tempo_total,
            tempo_ciclo_segundos=tempo_ciclo,
        )
        # Clique veio do próprio card efêmero: editar a mensagem original
        # atualiza só esse card, não o painel público.
        mensagem_do_card = await editar_mensagem_original(
            interaction,
            view=nova_view,
        )
        if mensagem_do_card is not None:
            nova_view.iniciar_atualizacao_ao_vivo(mensagem_do_card)

    async def _ao_carteira(self, interaction: discord.Interaction):
        from src.plantao.carteira_panel import abrir_carteira

        await abrir_carteira(interaction)

    async def _ao_selecionar_call(self, interaction: discord.Interaction):
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
        await responder_view(
            interaction,
            view_link,
            ephemeral=True,
        )


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
        """Executa a troca com uma barreira de erro para não deixar a interação sem resposta.

        Delega a regra financeira a um método separado e registra qualquer falha no
        canal de erros, informando o membro de modo seguro. Essa proteção é importante
        porque a operação pode debitar moedas e acionar o canal de finanças.
        """
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
                    "Ocorreu um erro inesperado. A equipe foi notificada no log de "
                    "erros.",
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
                    "Pedido publicado no **canal de finanças** (com botão de "
                    "confirmação).",
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
            tempo_ciclo = await calcular_segundos_plantao_atual(
                interaction.user.id,
                novo_estado,
            )
            tempo_total = await calcular_segundos_historico_fechado(interaction.user.id)
            nova_view = InformacoesPlantaoView(
                interaction.user,
                novo_estado,
                tempo_total_segundos=tempo_total,
                tempo_ciclo_segundos=tempo_ciclo,
            )
            mensagem_do_card = await editar_mensagem_original(
                interaction,
                view=nova_view,
            )
            if (
                novo_estado is not None
                and novo_estado.toggle_ligado
                and mensagem_do_card is not None
            ):
                nova_view.iniciar_atualizacao_ao_vivo(mensagem_do_card)
        except discord.HTTPException as erro_em_executar_troca:
            # Enfeite que falhou: atualizar o card da troca de moedas.
            # A acao principal ja tinha dado certo, entao so registro.
            ignorar_falha_cosmetica(
                erro_em_executar_troca,
                o_que_falhou="atualizar o card da troca de moedas",
            )
