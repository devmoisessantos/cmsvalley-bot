"""
Cog /templates — painel interativo do construtor Bot UI Kit (Components V2).

Usa src.templates.templates_modelo para estado, preview e geração de código.
Interface didática: cada botão adiciona um bloco ou gera artefatos.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.templates.templates_modelo import (
    TEM_CHANNEL_SELECT,
    TEM_ROLE_SELECT,
    TEM_USER_SELECT,
    BlocoTemplate,
    gerar_codigo_mensagem,
    gerar_codigo_modal,
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
)
from src.utils.mensagens import (
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    enviar_card,
    responder_erro,
)
from src.utils.permissions import apenas_administrador

# ---------------------------------------------------------------------------
# Painel de edição
# ---------------------------------------------------------------------------


def _indice_alvo_edicao(painel: "PainelTemplatesView") -> int:
    """Índice do bloco a editar: escolhido ou o último."""
    rascunho = obter_rascunho(painel.id_do_usuario)
    if painel.indice_em_edicao is not None and 0 <= painel.indice_em_edicao < len(
        rascunho.blocos
    ):
        return painel.indice_em_edicao
    return len(rascunho.blocos) - 1


def _aplicar_bloco_editado(painel: "PainelTemplatesView", novo) -> None:
    rascunho = obter_rascunho(painel.id_do_usuario)
    indice = _indice_alvo_edicao(painel)
    if 0 <= indice < len(rascunho.blocos):
        rascunho.blocos[indice] = novo
    else:
        rascunho.blocos.append(novo)
    painel.indice_em_edicao = None


def _bloco_em_edicao(painel: "PainelTemplatesView"):
    rascunho = obter_rascunho(painel.id_do_usuario)
    indice = _indice_alvo_edicao(painel)
    if 0 <= indice < len(rascunho.blocos):
        return rascunho.blocos[indice]
    return None


class PainelTemplatesView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Painel do editor (efêmero) + mensagem de preview separada (V2 real).

    A cada alteração o preview é editado na outra mensagem.
    No timeout / reset a mensagem de preview é apagada.
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
        # Mensagem separada com o Container V2 completo (editada a cada update)
        self.mensagem_preview = mensagem_preview
        self.mensagem_editor: discord.Message | None = None
        # Quando "Editar bloco" escolhe um índice, os modais usam este valor
        self.indice_em_edicao: int | None = None
        self._reconstruir()

    def _reconstruir(self) -> None:
        """Só o editor: lista de blocos + botões (sem preview embutido)."""
        self.clear_items()
        rascunho = obter_rascunho(self.id_do_usuario)

        # 1) secao | texto | titulo | separador
        linha_1 = discord.ui.ActionRow()
        for rotulo, emoji, callback in (
            ("Seção", "🗂️", self._ao_clicar_secao),
            ("Texto", "📝", self._ao_clicar_texto),
            ("Título", "✏️", self._ao_clicar_titulo),
            ("Separador", "➖", self._ao_clicar_separador),
        ):
            botao = discord.ui.Button(
                label=rotulo, style=discord.ButtonStyle.primary, emoji=emoji
            )
            botao.callback = callback
            linha_1.add_item(botao)

        # 2) arquivo | galeria | botao | select
        linha_2 = discord.ui.ActionRow()
        for rotulo, emoji, callback in (
            ("Arquivo", "📎", self._ao_clicar_arquivo),
            ("Galeria", "🖼️", self._ao_clicar_galeria),
            ("Botão", "🔘", self._ao_clicar_botao),
            ("Select", "📋", self._ao_clicar_select_string),
        ):
            botao = discord.ui.Button(
                label=rotulo, style=discord.ButtonStyle.secondary, emoji=emoji
            )
            botao.callback = callback
            linha_2.add_item(botao)

        # 3) sel. usuarios | sel. cargo | sel. canais | rodapé
        linha_3 = discord.ui.ActionRow()
        for rotulo, emoji, callback in (
            ("Sel. usuários", "👤", self._ao_clicar_select_user),
            ("Sel. cargo", "🎭", self._ao_clicar_select_role),
            ("Sel. canais", "#️⃣", self._ao_clicar_select_channel),
            ("Rodapé", "📌", self._ao_clicar_rodape),
        ):
            botao = discord.ui.Button(
                label=rotulo, style=discord.ButtonStyle.secondary, emoji=emoji
            )
            botao.callback = callback
            linha_3.add_item(botao)

        # 4) Editar bloco | Editar último | Mover bloco | Remover último
        linha_4 = discord.ui.ActionRow()
        for rotulo, emoji, estilo, callback in (
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
            botao.callback = callback
            linha_4.add_item(botao)

        # 5) Código Painel | Código Modal
        linha_5 = discord.ui.ActionRow()
        for rotulo, emoji, estilo, callback in (
            ("Código Painel", "📄", discord.ButtonStyle.danger, self._ao_clicar_codigo),
            (
                "Código Modal",
                "🪟",
                discord.ButtonStyle.secondary,
                self._ao_clicar_codigo_modal,
            ),
        ):
            botao = discord.ui.Button(label=rotulo, style=estilo, emoji=emoji)
            botao.callback = callback
            linha_5.add_item(botao)

        # Cor (Select sozinho na linha)
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

        # Preview | Resetar | Ajuda
        linha_acoes = discord.ui.ActionRow()
        for rotulo, emoji, estilo, callback in (
            ("Preview", "👁️", discord.ButtonStyle.success, self._ao_clicar_preview),
            ("Resetar", "♻️", discord.ButtonStyle.secondary, self._ao_clicar_resetar),
            ("Ajuda", "❓", discord.ButtonStyle.primary, self._ao_clicar_ajuda_api),
        ):
            botao = discord.ui.Button(label=rotulo, style=estilo, emoji=emoji)
            botao.callback = callback
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
                    "# 🧩 Construtor Bot UI Kit (V2)\n"
                    "-# O **preview** fica em outra mensagem e é atualizado a cada mudança."
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
                linha_4,
                linha_5,
                linha_cor,
                linha_acoes,
                accent_color=discord.Color.dark_teal(),
            )
        )

    async def _garantir_dono(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.id_do_usuario:
            await interacao.response.send_message(
                "❌ Este painel não é seu.", ephemeral=True
            )
            return False
        return True

    async def _atualizar_mensagem_preview(self) -> None:
        """Edita a mensagem separada com o Container V2 atual."""
        if self.mensagem_preview is None:
            return
        rascunho = obter_rascunho(self.id_do_usuario)
        view_preview = montar_preview(rascunho, self.guilda)
        try:
            await self.mensagem_preview.edit(view=view_preview)
        except (discord.NotFound, discord.HTTPException):
            # Mensagem sumiu — próxima abertura recria
            self.mensagem_preview = None

    async def _destruir_mensagem_preview(self) -> None:
        if self.mensagem_preview is None:
            return
        try:
            await self.mensagem_preview.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        self.mensagem_preview = None

    async def on_timeout(self) -> None:
        await self._destruir_mensagem_preview()

    async def _atualizar_painel(
        self,
        interacao: discord.Interaction | None = None,
    ) -> None:
        if interacao is not None and interacao.guild is not None:
            self.guilda = interacao.guild
        self._reconstruir()

        # Preferência: editar a mensagem do editor guardada (funciona mesmo
        # quando a interação veio de um card/modal auxiliar).
        if self.mensagem_editor is not None:
            try:
                await self.mensagem_editor.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                self.mensagem_editor = None

        if interacao is not None and self.mensagem_editor is None:
            if interacao.response.is_done():
                try:
                    await interacao.edit_original_response(view=self)
                except (discord.HTTPException, discord.InteractionResponded):
                    pass
            else:
                await interacao.response.edit_message(view=self)

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

    async def _ao_clicar_galeria(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalGaleria(self))

    async def _ao_clicar_arquivo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalArquivo(self))

    async def _ao_clicar_botao(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalBotao(self))

    async def _ao_clicar_select_string(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalSelectString(self))

    async def _ao_clicar_select_user(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        if not TEM_USER_SELECT:
            await interacao.response.send_message(
                "❌ UserSelect indisponível nesta versão.", ephemeral=True
            )
            return
        obter_rascunho(self.id_do_usuario).blocos.append(
            BlocoTemplate(
                tipo="select_user",
                placeholder_select_especial="Escolha um membro…",
            )
        )
        await self._atualizar_painel(interacao)

    async def _ao_clicar_select_role(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        if not TEM_ROLE_SELECT:
            await interacao.response.send_message(
                "❌ RoleSelect indisponível nesta versão.", ephemeral=True
            )
            return
        obter_rascunho(self.id_do_usuario).blocos.append(
            BlocoTemplate(
                tipo="select_role",
                placeholder_select_especial="Escolha um cargo…",
            )
        )
        await self._atualizar_painel(interacao)

    async def _ao_clicar_select_channel(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        if not TEM_CHANNEL_SELECT:
            await interacao.response.send_message(
                "❌ ChannelSelect indisponível nesta versão.", ephemeral=True
            )
            return
        obter_rascunho(self.id_do_usuario).blocos.append(
            BlocoTemplate(
                tipo="select_channel",
                placeholder_select_especial="Escolha um canal…",
            )
        )
        await self._atualizar_painel(interacao)

    async def _ao_clicar_remover_ultimo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        rascunho = obter_rascunho(self.id_do_usuario)
        if rascunho.blocos:
            rascunho.blocos.pop()
        await self._atualizar_painel(interacao)

    async def _ao_clicar_editar_ultimo(self, interacao: discord.Interaction):
        """Abre o modal do tipo do último bloco, já preenchido."""
        if not await self._garantir_dono(interacao):
            return
        rascunho = obter_rascunho(self.id_do_usuario)
        if not rascunho.blocos:
            await interacao.response.send_message(
                "❌ Não há bloco para editar.",
                ephemeral=True,
            )
            return

        self.indice_em_edicao = len(rascunho.blocos) - 1
        ultimo = rascunho.blocos[-1]
        modal = _modal_para_bloco(self, ultimo)
        if modal is None:
            self.indice_em_edicao = None
            await interacao.response.send_message(
                f"❌ Tipo `{ultimo.tipo}` sem editor.",
                ephemeral=True,
            )
            return
        await interacao.response.send_modal(modal)

    async def _ao_clicar_editar_bloco(self, interacao: discord.Interaction):
        """Pede o número do bloco e depois envia CardView com botão Abrir editor."""
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
        """Pede o número do bloco e depois CardView com Select pra cima/baixo."""
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

    async def _ao_clicar_rodape(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalRodape(self))

    async def _ao_escolher_cor(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        valores = interacao.data.get("values") if interacao.data else None
        if valores:
            obter_rascunho(self.id_do_usuario).cor_nome = valores[0]
        await self._atualizar_painel(interacao)

    async def _ao_clicar_preview(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        guilda = interacao.guild or self.guilda
        preview = montar_preview(obter_rascunho(self.id_do_usuario), guilda)
        await interacao.response.send_message(view=preview, ephemeral=True)

    async def _ao_clicar_codigo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        guilda = interacao.guild or self.guilda
        codigo = gerar_codigo_mensagem(obter_rascunho(self.id_do_usuario), guilda)
        await _enviar_codigo_em_partes(interacao, codigo)

    async def _ao_clicar_codigo_modal(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        codigo = gerar_codigo_modal(obter_rascunho(self.id_do_usuario))
        await _enviar_codigo_em_partes(interacao, codigo)

    async def _ao_clicar_resetar(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        limpar_rascunho(self.id_do_usuario)
        await self._atualizar_painel(interacao)

    async def _ao_clicar_ajuda_api(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_message(
            content=(
                "**Hierarquia Components V2**\n"
                "```\n"
                "LayoutView (raiz da mensagem)\n"
                " └─ Container (accent_color)\n"
                "     ├─ TextDisplay\n"
                "     ├─ Separator\n"
                "     ├─ Section → accessory Thumbnail | Button\n"
                "     ├─ MediaGallery → MediaGalleryItem\n"
                "     ├─ File (attachment://…)\n"
                "     └─ ActionRow → Button(s)  OU  1 Select\n"
                "\n"
                "Modal\n"
                " └─ Label\n"
                "     └─ TextInput | FileUpload | RadioGroup | CheckboxGroup\n"
                "```\n"
                "Limites: ≤40 componentes/msg · TextDisplay total ≤4000 chars · "
                "ActionRow ≤5 botões **ou** 1 select · MediaGallery 1–10.\n"
                "Docs: <https://discordpy.readthedocs.io/en/stable/interactions/api.html#bot-ui-kit>"
            ),
            ephemeral=True,
        )


async def _enviar_codigo_em_partes(
    interacao: discord.Interaction,
    codigo: str,
) -> None:
    """Parte o código em mensagens de ≤1800 chars (limite do Discord)."""
    partes: list[str] = []
    restante = codigo
    while restante:
        partes.append(f"```python\n{restante[:1800]}\n```")
        restante = restante[1800:]
    await interacao.response.send_message(content=partes[0], ephemeral=True)
    for pedaco in partes[1:]:
        await interacao.followup.send(content=pedaco, ephemeral=True)


# ---------------------------------------------------------------------------
# Modais
# ---------------------------------------------------------------------------


async def _enviar_card_acao_bloco(
    interacao: discord.Interaction,
    painel: "PainelTemplatesView",
    indice: int,
    modo: str,
) -> None:
    """
    Após o modal do número do bloco, envia CardView (mensagens.py)
    com Select (mover) ou botão (editar).
    """
    rascunho = obter_rascunho(painel.id_do_usuario)
    bloco = rascunho.blocos[indice]
    resumo = f"`{indice + 1}.` **{bloco.tipo}**"
    if bloco.texto:
        resumo += f" — {bloco.texto[:80]}"

    if modo == "mover":
        linha = discord.ui.ActionRow()
        seletor = discord.ui.Select(
            placeholder="Mover este bloco…",
            options=[
                discord.SelectOption(
                    label="1. Pra cima",
                    value="cima",
                    description="Troca de lugar com o bloco anterior",
                    emoji="⬆️",
                ),
                discord.SelectOption(
                    label="2. Pra baixo",
                    value="baixo",
                    description="Troca de lugar com o próximo bloco",
                    emoji="⬇️",
                ),
            ],
        )

        async def _ao_mover(interacao_select: discord.Interaction):
            if interacao_select.user.id != painel.id_do_usuario:
                await responder_erro(
                    interacao_select,
                    titulo="Sem permissão",
                    linhas=["Este controle não é seu."],
                )
                return
            valores = (
                interacao_select.data.get("values") if interacao_select.data else None
            )
            direcao = valores[0] if valores else ""
            rascunho_atual = obter_rascunho(painel.id_do_usuario)
            if indice < 0 or indice >= len(rascunho_atual.blocos):
                await responder_erro(
                    interacao_select,
                    titulo="Bloco sumiu",
                    linhas=["Esse bloco não existe mais no rascunho."],
                )
                return

            if direcao == "cima":
                if indice == 0:
                    await responder_aviso_local(
                        interacao_select,
                        "Já é o primeiro bloco.",
                    )
                    return
                (
                    rascunho_atual.blocos[indice - 1],
                    rascunho_atual.blocos[indice],
                ) = (
                    rascunho_atual.blocos[indice],
                    rascunho_atual.blocos[indice - 1],
                )
                destino = indice
            elif direcao == "baixo":
                if indice >= len(rascunho_atual.blocos) - 1:
                    await responder_aviso_local(
                        interacao_select,
                        "Já é o último bloco.",
                    )
                    return
                (
                    rascunho_atual.blocos[indice + 1],
                    rascunho_atual.blocos[indice],
                ) = (
                    rascunho_atual.blocos[indice],
                    rascunho_atual.blocos[indice + 1],
                )
                destino = indice + 2
            else:
                await responder_erro(
                    interacao_select,
                    titulo="Direção inválida",
                    linhas=["Escolha Pra cima ou Pra baixo."],
                )
                return

            await painel._atualizar_painel(interacao_select)
            await enviar_card(
                interacao_select,
                titulo="✅ Bloco movido",
                linhas=[
                    f"Bloco agora na posição **{destino}**.",
                    "Editor e preview foram atualizados.",
                ],
                cor=COR_SUCESSO,
                delay=12,
            )

        seletor.callback = _ao_mover
        linha.add_item(seletor)
        await enviar_card(
            interacao,
            titulo="↕️ Mover bloco",
            linhas=[
                f"Bloco selecionado: {resumo}",
                "Escolha no menu: **Pra cima** ou **Pra baixo**.",
            ],
            cor=COR_INFO,
            extra_row=linha,
            delay=None,
        )
        return

    # modo == "editar"
    linha = discord.ui.ActionRow()
    botao = discord.ui.Button(
        label="Abrir editor",
        style=discord.ButtonStyle.primary,
        emoji="✏️",
    )

    async def _ao_abrir_editor(interacao_botao: discord.Interaction):
        if interacao_botao.user.id != painel.id_do_usuario:
            await responder_erro(
                interacao_botao,
                titulo="Sem permissão",
                linhas=["Este controle não é seu."],
            )
            return
        rascunho_atual = obter_rascunho(painel.id_do_usuario)
        if indice < 0 or indice >= len(rascunho_atual.blocos):
            await responder_erro(
                interacao_botao,
                titulo="Bloco sumiu",
                linhas=["Esse bloco não existe mais no rascunho."],
            )
            return
        painel.indice_em_edicao = indice
        alvo = rascunho_atual.blocos[indice]
        modal = _modal_para_bloco(painel, alvo)
        if modal is None:
            painel.indice_em_edicao = None
            await responder_erro(
                interacao_botao,
                titulo="Sem editor",
                linhas=[f"Tipo `{alvo.tipo}` ainda não tem modal de edição."],
            )
            return
        await interacao_botao.response.send_modal(modal)

    botao.callback = _ao_abrir_editor
    linha.add_item(botao)
    await enviar_card(
        interacao,
        titulo="✏️ Editar bloco",
        linhas=[
            f"Bloco selecionado: {resumo}",
            "Clique em **Abrir editor** para modificar este bloco.",
        ],
        cor=COR_INFO,
        extra_row=linha,
        delay=None,
    )


async def responder_aviso_local(
    interacao: discord.Interaction,
    texto: str,
) -> None:
    """Aviso rápido via mensagens.py (card)."""
    from src.utils.mensagens import responder_aviso

    await responder_aviso(
        interacao,
        titulo="Aviso",
        linhas=[texto],
        delay=10,
    )


class ModalEscolherBloco(LoggingModalMixin, discord.ui.Modal):
    """Pede o número do bloco; em seguida envia CardView (editar ou mover)."""

    numero = discord.ui.TextInput(
        label="Número do bloco (veja a lista)",
        max_length=3,
        required=True,
        placeholder="1",
    )

    def __init__(self, painel: "PainelTemplatesView", modo: str = "editar"):
        titulo = "Mover bloco nº" if modo == "mover" else "Editar bloco nº"
        super().__init__(title=titulo[:45])
        self.painel = painel
        self.modo = modo

    async def on_submit(self, interacao: discord.Interaction):
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        try:
            indice = int(self.numero.value.strip()) - 1
        except ValueError:
            await responder_erro(
                interacao,
                titulo="Número inválido",
                linhas=["Informe só o número do bloco, por exemplo `2`."],
            )
            return
        if indice < 0 or indice >= len(rascunho.blocos):
            await responder_erro(
                interacao,
                titulo="Bloco inexistente",
                linhas=[f"Use um número entre **1** e **{len(rascunho.blocos)}**."],
            )
            return

        await _enviar_card_acao_bloco(
            interacao,
            self.painel,
            indice,
            self.modo,
        )


class ModalTextoBloco(LoggingModalMixin, discord.ui.Modal):
    campo_texto = discord.ui.TextInput(
        label="Texto",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )

    def __init__(
        self,
        painel: PainelTemplatesView,
        tipo: str,
        editar_ultimo: bool = False,
    ):
        titulo = "Editar " if editar_ultimo else "Adicionar "
        titulo += "título" if tipo == "titulo" else "texto"
        super().__init__(title=titulo[:45])
        self.painel = painel
        self.tipo = tipo
        self.editar_ultimo = editar_ultimo
        if editar_ultimo:
            bloco = _bloco_em_edicao(painel)
            if bloco is not None:
                self.campo_texto.default = bloco.texto

    async def on_submit(self, interacao: discord.Interaction):
        novo = BlocoTemplate(tipo=self.tipo, texto=self.campo_texto.value.strip())
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        if self.editar_ultimo and rascunho.blocos:
            rascunho.blocos[-1] = novo
        else:
            rascunho.blocos.append(novo)
        await self.painel._atualizar_painel(interacao)


class ModalSecaoCompleta(LoggingModalMixin, discord.ui.Modal):
    texto = discord.ui.TextInput(
        label="Texto da seção",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )
    usar_icone = discord.ui.TextInput(
        label="Ícone do servidor? (sim/não)",
        max_length=3,
        required=True,
        default="sim",
    )
    url_thumb = discord.ui.TextInput(
        label="URL thumbnail (alternativa)",
        required=False,
        max_length=300,
    )
    botao_rotulo = discord.ui.TextInput(
        label="OU botão link — rótulo",
        required=False,
        max_length=80,
    )
    botao_url = discord.ui.TextInput(
        label="OU botão link — URL",
        required=False,
        max_length=300,
    )

    def __init__(self, painel: PainelTemplatesView, editar_ultimo: bool = False):
        super().__init__(title="Editar seção" if editar_ultimo else "Seção + accessory")
        self.painel = painel
        self.editar_ultimo = editar_ultimo
        if editar_ultimo:
            bloco = _bloco_em_edicao(painel)
            if bloco is not None:
                self.texto.default = bloco.texto
                self.usar_icone.default = (
                    "sim" if bloco.usar_thumbnail_servidor else "não"
                )
                self.url_thumb.default = bloco.url_thumbnail or ""
                self.botao_rotulo.default = bloco.accessory_botao_rotulo or ""
                self.botao_url.default = bloco.accessory_botao_url or ""

    async def on_submit(self, interacao: discord.Interaction):
        usar = self.usar_icone.value.strip().lower() in ("sim", "s", "yes", "y", "1")
        novo = BlocoTemplate(
            tipo="secao",
            texto=self.texto.value.strip(),
            usar_thumbnail_servidor=usar,
            url_thumbnail=(self.url_thumb.value or "").strip(),
            accessory_botao_rotulo=(self.botao_rotulo.value or "").strip(),
            accessory_botao_url=(self.botao_url.value or "").strip(),
        )
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        if self.editar_ultimo and rascunho.blocos:
            rascunho.blocos[-1] = novo
        else:
            rascunho.blocos.append(novo)
        await self.painel._atualizar_painel(interacao)


class ModalSeparador(LoggingModalMixin, discord.ui.Modal):
    espacamento = discord.ui.TextInput(
        label="Espaçamento (small ou large)",
        max_length=5,
        required=True,
        default="large",
    )

    def __init__(self, painel: PainelTemplatesView, editar_ultimo: bool = False):
        super().__init__(title="Editar separador" if editar_ultimo else "Separador")
        self.painel = painel
        self.editar_ultimo = editar_ultimo
        if editar_ultimo:
            bloco = _bloco_em_edicao(painel)
            if bloco is not None:
                self.espacamento.default = bloco.espacamento

    async def on_submit(self, interacao: discord.Interaction):
        valor = self.espacamento.value.strip().lower()
        if valor not in ("small", "large"):
            valor = "large"
        novo = BlocoTemplate(tipo="separador", espacamento=valor)
        if self.editar_ultimo:
            _aplicar_bloco_editado(self.painel, novo)
        else:
            obter_rascunho(self.painel.id_do_usuario).blocos.append(novo)
        await self.painel._atualizar_painel(interacao)


class ModalGaleria(LoggingModalMixin, discord.ui.Modal):
    urls = discord.ui.TextInput(
        label="URLs (uma por linha, até 10)",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )

    def __init__(self, painel: PainelTemplatesView, editar_ultimo: bool = False):
        super().__init__(title="Editar galeria" if editar_ultimo else "MediaGallery")
        self.painel = painel
        self.editar_ultimo = editar_ultimo
        if editar_ultimo:
            bloco = _bloco_em_edicao(painel)
            if bloco is not None:
                self.urls.default = "\n".join(bloco.urls_midia)

    async def on_submit(self, interacao: discord.Interaction):
        lista = [
            linha.strip()
            for linha in self.urls.value.splitlines()
            if linha.strip().startswith("http")
        ][:10]
        if not lista:
            await interacao.response.send_message(
                "❌ Nenhuma URL http válida.", ephemeral=True
            )
            return
        novo = BlocoTemplate(tipo="galeria", urls_midia=lista)
        if self.editar_ultimo:
            _aplicar_bloco_editado(self.painel, novo)
        else:
            obter_rascunho(self.painel.id_do_usuario).blocos.append(novo)
        await self.painel._atualizar_painel(interacao)


class ModalArquivo(LoggingModalMixin, discord.ui.Modal):
    nome = discord.ui.TextInput(
        label="Nome do arquivo (ex: relatorio.pdf)",
        max_length=100,
        required=True,
        default="arquivo.bin",
    )

    def __init__(self, painel: PainelTemplatesView, editar_ultimo: bool = False):
        super().__init__(
            title="Editar arquivo" if editar_ultimo else "File (attachment)"
        )
        self.painel = painel
        self.editar_ultimo = editar_ultimo
        if editar_ultimo:
            bloco = _bloco_em_edicao(painel)
            if bloco is not None:
                self.nome.default = bloco.nome_arquivo

    async def on_submit(self, interacao: discord.Interaction):
        novo = BlocoTemplate(tipo="arquivo", nome_arquivo=self.nome.value.strip())
        if self.editar_ultimo:
            _aplicar_bloco_editado(self.painel, novo)
        else:
            obter_rascunho(self.painel.id_do_usuario).blocos.append(novo)
        await self.painel._atualizar_painel(interacao)


class ModalBotao(LoggingModalMixin, discord.ui.Modal):
    rotulo = discord.ui.TextInput(label="Rótulo", max_length=80, required=True)
    estilo = discord.ui.TextInput(
        label="Estilo: primary/secondary/success/danger/link",
        max_length=12,
        required=True,
        default="primary",
    )
    url_ou_id = discord.ui.TextInput(
        label="URL (link) ou custom_id",
        max_length=300,
        required=True,
    )

    def __init__(self, painel: PainelTemplatesView, editar_ultimo: bool = False):
        super().__init__(title="Editar botão" if editar_ultimo else "Button")
        self.painel = painel
        self.editar_ultimo = editar_ultimo
        if editar_ultimo:
            bloco = _bloco_em_edicao(painel)
            if bloco is not None and bloco.botoes:
                rotulo, estilo, valor = bloco.botoes[0]
                self.rotulo.default = rotulo
                self.estilo.default = estilo
                self.url_ou_id.default = valor

    async def on_submit(self, interacao: discord.Interaction):
        estilo = self.estilo.value.strip().lower()
        if estilo not in ("primary", "secondary", "success", "danger", "link"):
            estilo = "secondary"
        valor = self.url_ou_id.value.strip()
        if estilo == "link" and not valor.startswith("http"):
            await interacao.response.send_message(
                "❌ Botão link precisa de URL http(s).", ephemeral=True
            )
            return
        item = (self.rotulo.value.strip(), estilo, valor)
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        if self.editar_ultimo:
            indice = _indice_alvo_edicao(self.painel)
            if (
                0 <= indice < len(rascunho.blocos)
                and rascunho.blocos[indice].tipo == "botoes"
            ):
                if rascunho.blocos[indice].botoes:
                    rascunho.blocos[indice].botoes[0] = item
                else:
                    rascunho.blocos[indice].botoes = [item]
            self.painel.indice_em_edicao = None
        elif (
            rascunho.blocos
            and rascunho.blocos[-1].tipo == "botoes"
            and not self.editar_ultimo
        ):
            if len(rascunho.blocos[-1].botoes) >= 5:
                await interacao.response.send_message(
                    "❌ Máximo de 5 botões por ActionRow.", ephemeral=True
                )
                return
            rascunho.blocos[-1].botoes.append(item)
        else:
            rascunho.blocos.append(BlocoTemplate(tipo="botoes", botoes=[item]))
        await self.painel._atualizar_painel(interacao)


class ModalSelectString(LoggingModalMixin, discord.ui.Modal):
    placeholder = discord.ui.TextInput(
        label="Placeholder (1ª opção do menu)",
        max_length=100,
        required=False,
        default="Escolha uma opção…",
    )
    label = discord.ui.TextInput(label="Label", max_length=100, required=True)
    value = discord.ui.TextInput(label="Value", max_length=100, required=True)
    descricao = discord.ui.TextInput(
        label="Description (opcional)",
        max_length=100,
        required=False,
    )

    def __init__(self, painel: PainelTemplatesView, editar_ultimo: bool = False):
        super().__init__(title="Editar select" if editar_ultimo else "SelectOption")
        self.painel = painel
        self.editar_ultimo = editar_ultimo
        if editar_ultimo:
            bloco = _bloco_em_edicao(painel)
            if bloco is not None:
                self.placeholder.default = bloco.placeholder_select
                if bloco.opcoes_select:
                    lab, val, desc = bloco.opcoes_select[0]
                    self.label.default = lab
                    self.value.default = val
                    self.descricao.default = desc

    async def on_submit(self, interacao: discord.Interaction):
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        label = self.label.value.strip()
        value = self.value.value.strip()
        desc = (self.descricao.value or "").strip()
        placeholder = (self.placeholder.value or "Escolha uma opção…").strip()
        if self.editar_ultimo:
            indice = _indice_alvo_edicao(self.painel)
            if (
                0 <= indice < len(rascunho.blocos)
                and rascunho.blocos[indice].tipo == "select_string"
            ):
                bloco = rascunho.blocos[indice]
                bloco.placeholder_select = placeholder
                if bloco.opcoes_select:
                    bloco.opcoes_select[0] = (label, value, desc)
                else:
                    bloco.opcoes_select = [(label, value, desc)]
            self.painel.indice_em_edicao = None
        elif rascunho.blocos and rascunho.blocos[-1].tipo == "select_string":
            if len(rascunho.blocos[-1].opcoes_select) >= 25:
                await interacao.response.send_message(
                    "❌ Máximo de 25 opções.", ephemeral=True
                )
                return
            rascunho.blocos[-1].opcoes_select.append((label, value, desc))
        else:
            rascunho.blocos.append(
                BlocoTemplate(
                    tipo="select_string",
                    placeholder_select=placeholder,
                    opcoes_select=[(label, value, desc)],
                )
            )
        await self.painel._atualizar_painel(interacao)


class ModalSelectEspecial(LoggingModalMixin, discord.ui.Modal, title="Select especial"):
    placeholder = discord.ui.TextInput(
        label="Placeholder",
        max_length=100,
        required=True,
        default="Selecione…",
    )

    def __init__(
        self,
        painel: PainelTemplatesView,
        tipo: str,
        editar_ultimo: bool = False,
    ):
        super().__init__()
        self.painel = painel
        self.tipo = tipo
        self.editar_ultimo = editar_ultimo
        if editar_ultimo:
            bloco = _bloco_em_edicao(painel)
            if bloco is not None:
                self.placeholder.default = bloco.placeholder_select_especial

    async def on_submit(self, interacao: discord.Interaction):
        novo = BlocoTemplate(
            tipo=self.tipo,
            placeholder_select_especial=self.placeholder.value.strip(),
        )
        if self.editar_ultimo:
            _aplicar_bloco_editado(self.painel, novo)
        else:
            obter_rascunho(self.painel.id_do_usuario).blocos.append(novo)
        await self.painel._atualizar_painel(interacao)


class ModalRodape(LoggingModalMixin, discord.ui.Modal, title="Rodapé"):
    ativo = discord.ui.TextInput(
        label="Ativar? (sim/não)", max_length=3, required=True, default="sim"
    )
    texto = discord.ui.TextInput(label="Texto extra", max_length=120, required=False)
    nome_servidor = discord.ui.TextInput(
        label="Nome do servidor? (sim/não)",
        max_length=3,
        required=True,
        default="sim",
    )
    data_hora = discord.ui.TextInput(
        label="Data e hora? (sim/não)",
        max_length=3,
        required=True,
        default="sim",
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel
        rascunho = obter_rascunho(painel.id_do_usuario)
        self.ativo.default = "sim" if rascunho.rodape_ativo else "não"
        self.texto.default = rascunho.rodape_texto or ""
        self.nome_servidor.default = "sim" if rascunho.rodape_nome_servidor else "não"
        self.data_hora.default = "sim" if rascunho.rodape_data_hora else "não"

    async def on_submit(self, interacao: discord.Interaction):
        def _sim(v: str) -> bool:
            return v.strip().lower() in ("sim", "s", "yes", "y", "1")

        rascunho = obter_rascunho(self.painel.id_do_usuario)
        rascunho.rodape_ativo = _sim(self.ativo.value)
        rascunho.rodape_texto = (self.texto.value or "").strip()
        rascunho.rodape_nome_servidor = _sim(self.nome_servidor.value)
        rascunho.rodape_data_hora = _sim(self.data_hora.value)
        await self.painel._atualizar_painel(interacao)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class TemplatesCog(commands.Cog):
    """Comandos /templates — construtor visual do Bot UI Kit."""

    grupo = app_commands.Group(
        name="templates",
        description="Construtor visual completo de Components V2",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo.command(name="abrir", description="Abre o painel interativo de templates")
    @apenas_administrador()
    async def abrir(self, interacao: discord.Interaction):
        obter_rascunho(interacao.user.id)
        painel = PainelTemplatesView(
            interacao.user.id,
            guilda=interacao.guild,
        )
        # 1) Editor (efêmero)
        await interacao.response.send_message(view=painel, ephemeral=True)
        painel.mensagem_editor = await interacao.original_response()

        # 2) Preview V2 em mensagem separada (editada a cada change)
        rascunho = obter_rascunho(interacao.user.id)
        view_preview = montar_preview(rascunho, interacao.guild)
        mensagem_preview = await interacao.followup.send(
            view=view_preview,
            ephemeral=True,
            wait=True,
        )
        painel.mensagem_preview = mensagem_preview

    @grupo.command(name="preview-dm", description="Envia o preview na sua DM")
    @apenas_administrador()
    async def preview_dm(self, interacao: discord.Interaction):
        rascunho = obter_rascunho(interacao.user.id)
        await interacao.response.defer(ephemeral=True)
        preview = montar_preview(rascunho, interacao.guild)
        try:
            await interacao.user.send(view=preview)
            enviou = True
        except (discord.Forbidden, discord.HTTPException):
            enviou = False
        await enviar_card(
            interacao,
            titulo="✅ Preview na DM" if enviou else "❌ Falha na DM",
            linhas=["Confira sua caixa de mensagens diretas."],
            cor=COR_SUCESSO if enviou else COR_ERRO,
            delay=15,
        )

    @grupo.command(name="ajuda", description="Mapa do Bot UI Kit no construtor")
    @apenas_administrador()
    async def ajuda(self, interacao: discord.Interaction):
        await enviar_card(
            interacao,
            titulo="🧩 Ajuda · Templates / Bot UI Kit",
            linhas=[
                "`/templates abrir` — painel completo.",
                "**Mensagem:** TextDisplay, Separator, Section+Thumbnail/Button,",
                "MediaGallery, File, ActionRow (Button / Select string|user|role|channel).",
                "**Modal (código):** Label, TextInput, FileUpload, RadioGroup, CheckboxGroup.",
                "**Rodapé:** texto + servidor + `15 jul de 2028 • 22:54`.",
                "Botão **Ajuda API** no painel mostra hierarquia e limites.",
                "Docs: discordpy.readthedocs.io → Bot UI Kit.",
            ],
            cor=COR_INFO,
            delay=60,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TemplatesCog(bot))
