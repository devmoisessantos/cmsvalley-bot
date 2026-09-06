"""
Painéis de ingresso e gestão de membros GATE (Components V2).

- PainelIngressarGateLayout: Paramédico solicita entrada.
- Card de aprovação no canal APROVAR_GATE_REPROVAR.
- PainelGerenciarGateLayout: gestores buscam membro e agem.
"""

from __future__ import annotations

import logging

import discord

from src.config import (
    CANAIS,
    CURSOS_OBRIGATORIOS_INGRESSO_GATE,
)
from src.cursos.cursos_service import rotulo_curso
from src.gate.membros.membros_gate_logger import (
    log_expulsao_gate,
    log_ingresso_aprovado,
    log_ingresso_reprovado,
    log_promocao_gate,
    log_rebaixamento_gate,
)
from src.gate.membros.membros_gate_service import (
    aprovar_ingresso,
    buscar_solicitacao_pendente,
    buscar_solicitacao_por_id,
    cargo_gate_atual,
    criar_solicitacao_ingresso,
    e_gestor_gate,
    expulsar_membro_gate,
    marcar_mensagem_solicitacao,
    promover_membro_gate,
    rebaixar_membro_gate,
    reprovar_ingresso,
    validar_requisitos_ingresso,
)
from src.punicoes.punicoes_helpers import lista_cargos_punicao_ordenada
from src.punicoes.punicoes_service import aplicar_punicao
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
    responder_view,
)

registrador = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Painel: solicitar ingresso
# ---------------------------------------------------------------------------


class PainelIngressarGateLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel fixo — solicitar ingresso na GATE."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)

        url_icone = None
        if guild is not None and guild.icon is not None:
            url_icone = guild.icon.url

        lista_cursos = "\n".join(
            f"- {rotulo_curso(chave)}"
            for chave in CURSOS_OBRIGATORIOS_INGRESSO_GATE
        )

        componentes: list = []
        texto_cabecalho = (
            "# 🌱 Ingressar na GATE\n"
            "> 🛡️ Unidade tática – CMS Valley\n"
            "Solicite sua entrada na GATE após cumprir todos os requisitos."
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

        componentes.append(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
        )
        componentes.append(
            discord.ui.TextDisplay(
                "## 📋 Requisitos\n"
                "- Ser **Paramédico** (ou superior) ativo no CMS\n"
                "- Ter concluído os **Cursos Práticos 1.0** + Resgate:\n"
                f"{lista_cursos}\n"
                "- Boa conduta, respeito à hierarquia e disponibilidade\n"
                "- Sem histórico recente de punições graves\n\n"
                "> ⚠️ O cumprimento dos requisitos **não garante** aprovação "
                "imediata."
            )
        )
        componentes.append(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
        )
        componentes.append(
            discord.ui.TextDisplay(
                "### 🛡️ Formação — Guardião\n"
                "Ao ser aprovado, você inicia como **Guardião** (período de "
                "formação). Ao final poderá ser promovido a **Operador**, "
                "permanecer em avaliação ou ser desligado.\n\n"
                "-# *Disciplina, Comprometimento e Excelência Operacional.*"
            )
        )
        componentes.append(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
        )

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Solicitar Ingresso",
            style=discord.ButtonStyle.success,
            emoji="🌱",
            custom_id="gate:ingresso:solicitar",
        )
        botao.callback = self._ao_solicitar
        linha.add_item(botao)
        componentes.append(linha)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.green(),
            )
        )

    async def _ao_solicitar(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use este painel dentro do servidor."],
            )
            return

        ok, pendencias = validar_requisitos_ingresso(membro)
        if not ok:
            await responder_aviso(
                interacao,
                titulo="Requisitos incompletos",
                linhas=pendencias,
            )
            return

        pendente = await buscar_solicitacao_pendente(membro.id)
        if pendente is not None:
            await responder_aviso(
                interacao,
                titulo="Solicitação em análise",
                linhas=[
                    "Você já possui uma solicitação **pendente**.",
                    "Aguarde a decisão do Comando GATE.",
                ],
            )
            return

        await interacao.response.defer(ephemeral=True)
        registro = await criar_solicitacao_ingresso(membro)
        guild = interacao.guild
        if guild is None:
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Servidor não encontrado."],
            )
            return

        canal_aprovacao = guild.get_channel(
            CANAIS.get("APROVAR_GATE_REPROVAR") or 0
        )
        if canal_aprovacao is None:
            await responder_erro(
                interacao,
                titulo="Canal ausente",
                linhas=[
                    "Canal de aprovação GATE não configurado.",
                ],
            )
            return

        card = CardAprovacaoIngressoGate(
            solicitacao_id=registro.id,
            candidato=membro,
        )
        mensagem = await canal_aprovacao.send(view=card)
        await marcar_mensagem_solicitacao(
            registro.id, canal_aprovacao.id, mensagem.id
        )

        await responder_sucesso(
            interacao,
            titulo="Solicitação enviada",
            linhas=[
                "Seu pedido foi encaminhado ao Comando GATE.",
                "Aguarde a análise (aprovação ou reprovação).",
            ],
        )


class CardAprovacaoIngressoGate(LoggingViewMixin, discord.ui.LayoutView):
    """Card no canal de aprovação com botões persistentes."""

    def __init__(
        self,
        solicitacao_id: int,
        candidato: discord.Member,
    ):
        super().__init__(timeout=None)
        self.solicitacao_id = solicitacao_id

        linha = discord.ui.ActionRow()
        botao_aprovar = discord.ui.Button(
            label="Aprovar",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"gate:ingresso:aprovar:{solicitacao_id}",
        )
        botao_aprovar.callback = self._ao_aprovar
        linha.add_item(botao_aprovar)

        botao_reprovar = discord.ui.Button(
            label="Reprovar",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"gate:ingresso:reprovar:{solicitacao_id}",
        )
        botao_reprovar.callback = self._ao_reprovar
        linha.add_item(botao_reprovar)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# 🌱 Solicitação de Ingresso GATE"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    f"**Candidato:** {candidato.mention}\n"
                    f"**ID:** `{candidato.id}`\n"
                    f"**Nick:** {candidato.display_name}\n\n"
                    "Comandante / Subcomandante: aprove ou reprove."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_aprovar(self, interacao: discord.Interaction):
        await processar_aprovacao_ingresso(interacao, self.solicitacao_id)

    async def _ao_reprovar(self, interacao: discord.Interaction):
        await processar_reprovacao_ingresso(interacao, self.solicitacao_id)


async def processar_aprovacao_ingresso(
    interacao: discord.Interaction,
    solicitacao_id: int,
) -> None:
    if not isinstance(interacao.user, discord.Member):
        await responder_erro(
            interacao,
            titulo="Contexto inválido",
            linhas=["Use dentro do servidor."],
        )
        return
    if not e_gestor_gate(interacao.user):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=[
                "Apenas **Comandante** ou **Subcomandante** podem decidir.",
            ],
        )
        return

    await interacao.response.defer(ephemeral=True)
    solicitacao = await buscar_solicitacao_por_id(solicitacao_id)
    if solicitacao is None:
        await responder_erro(
            interacao,
            titulo="Não encontrada",
            linhas=["Solicitação não existe."],
        )
        return

    ok, mensagem = await aprovar_ingresso(
        interacao.guild, solicitacao, interacao.user
    )
    if not ok:
        await responder_aviso(interacao, titulo="Não foi possível", linhas=[mensagem])
        return

    candidato = interacao.guild.get_member(solicitacao.discord_id_candidato)
    if candidato is not None:
        await log_ingresso_aprovado(
            interacao.guild, candidato, interacao.user
        )

    await responder_sucesso(
        interacao, titulo="Aprovado", linhas=[mensagem]
    )
    try:
        await interacao.message.edit(view=_card_decidido("aprovado"))
    except Exception:
        pass


async def processar_reprovacao_ingresso(
    interacao: discord.Interaction,
    solicitacao_id: int,
) -> None:
    if not isinstance(interacao.user, discord.Member):
        await responder_erro(
            interacao,
            titulo="Contexto inválido",
            linhas=["Use dentro do servidor."],
        )
        return
    if not e_gestor_gate(interacao.user):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=[
                "Apenas **Comandante** ou **Subcomandante** podem decidir.",
            ],
        )
        return

    modal = ModalMotivoReprovacaoGate(solicitacao_id)
    await interacao.response.send_modal(modal)


class ModalMotivoReprovacaoGate(
    LoggingModalMixin, discord.ui.Modal, title="Reprovar ingresso GATE"
):
    motivo = discord.ui.TextInput(
        label="Motivo da reprovação",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    def __init__(self, solicitacao_id: int):
        super().__init__()
        self.solicitacao_id = solicitacao_id

    async def on_submit(self, interacao: discord.Interaction):
        await interacao.response.defer(ephemeral=True)
        solicitacao = await buscar_solicitacao_por_id(self.solicitacao_id)
        if solicitacao is None:
            await responder_erro(
                interacao,
                titulo="Não encontrada",
                linhas=["Solicitação não existe."],
            )
            return

        ok, mensagem = await reprovar_ingresso(
            solicitacao, interacao.user, self.motivo.value.strip()
        )
        if not ok:
            await responder_aviso(
                interacao, titulo="Não foi possível", linhas=[mensagem]
            )
            return

        candidato = interacao.guild.get_member(
            solicitacao.discord_id_candidato
        )
        if candidato is not None:
            await log_ingresso_reprovado(
                interacao.guild,
                candidato,
                interacao.user,
                self.motivo.value.strip(),
            )

        await responder_sucesso(
            interacao, titulo="Reprovado", linhas=[mensagem]
        )
        try:
            if interacao.message:
                await interacao.message.edit(view=_card_decidido("reprovado"))
        except Exception:
            pass


def _card_decidido(status: str) -> discord.ui.LayoutView:
    titulo = (
        "# ✅ Solicitação aprovada"
        if status == "aprovado"
        else "# ❌ Solicitação reprovada"
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(titulo),
            discord.ui.TextDisplay("-# Decisão registrada."),
            accent_color=(
                discord.Color.green()
                if status == "aprovado"
                else discord.Color.red()
            ),
        )
    )
    return view


# ---------------------------------------------------------------------------
# Painel: gerenciar membros GATE
# ---------------------------------------------------------------------------


class PainelGerenciarGateLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel fixo — gestão de membros GATE (só comando)."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)

        url_icone = None
        if guild is not None and guild.icon is not None:
            url_icone = guild.icon.url

        componentes: list = []
        texto_cabecalho = (
            "# 🛡️ Gerenciar Membros GATE\n"
            "> 🔒 Comando tático – Comandante / Subcomandante\n"
            "Promova, rebaixe, expulse ou aplique advertência a membros GATE."
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

        componentes.append(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
        )
        componentes.append(
            discord.ui.TextDisplay(
                "### Ações disponíveis\n"
                "- ⬆️ **Promover** — sobe um degrau na hierarquia GATE\n"
                "- ⬇️ **Rebaixar** — desce um degrau\n"
                "- 🚪 **Expulsar** — remove cargos GATE (mantém hospitalar)\n"
                "- ⚠️ **Advertência** — aplica punição sem sair deste painel\n\n"
                "-# Busque o membro por menção, Discord ID ou ID FiveM."
            )
        )
        componentes.append(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
        )

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Gerenciar Membro GATE",
            style=discord.ButtonStyle.primary,
            emoji="🛡️",
            custom_id="gate:membros:abrir",
        )
        botao.callback = self._ao_abrir
        linha.add_item(botao)
        componentes.append(linha)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.dark_gold(),
            )
        )

    async def _ao_abrir(self, interacao: discord.Interaction):
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use dentro do servidor."],
            )
            return
        if not e_gestor_gate(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Apenas **Comandante** ou **Subcomandante** podem usar.",
                ],
            )
            return
        await responder_view(
            interacao,
            ViewBuscarMembroGate(),
            ephemeral=True,
        )


class ViewBuscarMembroGate(LoggingViewMixin, discord.ui.LayoutView):
    """Select de usuário + atalho por Discord ID."""

    def __init__(self):
        super().__init__(timeout=180)

        linha_select = discord.ui.ActionRow()
        select = discord.ui.UserSelect(
            placeholder="Selecione o membro GATE…",
            min_values=1,
            max_values=1,
        )
        select.callback = self._ao_selecionar
        linha_select.add_item(select)

        linha_botao = discord.ui.ActionRow()
        botao_id = discord.ui.Button(
            label="Buscar por Discord ID",
            style=discord.ButtonStyle.secondary,
            emoji="🔍",
        )
        botao_id.callback = self._ao_pedir_id
        linha_botao.add_item(botao_id)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("## 🔍 Buscar membro GATE"),
                discord.ui.TextDisplay(
                    "Escolha o membro no select ou informe o Discord ID."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha_select,
                linha_botao,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_selecionar(self, interacao: discord.Interaction):
        valores = interacao.data.get("values") or []
        if not valores:
            await responder_erro(
                interacao,
                titulo="Seleção vazia",
                linhas=["Nenhum membro selecionado."],
            )
            return
        membro = interacao.guild.get_member(int(valores[0]))
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Não encontrado",
                linhas=["Membro não está no servidor."],
            )
            return
        await responder_view(
            interacao,
            ViewAcoesMembroGate(membro),
            ephemeral=True,
        )

    async def _ao_pedir_id(self, interacao: discord.Interaction):
        await interacao.response.send_modal(ModalDiscordIdGate())


class ModalDiscordIdGate(
    LoggingModalMixin, discord.ui.Modal, title="Discord ID do membro"
):
    discord_id = discord.ui.TextInput(
        label="Discord ID",
        placeholder="Ex: 123456789012345678",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interacao: discord.Interaction):
        texto = self.discord_id.value.strip()
        if not texto.isdigit():
            await responder_erro(
                interacao,
                titulo="ID inválido",
                linhas=["Informe apenas números."],
            )
            return
        membro = interacao.guild.get_member(int(texto))
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Não encontrado",
                linhas=["Membro não está no servidor."],
            )
            return
        await responder_view(
            interacao,
            ViewAcoesMembroGate(membro),
            ephemeral=True,
        )


class ViewAcoesMembroGate(LoggingViewMixin, discord.ui.LayoutView):
    """Ações sobre um membro GATE escolhido."""

    def __init__(self, alvo: discord.Member):
        super().__init__(timeout=180)
        self.alvo = alvo
        cargo_atual = cargo_gate_atual(alvo) or "— (sem cargo GATE)"

        linha = discord.ui.ActionRow()
        for rotulo, estilo, emoji, callback in (
            ("Promover", discord.ButtonStyle.success, "⬆️", self._ao_promover),
            ("Rebaixar", discord.ButtonStyle.secondary, "⬇️", self._ao_rebaixar),
            ("Expulsar", discord.ButtonStyle.danger, "🚪", self._ao_expulsar),
            (
                "Advertência",
                discord.ButtonStyle.primary,
                "⚠️",
                self._ao_advertencia,
            ),
        ):
            botao = discord.ui.Button(label=rotulo, style=estilo, emoji=emoji)
            botao.callback = callback
            linha.add_item(botao)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"## 🛡️ {alvo.display_name}\n"
                    f"{alvo.mention} · `{alvo.id}`\n"
                    f"**Cargo GATE:** {cargo_atual}"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha,
                accent_color=discord.Color.dark_gold(),
            )
        )

    async def _ao_promover(self, interacao: discord.Interaction):
        if not e_gestor_gate(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas o Comando GATE."],
            )
            return
        await interacao.response.defer(ephemeral=True)
        ok, detalhe = await promover_membro_gate(
            interacao.guild, self.alvo, interacao.user
        )
        if not ok:
            await responder_aviso(
                interacao, titulo="Não foi possível", linhas=[detalhe]
            )
            return
        await log_promocao_gate(
            interacao.guild, self.alvo, interacao.user, detalhe
        )
        await responder_sucesso(
            interacao, titulo="Promovido", linhas=[detalhe]
        )

    async def _ao_rebaixar(self, interacao: discord.Interaction):
        if not e_gestor_gate(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas o Comando GATE."],
            )
            return
        await interacao.response.defer(ephemeral=True)
        ok, detalhe = await rebaixar_membro_gate(
            interacao.guild, self.alvo, interacao.user
        )
        if not ok:
            await responder_aviso(
                interacao, titulo="Não foi possível", linhas=[detalhe]
            )
            return
        await log_rebaixamento_gate(
            interacao.guild, self.alvo, interacao.user, detalhe
        )
        await responder_sucesso(
            interacao, titulo="Rebaixado", linhas=[detalhe]
        )

    async def _ao_expulsar(self, interacao: discord.Interaction):
        if not e_gestor_gate(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas o Comando GATE."],
            )
            return
        await interacao.response.defer(ephemeral=True)
        ok, detalhe = await expulsar_membro_gate(
            interacao.guild, self.alvo, interacao.user
        )
        if not ok:
            await responder_aviso(
                interacao, titulo="Não foi possível", linhas=[detalhe]
            )
            return
        await log_expulsao_gate(
            interacao.guild, self.alvo, interacao.user, detalhe
        )
        await responder_sucesso(
            interacao, titulo="Expulso da GATE", linhas=[detalhe]
        )

    async def _ao_advertencia(self, interacao: discord.Interaction):
        if not e_gestor_gate(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas o Comando GATE."],
            )
            return
        await responder_view(
            interacao,
            ViewAdvertenciaRapidaGate(self.alvo),
            ephemeral=True,
        )


class ViewAdvertenciaRapidaGate(LoggingViewMixin, discord.ui.LayoutView):
    """Escolhe o tipo de advertência e abre o modal de motivo."""

    def __init__(self, alvo: discord.Member):
        super().__init__(timeout=120)
        self.alvo = alvo

        opcoes = []
        for nome, cargo_id in lista_cargos_punicao_ordenada():
            opcoes.append(
                discord.SelectOption(
                    label=nome[:100],
                    value=f"{cargo_id}|{nome}",
                    description=str(cargo_id)[:100],
                )
            )

        linha = discord.ui.ActionRow()
        select = discord.ui.Select(
            placeholder="Tipo de advertência…",
            options=opcoes[:25],
        )
        select.callback = self._ao_escolher
        linha.add_item(select)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"## ⚠️ Advertência — {alvo.display_name}"
                ),
                discord.ui.TextDisplay(
                    "Escolha o tipo. Em seguida informe o motivo."
                ),
                linha,
                accent_color=discord.Color.orange(),
            )
        )

    async def _ao_escolher(self, interacao: discord.Interaction):
        valor = (interacao.data.get("values") or [""])[0]
        partes = valor.split("|", 1)
        if len(partes) != 2:
            await responder_erro(
                interacao,
                titulo="Opção inválida",
                linhas=["Tente novamente."],
            )
            return
        cargo_id = int(partes[0])
        cargo_nome = partes[1]
        modal = ModalMotivoAdvertenciaGate(
            self.alvo, cargo_id, cargo_nome
        )
        await interacao.response.send_modal(modal)


class ModalMotivoAdvertenciaGate(
    LoggingModalMixin, discord.ui.Modal, title="Motivo da advertência"
):
    motivo = discord.ui.TextInput(
        label="Motivo",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1500,
    )
    id_fivem = discord.ui.TextInput(
        label="ID FiveM (opcional)",
        required=False,
        max_length=20,
    )

    def __init__(
        self,
        alvo: discord.Member,
        cargo_id: int,
        cargo_nome: str,
    ):
        super().__init__()
        self.alvo = alvo
        self.cargo_id = cargo_id
        self.cargo_nome = cargo_nome

    async def on_submit(self, interacao: discord.Interaction):
        await interacao.response.defer(ephemeral=True)
        ok, mensagem, _punicao = await aplicar_punicao(
            guild=interacao.guild,
            alvo=self.alvo,
            executor=interacao.user,
            id_fivem=(self.id_fivem.value or "").strip(),
            cargo_nome=self.cargo_nome,
            cargo_id=self.cargo_id,
            motivo=self.motivo.value.strip(),
            links_texto=None,
        )
        if not ok:
            await responder_aviso(
                interacao, titulo="Falha na punição", linhas=[mensagem]
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Advertência aplicada",
            linhas=[mensagem or "Punição registrada."],
        )
