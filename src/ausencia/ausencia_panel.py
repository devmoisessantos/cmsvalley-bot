# src/ausencia/ausencia_panel.py
"""Painel fixo + fluxos ephemeral e de decisão da ausência."""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord

from src.ausencia.ausencia_service import (
    PERIODOS_AUSENCIA,
    TIPOS_AUSENCIA,
    aplicar_cargos_ausencia,
    calcular_datas_periodo,
    cargo_atual_hierarquia,
    criar_solicitacao,
    decidir_ausencia,
    marcar_mensagem_pedido,
    membro_e_diretoria,
    membro_pode_solicitar_ausencia,
    obter_pedido_pendente,
)
from src.config import CANAIS
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
    enviar_erro_para_log_erros,
)
from src.utils.formatacao import (
    agora_brasilia,
    para_horario_brasilia,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)
from src.utils.notificacao import (
    COR_AVISO,
    enviar_dm_card,
)

CUSTOM_ID_SOLICITAR = "ausencia:solicitar"
CUSTOM_ID_APROVAR = "ausencia:aprovar:"
CUSTOM_ID_REPROVAR = "ausencia:reprovar:"


def _formatar_momento_brasilia(data: datetime | None = None) -> str:
    local = para_horario_brasilia(data) if data else agora_brasilia()
    if local is None:
        local = agora_brasilia()
    meses = (
        "Jan",
        "Fev",
        "Mar",
        "Abr",
        "Mai",
        "Jun",
        "Jul",
        "Ago",
        "Set",
        "Out",
        "Nov",
        "Dez",
    )
    return (
        f"{local.day} {meses[local.month - 1]} {local.year} — {local.strftime('%H:%M')}"
    )


def _formatar_data_curta(data: datetime) -> str:
    local = para_horario_brasilia(data) or data
    return local.strftime("%d/%m/%Y")


# ---------------------------------------------------------------------------
# Painel persistente
# ---------------------------------------------------------------------------


class PainelAusenciaLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente no canal CANAL_REGISTRAR_AUSENCIA."""

    def __init__(self, guilda: discord.Guild | None = None):
        super().__init__(timeout=None)
        self.guild_ref = guilda

        url_icone = None
        if guilda is not None and guilda.icon is not None:
            url_icone = guilda.icon.url

        componentes: list = []
        titulo = (
            "# :beach: CMS Valley — Registro de Ausências\n"
            "> Use a opção abaixo para solicitar seu afastamento do servidor.\n"
            "Cada solicitação é registrada em nosso sistema para aprovação da Diretoria.\n"
            "Caso tenha dúvidas, entre em contato com os Gerais!"
        )
        if url_icone:
            componentes.append(
                discord.ui.Section(
                    titulo,
                    accessory=discord.ui.Thumbnail(url_icone),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(titulo))

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(
            discord.ui.TextDisplay(
                "## :gear: Regras Gerais:\n"
                ":white_check_mark: Solicitações devem ser feitas com antecedência mínima de 12h.\n"
                ":white_check_mark: Ausências superiores a **30 dias** exigem aprovação da Diretoria.\n"
                ":white_check_mark: Para emergências, contate um Superior no privado.\n\n"
                "### **:pushpin: Como Funciona:**\n"
                "→ Após escolher o tipo e o período, o pedido será enviado para o canal da Diretoria.\n"
                "→ Você receberá uma notificação quando for aprovado ou negado."
            )
        )
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="📩 Iniciar Registro",
            style=discord.ButtonStyle.success,
            custom_id=CUSTOM_ID_SOLICITAR,
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

        if not membro_pode_solicitar_ausencia(membro):
            await responder_aviso(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Só membros da **hierarquia** / HP S・Valley podem solicitar ausência.",
                ],
            )
            return

        pendente = await obter_pedido_pendente(membro.id)
        if pendente is not None:
            await responder_aviso(
                interacao,
                titulo="Pedido já em análise",
                linhas=[
                    f"Você já tem a solicitação `#{pendente.id}` **pendente**.",
                    "Aguarde a decisão da diretoria antes de abrir outra.",
                ],
                delay=15,
            )
            return

        view = ViewSelecaoAusencia(membro_id=membro.id)
        await interacao.response.send_message(view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# Seleção ephemeral (tipo + período)
# ---------------------------------------------------------------------------


class ViewSelecaoAusencia(LoggingViewMixin, discord.ui.LayoutView):
    """Painel ephemeral: tipo (exclusivo) + período + enviar/cancelar."""

    def __init__(self, *, membro_id: int):
        super().__init__(timeout=300)
        self.membro_id = membro_id
        self.tipo_selecionado: str | None = None
        self.periodo_selecionado: str | None = None
        self._reconstruir()

    def _reconstruir(self) -> None:
        self.clear_items()

        texto_tipo = (
            TIPOS_AUSENCIA.get(self.tipo_selecionado, "—")
            if self.tipo_selecionado
            else "*(nenhum)*"
        )
        texto_periodo = (
            PERIODOS_AUSENCIA[self.periodo_selecionado][0]
            if self.periodo_selecionado
            else "*(nenhum)*"
        )

        componentes: list = [
            discord.ui.TextDisplay(
                "# 📩 Solicitar Ausência\n"
                "Escolha o **tipo** e o **período**. Depois confirme."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"**Tipo:** {texto_tipo}\n**Período:** {texto_periodo}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay("### Tipo de ausência"),
        ]

        linha_tipos = discord.ui.ActionRow()
        for chave, rotulo in TIPOS_AUSENCIA.items():
            estilo = (
                discord.ButtonStyle.primary
                if self.tipo_selecionado == chave
                else discord.ButtonStyle.secondary
            )
            btn = discord.ui.Button(
                label=rotulo,
                style=estilo,
                disabled=self.tipo_selecionado is not None
                and self.tipo_selecionado != chave,
                custom_id=f"ausencia:tipo:{chave}",
            )
            btn.callback = self._fazer_callback_tipo(chave)
            linha_tipos.add_item(btn)
        componentes.append(linha_tipos)

        # Botão alterar tipo (reabilita)
        if self.tipo_selecionado:
            linha_alt = discord.ui.ActionRow()
            btn_alt = discord.ui.Button(
                label="🔄 Alterar tipo",
                style=discord.ButtonStyle.secondary,
            )
            btn_alt.callback = self._ao_alterar_tipo
            linha_alt.add_item(btn_alt)
            componentes.append(linha_alt)

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(discord.ui.TextDisplay("### Período"))

        linha_periodos = discord.ui.ActionRow()
        for chave, (rotulo, _) in PERIODOS_AUSENCIA.items():
            estilo = (
                discord.ButtonStyle.primary
                if self.periodo_selecionado == chave
                else discord.ButtonStyle.secondary
            )
            btn = discord.ui.Button(
                label=rotulo,
                style=estilo,
                disabled=self.periodo_selecionado is not None
                and self.periodo_selecionado != chave,
                custom_id=f"ausencia:periodo:{chave}",
            )
            btn.callback = self._fazer_callback_periodo(chave)
            linha_periodos.add_item(btn)
        componentes.append(linha_periodos)

        if self.periodo_selecionado:
            linha_alt_p = discord.ui.ActionRow()
            btn_alt_p = discord.ui.Button(
                label="🔄 Alterar período",
                style=discord.ButtonStyle.secondary,
            )
            btn_alt_p.callback = self._ao_alterar_periodo
            linha_alt_p.add_item(btn_alt_p)
            componentes.append(linha_alt_p)

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        linha_acoes = discord.ui.ActionRow()
        btn_enviar = discord.ui.Button(
            label="📩 Solicitar Ausência",
            style=discord.ButtonStyle.success,
            disabled=not (self.tipo_selecionado and self.periodo_selecionado),
        )
        btn_enviar.callback = self._ao_enviar
        btn_cancelar = discord.ui.Button(
            label="❌ Cancelar",
            style=discord.ButtonStyle.danger,
        )
        btn_cancelar.callback = self._ao_cancelar
        linha_acoes.add_item(btn_enviar)
        linha_acoes.add_item(btn_cancelar)
        componentes.append(linha_acoes)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.green(),
            )
        )

    def _fazer_callback_tipo(self, chave: str):
        async def _cb(interacao: discord.Interaction):
            if interacao.user.id != self.membro_id:
                await responder_erro(
                    interacao,
                    titulo="Sem permissão",
                    linhas=["Só quem abriu o pedido pode selecionar."],
                )
                return
            self.tipo_selecionado = chave
            self._reconstruir()
            await interacao.response.edit_message(view=self)

        return _cb

    def _fazer_callback_periodo(self, chave: str):
        async def _cb(interacao: discord.Interaction):
            if interacao.user.id != self.membro_id:
                await responder_erro(
                    interacao,
                    titulo="Sem permissão",
                    linhas=["Só quem abriu o pedido pode selecionar."],
                )
                return
            self.periodo_selecionado = chave
            self._reconstruir()
            await interacao.response.edit_message(view=self)

        return _cb

    async def _ao_alterar_tipo(self, interacao: discord.Interaction):
        if interacao.user.id != self.membro_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu o pedido pode alterar."],
            )
            return
        self.tipo_selecionado = None
        self._reconstruir()
        await interacao.response.edit_message(view=self)

    async def _ao_alterar_periodo(self, interacao: discord.Interaction):
        if interacao.user.id != self.membro_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu o pedido pode alterar."],
            )
            return
        self.periodo_selecionado = None
        self._reconstruir()
        await interacao.response.edit_message(view=self)

    async def _ao_cancelar(self, interacao: discord.Interaction):
        if interacao.user.id != self.membro_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu o pedido pode cancelar."],
            )
            return
        await interacao.response.edit_message(
            view=discord.ui.LayoutView().add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay(
                        "# ❌ Cancelado\nSolicitação de ausência cancelada."
                    ),
                    accent_color=discord.Color.red(),
                )
            )
        )

    async def _ao_enviar(self, interacao: discord.Interaction):
        if interacao.user.id != self.membro_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu o pedido pode enviar."],
            )
            return
        if not self.tipo_selecionado or not self.periodo_selecionado:
            await responder_aviso(
                interacao,
                titulo="Seleção incompleta",
                linhas=["Escolha o **tipo** e o **período** antes de enviar."],
            )
            return

        # Modal para motivo (+ datas se 30+)
        await interacao.response.send_modal(
            ModalMotivoAusencia(
                tipo=self.tipo_selecionado,
                periodo_chave=self.periodo_selecionado,
            )
        )


# ---------------------------------------------------------------------------
# Modal motivo (+ datas opcionais)
# ---------------------------------------------------------------------------


class ModalMotivoAusencia(
    LoggingModalMixin, discord.ui.Modal, title="📩 Detalhes da ausência"
):
    def __init__(self, *, tipo: str, periodo_chave: str):
        super().__init__()
        self.tipo = tipo
        self.periodo_chave = periodo_chave

        self.campo_motivo = discord.ui.TextInput(
            label="Motivo / observação",
            style=discord.TextStyle.paragraph,
            placeholder="Descreva brevemente o motivo do afastamento…",
            required=True,
            min_length=5,
            max_length=1000,
        )
        self.add_item(self.campo_motivo)

        # Para 30+ ou sempre permitir ajuste de datas
        self.campo_inicio = discord.ui.TextInput(
            label="Data início (DD/MM/AAAA)",
            style=discord.TextStyle.short,
            placeholder="Ex: 20/08/2026 — deixe vazio = hoje",
            required=False,
            max_length=10,
        )
        self.add_item(self.campo_inicio)

        if periodo_chave == "30plus":
            self.campo_fim = discord.ui.TextInput(
                label="Data fim (DD/MM/AAAA) — obrigatória para 30+",
                style=discord.TextStyle.short,
                placeholder="Ex: 25/09/2026",
                required=True,
                max_length=10,
            )
            self.add_item(self.campo_fim)
        else:
            self.campo_fim = None

    def _parse_data(self, texto: str) -> datetime | None:
        texto = (texto or "").strip()
        if not texto:
            return None
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                dt = datetime.strptime(texto, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    async def on_submit(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use dentro do servidor."],
            )
            return

        motivo = (self.campo_motivo.value or "").strip()
        if len(motivo) < 5:
            await responder_erro(
                interacao,
                titulo="Motivo curto demais",
                linhas=["Escreva pelo menos algumas palavras."],
            )
            return

        inicio_custom = self._parse_data(self.campo_inicio.value or "")
        if self.campo_inicio.value and inicio_custom is None:
            await responder_erro(
                interacao,
                titulo="Data inválida",
                linhas=["Use o formato **DD/MM/AAAA** na data de início."],
            )
            return

        if self.periodo_chave == "30plus":
            fim_custom = (
                self._parse_data(self.campo_fim.value or "") if self.campo_fim else None
            )
            if fim_custom is None:
                await responder_erro(
                    interacao,
                    titulo="Data fim obrigatória",
                    linhas=["Para **30+ dias** informe a data de fim (DD/MM/AAAA)."],
                )
                return
            inicio = inicio_custom or datetime.now(timezone.utc)
            if fim_custom <= inicio:
                await responder_erro(
                    interacao,
                    titulo="Período inválido",
                    linhas=["A data de fim deve ser **depois** da data de início."],
                )
                return
            data_inicio, data_fim = inicio, fim_custom
        else:
            data_inicio, data_fim = calcular_datas_periodo(
                self.periodo_chave, data_inicio=inicio_custom
            )

        cargo = cargo_atual_hierarquia(membro)
        tipo_rotulo = TIPOS_AUSENCIA.get(self.tipo, self.tipo)
        periodo_rotulo = PERIODOS_AUSENCIA.get(
            self.periodo_chave, (self.periodo_chave,)
        )[0]
        momento = _formatar_momento_brasilia()

        corpo = (
            f"`👤` **Membro:** {membro.mention}\n"
            f"- **Cargo:** `{cargo}`\n"
            f"- **Tipo:** {tipo_rotulo}\n"
            f"- **Período:** `{periodo_rotulo}`\n"
            f"- **Início:** `{_formatar_data_curta(data_inicio)}`\n"
            f"- **Fim:** `{_formatar_data_curta(data_fim)}`\n"
            f"- **Motivo:** {motivo}\n"
            f"`🕐` **Aberto em:** `{momento}`\n"
            f"`📌` **Status:** 🟡 **Pendente** — confirme o envio."
        )

        view = ViewConfirmarEnvioAusencia(
            membro_id=membro.id,
            tipo=self.tipo,
            periodo_chave=self.periodo_chave,
            data_inicio=data_inicio,
            data_fim=data_fim,
            motivo=motivo,
            cargo=cargo,
            corpo=corpo,
            url_thumb=membro.display_avatar.url,
        )
        await interacao.response.send_message(view=view, ephemeral=True)


class ViewConfirmarEnvioAusencia(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        *,
        membro_id: int,
        tipo: str,
        periodo_chave: str,
        data_inicio: datetime,
        data_fim: datetime,
        motivo: str,
        cargo: str,
        corpo: str,
        url_thumb: str,
    ):
        super().__init__(timeout=300)
        self.membro_id = membro_id
        self.tipo = tipo
        self.periodo_chave = periodo_chave
        self.data_inicio = data_inicio
        self.data_fim = data_fim
        self.motivo = motivo
        self.cargo = cargo

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Confirmar e enviar",
            style=discord.ButtonStyle.success,
            emoji="✅",
        )
        botao.callback = self._ao_confirmar
        linha.add_item(botao)

        self.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    "# 🔍 Confirmar solicitação",
                    corpo,
                    accessory=discord.ui.Thumbnail(url_thumb),
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                discord.ui.TextDisplay(
                    "-# Ao confirmar, o pedido vai para a **Diretoria** analisar."
                ),
                accent_color=discord.Color.orange(),
            )
        )

    async def _ao_confirmar(self, interacao: discord.Interaction):
        if interacao.user.id != self.membro_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu o pedido pode confirmar."],
            )
            return
        if not isinstance(interacao.user, discord.Member) or interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use dentro do servidor."],
            )
            return

        pendente = await obter_pedido_pendente(interacao.user.id)
        if pendente is not None:
            await responder_aviso(
                interacao,
                titulo="Pedido já em análise",
                linhas=[f"Já existe pedido `#{pendente.id}` pendente."],
            )
            return

        try:
            registro = await criar_solicitacao(
                membro=interacao.user,
                tipo=self.tipo,
                periodo_chave=self.periodo_chave,
                data_inicio=self.data_inicio,
                data_fim=self.data_fim,
                motivo=self.motivo,
            )
            await publicar_pedido_diretoria(
                interacao.guild,
                registro=registro,
                membro=interacao.user,
            )
            await responder_sucesso(
                interacao,
                titulo="Pedido enviado",
                linhas=[
                    f"Solicitação `#{registro.id}` enviada à Diretoria.",
                    "Você será notificado quando houver decisão.",
                ],
                delay=20,
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Falha ao criar solicitação de ausência",
                erro,
                contexto="ViewConfirmarEnvioAusencia._ao_confirmar",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro ao enviar",
                linhas=["Não foi possível registrar o pedido. Tente novamente."],
            )


# ---------------------------------------------------------------------------
# Publicação no canal da Diretoria + decisão
# ---------------------------------------------------------------------------


async def publicar_pedido_diretoria(
    guilda: discord.Guild,
    *,
    registro,
    membro: discord.Member,
) -> None:
    canal_id = CANAIS.get("CANAL_PEDIDOS_AUSENCIA") or 0
    canal = guilda.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        return

    tipo_rotulo = TIPOS_AUSENCIA.get(registro.tipo, registro.tipo)
    corpo = (
        f"`👤` **Membro:** {membro.mention} (`{membro.id}`)\n"
        f"- **FID:** `{registro.id_fivem or '—'}`\n"
        f"- **Cargo:** `{registro.cargo_principal or '—'}`\n"
        f"- **Tipo:** {tipo_rotulo}\n"
        f"- **Período:** `{registro.periodo_rotulo}`\n"
        f"- **Início:** `{_formatar_data_curta(registro.data_inicio)}`\n"
        f"- **Fim:** `{_formatar_data_curta(registro.data_fim)}`\n"
        f"- **Motivo:** {registro.motivo or '—'}\n"
        f"`🕐` **Solicitado em:** `{_formatar_momento_brasilia(registro.data_solicitacao)}`\n"
        f"`📌` **Status:** 🟡 **Pendente**"
    )

    view = ViewDecisaoAusencia(
        solicitacao_id=registro.id,
        corpo=corpo,
        url_thumb=membro.display_avatar.url,
    )
    mensagem = await canal.send(view=view)
    await marcar_mensagem_pedido(registro.id, canal.id, mensagem.id)


class ViewDecisaoAusencia(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, *, solicitacao_id: int, corpo: str, url_thumb: str):
        super().__init__(timeout=None)
        self.solicitacao_id = solicitacao_id

        linha = discord.ui.ActionRow()
        botao_ok = discord.ui.Button(
            label="Aprovar",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"{CUSTOM_ID_APROVAR}{solicitacao_id}",
        )
        botao_no = discord.ui.Button(
            label="Negar",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"{CUSTOM_ID_REPROVAR}{solicitacao_id}",
        )
        linha.add_item(botao_ok)
        linha.add_item(botao_no)

        self.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    f"# 📩 Pedido de Ausência `#{solicitacao_id}`",
                    corpo,
                    accessory=discord.ui.Thumbnail(url_thumb),
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                accent_color=discord.Color.blurple(),
            )
        )


async def processar_decisao_ausencia(
    interacao: discord.Interaction,
    pedido_id: int,
    *,
    aprovada: bool,
) -> None:
    if interacao.guild is None or not isinstance(interacao.user, discord.Member):
        await responder_erro(
            interacao,
            titulo="Contexto inválido",
            linhas=["Use dentro do servidor."],
        )
        return

    if not membro_e_diretoria(interacao.user):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Apenas a **Diretoria** pode decidir pedidos de ausência."],
        )
        return

    registro, decidido_agora = await decidir_ausencia(
        solicitacao_id=pedido_id,
        aprovada=aprovada,
        diretor=interacao.user,
    )
    if registro is None:
        await responder_erro(
            interacao,
            titulo="Pedido não encontrado",
            linhas=[f"Solicitação `#{pedido_id}` não existe."],
        )
        return
    if not decidido_agora:
        await responder_aviso(
            interacao,
            titulo="Já decidido",
            linhas=[f"Este pedido já está **{registro.status}**."],
        )
        return

    membro = interacao.guild.get_member(registro.discord_id)
    tipo_rotulo = TIPOS_AUSENCIA.get(registro.tipo, registro.tipo)
    status_emoji = "✅ Aprovada" if aprovada else "❌ Negada"

    if aprovada and membro is not None:
        ok, msg = await aplicar_cargos_ausencia(
            membro,
            executor=interacao.user,
            motivo=registro.motivo or tipo_rotulo,
        )
        if not ok:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Falha ao aplicar cargos de ausência",
                Exception(msg),
                contexto=f"processar_decisao_ausencia#{pedido_id}",
                usuario=membro,
            )

    # Atualiza a mensagem do pedido
    corpo_atualizado = (
        f"`👤` **Membro:** <@{registro.discord_id}> (`{registro.discord_id}`)\n"
        f"- **FID:** `{registro.id_fivem or '—'}`\n"
        f"- **Cargo:** `{registro.cargo_principal or '—'}`\n"
        f"- **Tipo:** {tipo_rotulo}\n"
        f"- **Período:** `{registro.periodo_rotulo}`\n"
        f"- **Início:** `{_formatar_data_curta(registro.data_inicio)}`\n"
        f"- **Fim:** `{_formatar_data_curta(registro.data_fim)}`\n"
        f"- **Motivo:** {registro.motivo or '—'}\n"
        f"`🕐` **Solicitado em:** `{_formatar_momento_brasilia(registro.data_solicitacao)}`\n"
        f"`📌` **Status:** {status_emoji}\n"
        f"`🛡️` **Decidido por:** {interacao.user.mention}\n"
        f"`🕐` **Decisão em:** `{_formatar_momento_brasilia()}`"
    )

    view_final = discord.ui.LayoutView(timeout=None)
    view_final.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                f"# 📩 Pedido de Ausência `#{pedido_id}`\n{corpo_atualizado}"
            ),
            accent_color=discord.Color.green() if aprovada else discord.Color.red(),
        )
    )
    try:
        await interacao.response.edit_message(view=view_final)
    except discord.HTTPException:
        await interacao.response.defer()

    # DM ao membro
    try:
        await enviar_dm_card(
            interacao.client.get_user(registro.discord_id) or membro,
            titulo=f"Ausência {status_emoji}",
            linhas=[
                f"Seu pedido `#{pedido_id}` foi **{registro.status}**.",
                f"Tipo: {tipo_rotulo} · Período: {registro.periodo_rotulo}",
                f"Início: {_formatar_data_curta(registro.data_inicio)} · Fim: {_formatar_data_curta(registro.data_fim)}",
            ],
            cor=discord.Color.green() if aprovada else COR_AVISO,
        )
    except Exception:
        pass


def view_painel_ausencia(guilda: discord.Guild | None = None) -> PainelAusenciaLayout:
    return PainelAusenciaLayout(guilda=guilda)
