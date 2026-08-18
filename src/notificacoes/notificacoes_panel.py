"""Painel persistente e fluxos ephemeral de notificação por DM.

1. Painel fixo no canal
2. Destino (membro ou cargo)
3. Construtor igual ao /templates (blocos + preview ao vivo)
   — só com as opções pedidas: seção/texto/título/separador, botão, rodapé,
     editar/mover/remover, cor, enviar/resetar/cancelar
"""

from __future__ import annotations

import discord

from src.notificacoes.notificacoes_service import (
    LIMITE_NOTIFICACOES_POR_HORA,
    ainda_pode_enviar,
    destino_esta_pronto,
    enviar_notificacao_da_sessao,
    limpar_sessao,
    obter_sessao,
    quantidade_envios_na_hora,
    rascunho_tem_conteudo,
    resumo_destino,
)
from src.plantao.plantao_permissoes import (
    e_diretoria,
    mensagem_sem_permissao,
)
from src.templates.templates_cogs import (
    ModalBotao,
    ModalEscolherBloco,
    ModalRodape,
    ModalSecaoCompleta,
    ModalSeparador,
    ModalTextoBloco,
    _modal_para_bloco,
)
from src.templates.templates_modelo import (
    limpar_rascunho,
    mapa_de_cores,
    montar_preview,
    montar_texto_do_rodape,
    obter_rascunho,
    resumo_dos_blocos,
)
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
    ignorar_falha_cosmetica,
)
from src.utils.mensagens import (
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    editar_mensagem_original,
    responder_aviso,
    responder_erro,
    responder_view,
)

# ═══════════════════════════════════════════════════════════════════════════
# PAINEL PRINCIPAL (persistente no canal)
# ═══════════════════════════════════════════════════════════════════════════


class PainelNotificacaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Card fixo do canal de envio de notificação por DM."""

    def __init__(self, guilda: discord.Guild):
        super().__init__(timeout=None)
        self.guilda = guilda

        url_icone = guilda.icon.url if guilda.icon else None

        if url_icone:
            bloco_cabecalho = discord.ui.Section(
                "# 📬 Painel de Notificação por DM\n\n"
                "> Use as opções abaixo para enviar notificações via mensagem "
                "direta aos membros do servidor!\n"
                "> Todas as notificações são registradas em log, portanto "
                "evite abusar!\n"
                "> Caso tenha dúvidas entre em contato com os Gerais!",
                accessory=discord.ui.Thumbnail(url_icone),
            )
        else:
            bloco_cabecalho = discord.ui.TextDisplay(
                "# 📬 Painel de Notificação por DM\n\n"
                "> Use as opções abaixo para enviar notificações via mensagem "
                "direta aos membros do servidor!\n"
                "> Todas as notificações são registradas em log, portanto "
                "evite abusar!\n"
                "> Caso tenha dúvidas entre em contato com os Gerais!"
            )

        linha_botao = discord.ui.ActionRow()
        botao_iniciar = discord.ui.Button(
            label="Iniciar notificação",
            style=discord.ButtonStyle.primary,
            emoji="📰",
            custom_id="notificacao:iniciar",
        )
        botao_iniciar.callback = self._ao_clicar_iniciar
        linha_botao.add_item(botao_iniciar)

        self.add_item(
            discord.ui.Container(
                bloco_cabecalho,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    "## 📌 Antes de iniciar\n\n"
                    "✅ Certifique-se de que o membro **aceita receber DMs** "
                    "do servidor.\n"
                    "✅ Tenha em mãos o **nome, cargo ou ID** do destinatário.\n"
                    "✅ Verifique se o conteúdo da mensagem está de acordo com "
                    "as **regras do servidor**.\n"
                    "✅ Confirme se a notificação é realmente necessária para "
                    "evitar spam."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    "## 📌 Configurações Ativas\n\n"
                    "**➜ Sistema de Notificação por DM:** `✅ Configurado`\n"
                    "**➜ Registro de Logs:** `✅ Ativado`\n"
                    f"**➜ Limite de Notificações por Hora:** "
                    f"`{LIMITE_NOTIFICACOES_POR_HORA} por usuário`"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_botao,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_clicar_iniciar(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member) or not e_diretoria(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    mensagem_sem_permissao("usar o painel de notificação"),
                ],
            )
            return

        if not ainda_pode_enviar(membro.id):
            usados = quantidade_envios_na_hora(membro.id)
            await responder_erro(
                interacao,
                titulo="Limite por hora atingido",
                linhas=[
                    f"Limite de **{LIMITE_NOTIFICACOES_POR_HORA}** notificações "
                    f"por hora atingido (`{usados}` neste período).\n"
                    "Aguarde um pouco antes de enviar outra.",
                ],
            )
            return

        limpar_sessao(membro.id)
        obter_sessao(membro.id)
        await responder_view(
            interacao,
            FluxoEscolherDestinoView(id_do_executor=membro.id),
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════════════
# ETAPA 1 — escolher destino (membro ou cargo)
# ═══════════════════════════════════════════════════════════════════════════


class FluxoEscolherDestinoView(LoggingViewMixin, discord.ui.LayoutView):
    """Ephemeral: tipo de destino + seleção + Continuar."""

    def __init__(self, id_do_executor: int):
        super().__init__(timeout=600)
        self.id_do_executor = id_do_executor
        self._reconstruir()

    def _reconstruir(self) -> None:
        self.clear_items()
        sessao = obter_sessao(self.id_do_executor)

        tipo_txt = {
            "membro": "👤 Membro único",
            "cargo": "🏷️ Cargo específico",
        }.get(sessao.tipo_destino or "", "*Nenhum*")

        linha_tipo = discord.ui.ActionRow()
        seletor_tipo = discord.ui.Select(
            placeholder="Como deseja notificar?",
            options=[
                discord.SelectOption(
                    label="Membro único",
                    value="membro",
                    emoji="👤",
                    description="Um único membro (UserSelect ou ID Discord)",
                    default=sessao.tipo_destino == "membro",
                ),
                discord.SelectOption(
                    label="Cargo específico",
                    value="cargo",
                    emoji="🏷️",
                    description="Todos os membros com aquele cargo",
                    default=sessao.tipo_destino == "cargo",
                ),
            ],
        )
        seletor_tipo.callback = self._ao_escolher_tipo
        linha_tipo.add_item(seletor_tipo)

        componentes: list = [
            discord.ui.TextDisplay("# 📰 Iniciar notificação"),
            discord.ui.TextDisplay(
                "### Destino atual\n"
                f"> **Tipo:** {tipo_txt}\n"
                f"> **Alvo:** {resumo_destino(sessao)}\n\n"
                "Escolha o tipo, selecione o destino e clique em **Continuar**."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay("### 1) Tipo de destino"),
            linha_tipo,
        ]

        if sessao.tipo_destino == "membro":
            linha_user = discord.ui.ActionRow()
            seletor_user = discord.ui.UserSelect(
                placeholder="Selecione o membro no Discord…",
                min_values=1,
                max_values=1,
            )
            seletor_user.callback = self._ao_escolher_membro
            linha_user.add_item(seletor_user)

            linha_id = discord.ui.ActionRow()
            botao_id = discord.ui.Button(
                label="Buscar membro pelo Discord ID",
                style=discord.ButtonStyle.secondary,
                emoji="🔍",
            )
            botao_id.callback = self._ao_buscar_id
            linha_id.add_item(botao_id)

            componentes.extend(
                [
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                    discord.ui.TextDisplay("### 2) Escolher o membro"),
                    linha_user,
                    linha_id,
                ]
            )

        elif sessao.tipo_destino == "cargo":
            linha_cargo = discord.ui.ActionRow()
            seletor_cargo = discord.ui.RoleSelect(
                placeholder="Selecione o cargo…",
                min_values=1,
                max_values=1,
            )
            seletor_cargo.callback = self._ao_escolher_cargo
            linha_cargo.add_item(seletor_cargo)

            componentes.extend(
                [
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                    discord.ui.TextDisplay(
                        "### 2) Escolher o cargo\n"
                        "-# Todos os membros **não-bot** com esse cargo "
                        "receberão a DM."
                    ),
                    linha_cargo,
                ]
            )

        linha_acoes = discord.ui.ActionRow()
        botao_continuar = discord.ui.Button(
            label="Continuar",
            style=discord.ButtonStyle.success,
            emoji="➡️",
            disabled=not destino_esta_pronto(sessao),
        )
        botao_continuar.callback = self._ao_continuar
        linha_acoes.add_item(botao_continuar)

        botao_cancelar = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            emoji="✖️",
        )
        botao_cancelar.callback = self._ao_cancelar
        linha_acoes.add_item(botao_cancelar)

        componentes.extend(
            [
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_acoes,
            ]
        )

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _garantir_dono(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.id_do_executor:
            await responder_erro(
                interacao,
                titulo="Painel de outra pessoa",
                linhas=[
                    "Este painel não é seu.",
                ],
            )
            return False
        return True

    async def _atualizar(self, interacao: discord.Interaction) -> None:
        self._reconstruir()
        await editar_mensagem_original(
            interacao,
            view=self,
        )

    async def _ao_escolher_tipo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        valor = interacao.data["values"][0]
        sessao = obter_sessao(self.id_do_executor)
        sessao.tipo_destino = valor
        sessao.id_do_membro = None
        sessao.mencao_do_membro = None
        sessao.id_do_cargo = None
        sessao.nome_do_cargo = None
        await self._atualizar(interacao)

    async def _ao_escolher_membro(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        id_membro = int(interacao.data["values"][0])
        membro = interacao.guild.get_member(id_membro) if interacao.guild else None
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Não encontrado",
                linhas=[
                    "Membro não encontrado no servidor.",
                ],
            )
            return
        sessao = obter_sessao(self.id_do_executor)
        sessao.id_do_membro = membro.id
        sessao.mencao_do_membro = membro.mention
        await self._atualizar(interacao)

    async def _ao_buscar_id(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalDiscordIdNotificacao(self))

    async def _ao_escolher_cargo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        id_cargo = int(interacao.data["values"][0])
        cargo = interacao.guild.get_role(id_cargo) if interacao.guild else None
        if cargo is None:
            await responder_erro(
                interacao,
                titulo="Não encontrado",
                linhas=[
                    "Cargo não encontrado.",
                ],
            )
            return
        sessao = obter_sessao(self.id_do_executor)
        sessao.id_do_cargo = cargo.id
        sessao.nome_do_cargo = cargo.name
        await self._atualizar(interacao)

    async def _ao_continuar(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        sessao = obter_sessao(self.id_do_executor)
        if not destino_esta_pronto(sessao):
            await responder_aviso(
                interacao,
                titulo="Falta escolher",
                linhas=[
                    "Selecione o destino antes de continuar.",
                ],
            )
            return

        limpar_rascunho(self.id_do_executor)
        obter_rascunho(self.id_do_executor)

        painel = FluxoComporNotificacaoView(
            id_do_usuario=self.id_do_executor,
            guilda=interacao.guild,
        )
        await editar_mensagem_original(
            interacao,
            view=painel,
        )
        painel.mensagem_editor = await interacao.original_response()

        view_preview = montar_preview(
            obter_rascunho(self.id_do_executor),
            interacao.guild,
        )
        mensagem_preview = await responder_view(
            interacao,
            view_preview,
            ephemeral=True,
        )
        painel.mensagem_preview = mensagem_preview

    async def _ao_cancelar(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        limpar_sessao(self.id_do_executor)
        await editar_mensagem_original(
            interacao,
            view=_view_mensagem_simples(
                titulo="❌ Notificação cancelada",
                linhas=["Nada foi enviado."],
                cor=COR_ERRO,
            ),
        )


class ModalDiscordIdNotificacao(
    LoggingModalMixin, discord.ui.Modal, title="Buscar por Discord ID"
):
    campo_id = discord.ui.TextInput(
        label="ID do Discord do membro",
        placeholder="Ex.: 123456789012345678",
        required=True,
        min_length=15,
        max_length=22,
    )

    def __init__(self, painel: FluxoEscolherDestinoView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        """Resolve o membro digitado e atualiza o destino da sessão privada.

        Valida o formato e confirma que o ID pertence à guilda, inclusive fora
        do cache. Só então substitui o destino por membro e redesenha o painel,
        evitando o envio acidental para uma referência inválida.
        """
        texto = self.campo_id.value.strip()
        if not texto.isdigit():
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "ID inválido. Use apenas números.",
                ],
            )
            return

        id_membro = int(texto)
        membro = interacao.guild.get_member(id_membro) if interacao.guild else None
        if membro is None and interacao.guild is not None:
            try:
                membro = await interacao.guild.fetch_member(id_membro)
            except (discord.NotFound, discord.HTTPException):
                membro = None

        if membro is None:
            await responder_erro(
                interacao,
                titulo="Membro fora do servidor",
                linhas=[
                    "Membro não está no servidor (ou ID incorreto).",
                ],
            )
            return

        sessao = obter_sessao(self.painel.id_do_executor)
        sessao.tipo_destino = "membro"
        sessao.id_do_membro = membro.id
        sessao.mencao_do_membro = membro.mention
        sessao.id_do_cargo = None
        sessao.nome_do_cargo = None
        self.painel._reconstruir()
        await editar_mensagem_original(
            interacao,
            view=self.painel,
        )


# ═══════════════════════════════════════════════════════════════════════════
# ETAPA 2 — construtor (mesmo fluxo do /templates, opções filtradas)
# ═══════════════════════════════════════════════════════════════════════════


class FluxoComporNotificacaoView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Editor de blocos + preview separado — API compatível com os modais
    de templates_cogs (id_do_usuario, indice_em_edicao, _atualizar_painel).
    """

    def __init__(
        self,
        id_do_usuario: int,
        guilda: discord.Guild | None = None,
        mensagem_preview: discord.Message | None = None,
    ):
        super().__init__(timeout=1200)
        self.id_do_usuario = id_do_usuario
        self.guilda = guilda
        self.mensagem_preview = mensagem_preview
        self.mensagem_editor: discord.Message | None = None
        self.indice_em_edicao: int | None = None
        self._reconstruir()

    def _reconstruir(self) -> None:
        self.clear_items()
        rascunho = obter_rascunho(self.id_do_usuario)
        sessao = obter_sessao(self.id_do_usuario)

        linha_1 = discord.ui.ActionRow()
        for rotulo, emoji, funcao_ao_clicar in (
            ("Seção", "🗂️", self._ao_clicar_secao),
            ("Texto", "📝", self._ao_clicar_texto),
            ("Título", "✏️", self._ao_clicar_titulo),
            ("Separador", "➖", self._ao_clicar_separador),
        ):
            botao = discord.ui.Button(
                label=rotulo,
                style=discord.ButtonStyle.primary,
                emoji=emoji,
            )
            botao.callback = funcao_ao_clicar
            linha_1.add_item(botao)

        linha_2 = discord.ui.ActionRow()
        for rotulo, emoji, funcao_ao_clicar in (
            ("Botão", "🔘", self._ao_clicar_botao),
            ("Rodapé", "📌", self._ao_clicar_rodape),
        ):
            botao = discord.ui.Button(
                label=rotulo,
                style=discord.ButtonStyle.secondary,
                emoji=emoji,
            )
            botao.callback = funcao_ao_clicar
            linha_2.add_item(botao)

        linha_3 = discord.ui.ActionRow()
        for rotulo, emoji, estilo, funcao_ao_clicar in (
            (
                "Editar bloco",
                "📝",
                discord.ButtonStyle.primary,
                self._ao_clicar_editar_bloco,
            ),
            (
                "Editar último",
                "✏️",
                discord.ButtonStyle.primary,
                self._ao_clicar_editar_ultimo,
            ),
            (
                "Mover bloco",
                "↕️",
                discord.ButtonStyle.secondary,
                self._ao_clicar_mover_bloco,
            ),
            (
                "Remover último",
                "🗑️",
                discord.ButtonStyle.danger,
                self._ao_clicar_remover_ultimo,
            ),
        ):
            botao = discord.ui.Button(label=rotulo, style=estilo, emoji=emoji)
            botao.callback = funcao_ao_clicar
            linha_3.add_item(botao)

        linha_cor = discord.ui.ActionRow()
        seletor_cor = discord.ui.Select(
            placeholder="Cor do Container…",
            options=[
                discord.SelectOption(label=nome.title(), value=nome)
                for nome in mapa_de_cores().keys()
            ],
        )
        seletor_cor.callback = self._ao_escolher_cor
        linha_cor.add_item(seletor_cor)

        linha_acoes = discord.ui.ActionRow()
        for rotulo, emoji, estilo, funcao_ao_clicar in (
            ("Enviar", "📨", discord.ButtonStyle.success, self._ao_clicar_enviar),
            ("Resetar", "♻️", discord.ButtonStyle.secondary, self._ao_clicar_resetar),
            ("Cancelar", "✖️", discord.ButtonStyle.danger, self._ao_clicar_cancelar),
        ):
            botao = discord.ui.Button(label=rotulo, style=estilo, emoji=emoji)
            botao.callback = funcao_ao_clicar
            linha_acoes.add_item(botao)

        status_rodape = "ligado" if rascunho.rodape_ativo else "desligado"
        texto_rodape = (
            montar_texto_do_rodape(rascunho, self.guilda)
            if rascunho.rodape_ativo
            else ""
        )
        status_preview = (
            "mensagem separada ativa"
            if self.mensagem_preview is not None
            else "aguardando…"
        )

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 📨 Montar notificação (DM)\n"
                    f"-# Destino: {resumo_destino(sessao)}\n"
                    "-# O **preview** fica em outra mensagem e atualiza a cada mudança."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    f"### Blocos ({len(rascunho.blocos)})\n"
                    f"{resumo_dos_blocos(rascunho)}\n\n"
                    f"**Cor:** `{rascunho.cor_nome}` · **Rodapé:** `{status_rodape}`"
                    + (f"\n{texto_rodape}" if texto_rodape else "")
                    + f"\n-# Preview: {status_preview}"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_1,
                linha_2,
                linha_3,
                linha_cor,
                linha_acoes,
                accent_color=discord.Color.dark_teal(),
            )
        )

    async def _garantir_dono(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.id_do_usuario:
            await responder_erro(
                interacao,
                titulo="Painel de outra pessoa",
                linhas=[
                    "Este painel não é seu.",
                ],
            )
            return False
        return True

    async def _atualizar_mensagem_preview(self) -> None:
        if self.mensagem_preview is None:
            return
        rascunho = obter_rascunho(self.id_do_usuario)
        view_preview = montar_preview(rascunho, self.guilda)
        try:
            await self.mensagem_preview.edit(view=view_preview)
        except (discord.NotFound, discord.HTTPException):
            self.mensagem_preview = None

    async def _destruir_mensagem_preview(self) -> None:
        if self.mensagem_preview is None:
            return
        try:
            await self.mensagem_preview.delete()
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ) as erro_em_destruir_mensagem_preview:
            # Enfeite que falhou: apagar a mensagem de preview.
            # A acao principal ja tinha dado certo, entao so registro.
            ignorar_falha_cosmetica(
                erro_em_destruir_mensagem_preview,
                o_que_falhou="apagar a mensagem de preview",
            )
        self.mensagem_preview = None

    async def on_timeout(self) -> None:
        """Apaga o preview temporário quando a edição da notificação expira."""
        await self._destruir_mensagem_preview()

    async def _atualizar_painel(
        self,
        interacao: discord.Interaction | None = None,
    ) -> None:
        """Mesmo contrato do PainelTemplatesView — usado pelos modais importados."""
        if interacao is not None and interacao.guild is not None:
            self.guilda = interacao.guild

        if interacao is not None and not interacao.response.is_done():
            try:
                await interacao.response.defer(ephemeral=True)
            except discord.HTTPException as erro_em_atualizar_painel:
                # Enfeite que falhou: atualizar o painel na tela.
                # A acao principal ja tinha dado certo, entao so registro.
                ignorar_falha_cosmetica(
                    erro_em_atualizar_painel,
                    o_que_falhou="atualizar o painel na tela",
                )

        self._reconstruir()

        if self.mensagem_editor is not None:
            try:
                await self.mensagem_editor.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                self.mensagem_editor = None
        elif interacao is not None:
            try:
                await editar_mensagem_original(
                    interacao,
                    view=self,
                )
            except (
                discord.HTTPException,
                discord.NotFound,
            ) as erro_em_atualizar_painel:
                # Enfeite que falhou: atualizar o painel na tela.
                # A acao principal ja tinha dado certo, entao so registro.
                ignorar_falha_cosmetica(
                    erro_em_atualizar_painel,
                    o_que_falhou="atualizar o painel na tela",
                )

        await self._atualizar_mensagem_preview()

    async def _ao_clicar_titulo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalTextoBloco(self, "titulo"))

    async def _ao_clicar_texto(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalTextoBloco(self, "texto"))

    async def _ao_clicar_secao(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalSecaoCompleta(self))

    async def _ao_clicar_separador(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalSeparador(self))

    async def _ao_clicar_botao(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalBotao(self))

    async def _ao_clicar_rodape(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalRodape(self))

    async def _ao_clicar_remover_ultimo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        rascunho = obter_rascunho(self.id_do_usuario)
        if rascunho.blocos:
            rascunho.blocos.pop()
        await self._atualizar_painel(interacao)

    async def _ao_clicar_editar_ultimo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        rascunho = obter_rascunho(self.id_do_usuario)
        if not rascunho.blocos:
            await responder_erro(
                interacao,
                titulo="Nada para editar",
                linhas=[
                    "Não há bloco para editar.",
                ],
            )
            return

        self.indice_em_edicao = len(rascunho.blocos) - 1
        ultimo = rascunho.blocos[-1]
        modal = _modal_para_bloco(self, ultimo)
        if modal is None:
            self.indice_em_edicao = None
            await responder_erro(
                interacao,
                titulo="Bloco sem editor",
                linhas=[
                    f"Tipo `{ultimo.tipo}` sem editor.",
                ],
            )
            return
        await interacao.response.send_modal(modal)

    async def _ao_clicar_editar_bloco(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        rascunho = obter_rascunho(self.id_do_usuario)
        if not rascunho.blocos:
            await responder_erro(
                interacao,
                titulo="Sem blocos",
                linhas=["Não há blocos para editar. Adicione algum primeiro."],
            )
            return
        await interacao.response.send_modal(ModalEscolherBloco(self, modo="editar"))

    async def _ao_clicar_mover_bloco(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        rascunho = obter_rascunho(self.id_do_usuario)
        if len(rascunho.blocos) < 2:
            await responder_erro(
                interacao,
                titulo="Poucos blocos",
                linhas=["É preciso ter pelo menos **2 blocos** para mover."],
            )
            return
        await interacao.response.send_modal(ModalEscolherBloco(self, modo="mover"))

    async def _ao_escolher_cor(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        valores = interacao.data.get("values") if interacao.data else None
        if valores:
            obter_rascunho(self.id_do_usuario).cor_nome = valores[0]
        await self._atualizar_painel(interacao)

    async def _ao_clicar_resetar(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        limpar_rascunho(self.id_do_usuario)
        await self._atualizar_painel(interacao)

    async def _ao_clicar_cancelar(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await self._destruir_mensagem_preview()
        limpar_sessao(self.id_do_usuario)
        await editar_mensagem_original(
            interacao,
            view=_view_mensagem_simples(
                titulo="❌ Notificação cancelada",
                linhas=["Rascunho e destino foram descartados."],
                cor=COR_ERRO,
            ),
        )

    async def _ao_clicar_enviar(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return

        sessao = obter_sessao(self.id_do_usuario)
        if not destino_esta_pronto(sessao):
            await responder_erro(
                interacao,
                titulo="Dado inválido",
                linhas=[
                    "Destino inválido. Cancele e comece de novo.",
                ],
            )
            return

        if not rascunho_tem_conteudo(self.id_do_usuario):
            await responder_erro(
                interacao,
                titulo="Rascunho vazio",
                linhas=["Adicione pelo menos um bloco antes de enviar."],
            )
            return

        if not ainda_pode_enviar(self.id_do_usuario):
            await responder_erro(
                interacao,
                titulo="Limite por hora atingido",
                linhas=[
                    f"Limite de **{LIMITE_NOTIFICACOES_POR_HORA}** por hora atingido.",
                ],
            )
            return

        if interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Comando indisponível aqui",
                linhas=[
                    "Este fluxo só funciona dentro do servidor.",
                ],
            )
            return

        await interacao.response.defer(ephemeral=True)
        await self._destruir_mensagem_preview()

        resultado = await enviar_notificacao_da_sessao(interacao.guild, sessao)

        if resultado.total == 0:
            titulo = "❌ Nenhum destinatário"
            linhas = [
                "Não foi possível resolver membros para o destino escolhido.",
            ]
            cor = COR_ERRO
        elif resultado.enviados == 0:
            titulo = "❌ Falha no envio"
            linhas = [
                f"Tentativas: **{resultado.total}**",
                "Nenhuma DM chegou (DMs fechadas ou bot sem permissão).",
            ]
            cor = COR_ERRO
        elif resultado.falhas == 0:
            titulo = "✅ Notificação enviada"
            linhas = [
                f"Destino: {resumo_destino(sessao)}",
                f"Enviadas: **{resultado.enviados}** / {resultado.total}",
            ]
            cor = COR_SUCESSO
        else:
            titulo = "⚠️ Envio parcial"
            linhas = [
                f"Destino: {resumo_destino(sessao)}",
                f"Enviadas: **{resultado.enviados}** · Falhas: **{resultado.falhas}**",
                f"(total resolvido: {resultado.total})",
            ]
            cor = COR_INFO

        limpar_sessao(self.id_do_usuario)
        await editar_mensagem_original(
            interacao,
            view=_view_mensagem_simples(titulo=titulo, linhas=linhas, cor=cor),
        )


def _view_mensagem_simples(
    *,
    titulo: str,
    linhas: list[str],
    cor: discord.Color,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=120)
    texto_linhas = "\n".join(f"> {linha}" for linha in linhas)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {titulo}"),
            discord.ui.TextDisplay(texto_linhas),
            accent_color=cor,
        )
    )
    return view
