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
)
from src.utils.permissions import apenas_administrador

# ---------------------------------------------------------------------------
# Painel de edição
# ---------------------------------------------------------------------------


def _preview_textual(
    rascunho,
    guilda: discord.Guild | None,
) -> str:
    """
    Representação leve do card final (só texto).

    Usado no painel para não estourar o limite de 40 componentes do LayoutView.
    O botão Preview V2 monta o Container real em mensagem ephemeral.
    """
    if not rascunho.blocos and not rascunho.rodape_ativo:
        return "_Vazio — adicione blocos pelos botões._"

    partes: list[str] = []
    for bloco in rascunho.blocos:
        if bloco.tipo == "titulo":
            partes.append(f"# {bloco.texto}")
        elif bloco.tipo == "texto":
            partes.append(bloco.texto)
        elif bloco.tipo == "separador":
            partes.append("─" * 12)
        elif bloco.tipo == "secao":
            extra = ""
            if bloco.usar_thumbnail_servidor:
                extra = "  _(thumb: ícone do servidor)_"
            elif bloco.url_thumbnail:
                extra = "  _(thumb: URL)_"
            elif bloco.accessory_botao_rotulo:
                extra = f"  _[btn: {bloco.accessory_botao_rotulo}]_"
            partes.append(f"{bloco.texto}{extra}")
        elif bloco.tipo == "galeria":
            partes.append(f"🖼️ Galeria ({len(bloco.urls_midia)} imagem(ns))")
        elif bloco.tipo == "arquivo":
            partes.append(f"📎 Arquivo: `{bloco.nome_arquivo or '?'}`")
        elif bloco.tipo == "botoes":
            nomes = " · ".join(f"`{r}`" for r, _, _ in bloco.botoes)
            partes.append(f"🔘 Botões: {nomes}")
        elif bloco.tipo == "select_string":
            nomes = " · ".join(lab for lab, _, _ in bloco.opcoes_select)
            partes.append(f"📋 Select: {nomes or bloco.placeholder_select}")
        elif bloco.tipo.startswith("select_"):
            partes.append(f"📋 {bloco.tipo}: {bloco.placeholder_select_especial}")
        else:
            partes.append(f"`{bloco.tipo}`")

    if rascunho.rodape_ativo:
        from src.templates.templates_modelo import montar_texto_do_rodape

        rodape = montar_texto_do_rodape(rascunho, guilda)
        if rodape:
            partes.append(rodape)

    texto = "\n\n".join(partes)
    # TextDisplay compartilha pool de ~4000 chars na mensagem
    if len(texto) > 1800:
        texto = texto[:1800] + "\n-# …preview truncado"
    return texto


class PainelTemplatesView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Painel efêmero onde a staff monta o Container bloco a bloco.

    LayoutView + Container no próprio editor (Components V2 end-to-end).
    """

    def __init__(
        self,
        id_do_usuario: int,
        guilda: discord.Guild | None = None,
    ):
        super().__init__(timeout=1200)
        self.id_do_usuario = id_do_usuario
        self.guilda = guilda
        self._reconstruir()

    def _reconstruir(self) -> None:
        """
        Monta o painel do editor.

        Importante: LayoutView tem limite de **40 componentes aninhados**.
        Por isso o preview *ao vivo* no card é textual (leve).
        O botão Preview continua abrindo o Container V2 completo (ephemeral).
        """
        self.clear_items()
        rascunho = obter_rascunho(self.id_do_usuario)

        # --- linha 1: conteúdo ---
        linha_conteudo = discord.ui.ActionRow()
        for rotulo, emoji, callback in (
            ("Título", "✏️", self._ao_clicar_titulo),
            ("Texto", "📝", self._ao_clicar_texto),
            ("Seção", "🗂️", self._ao_clicar_secao),
            ("Separador", "➖", self._ao_clicar_separador),
            ("Galeria", "🖼️", self._ao_clicar_galeria),
        ):
            botao = discord.ui.Button(
                label=rotulo, style=discord.ButtonStyle.primary, emoji=emoji
            )
            botao.callback = callback
            linha_conteudo.add_item(botao)

        # --- linha 2: mídia + interação ---
        linha_midia = discord.ui.ActionRow()
        for rotulo, emoji, callback in (
            ("Arquivo", "📎", self._ao_clicar_arquivo),
            ("Botão", "🔘", self._ao_clicar_botao),
            ("Select", "📋", self._ao_clicar_select_string),
            ("User", "👤", self._ao_clicar_select_user),
            ("Role", "🎭", self._ao_clicar_select_role),
        ):
            botao = discord.ui.Button(
                label=rotulo, style=discord.ButtonStyle.secondary, emoji=emoji
            )
            botao.callback = callback
            linha_midia.add_item(botao)

        # --- linha 3: canal + editar/remover + rodapé ---
        linha_edicao = discord.ui.ActionRow()
        for rotulo, emoji, estilo, callback in (
            (
                "Channel",
                "#️⃣",
                discord.ButtonStyle.secondary,
                self._ao_clicar_select_channel,
            ),
            (
                "Editar último",
                "✏️",
                discord.ButtonStyle.primary,
                self._ao_clicar_editar_ultimo,
            ),
            (
                "Remover último",
                "🗑️",
                discord.ButtonStyle.danger,
                self._ao_clicar_remover_ultimo,
            ),
            ("Rodapé", "📌", discord.ButtonStyle.secondary, self._ao_clicar_rodape),
            (
                "Cód. Modal",
                "🪟",
                discord.ButtonStyle.secondary,
                self._ao_clicar_codigo_modal,
            ),
        ):
            botao = discord.ui.Button(label=rotulo, style=estilo, emoji=emoji)
            botao.callback = callback
            linha_edicao.add_item(botao)

        # --- linha 4: cor (Select sozinho) ---
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

        # --- linha 5: ações finais ---
        linha_acoes = discord.ui.ActionRow()
        for rotulo, emoji, estilo, callback in (
            ("Preview V2", "👁️", discord.ButtonStyle.success, self._ao_clicar_preview),
            ("Código msg", "📄", discord.ButtonStyle.danger, self._ao_clicar_codigo),
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

        # Preview textual (não conta dezenas de componentes — evita limite 40)
        preview_texto = _preview_textual(rascunho, self.guilda)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 🧩 Construtor Bot UI Kit (V2)\n"
                    "-# Preview ao vivo abaixo (texto). "
                    "**Preview V2** abre o Container completo (ephemeral)."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    f"### Blocos ({len(rascunho.blocos)})\n"
                    f"{resumo_dos_blocos(rascunho)}\n\n"
                    f"**Cor:** `{rascunho.cor_nome}` · **Rodapé:** `{status_rodape}`"
                    + (f"\n{texto_rodape}" if texto_rodape else "")
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(f"### Preview ao vivo\n{preview_texto}"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_conteudo,
                linha_midia,
                linha_edicao,
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

    async def _atualizar_painel(self, interacao: discord.Interaction) -> None:
        if interacao.guild is not None:
            self.guilda = interacao.guild
        self._reconstruir()
        if interacao.response.is_done():
            await interacao.edit_original_response(view=self)
        else:
            await interacao.response.edit_message(view=self)

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

        ultimo = rascunho.blocos[-1]
        if ultimo.tipo in ("titulo", "texto"):
            await interacao.response.send_modal(
                ModalTextoBloco(self, ultimo.tipo, editar_ultimo=True)
            )
        elif ultimo.tipo == "secao":
            await interacao.response.send_modal(
                ModalSecaoCompleta(self, editar_ultimo=True)
            )
        elif ultimo.tipo == "separador":
            await interacao.response.send_modal(
                ModalSeparador(self, editar_ultimo=True)
            )
        elif ultimo.tipo == "galeria":
            await interacao.response.send_modal(ModalGaleria(self, editar_ultimo=True))
        elif ultimo.tipo == "arquivo":
            await interacao.response.send_modal(ModalArquivo(self, editar_ultimo=True))
        elif ultimo.tipo == "botoes":
            await interacao.response.send_modal(ModalBotao(self, editar_ultimo=True))
        elif ultimo.tipo == "select_string":
            await interacao.response.send_modal(
                ModalSelectString(self, editar_ultimo=True)
            )
        elif ultimo.tipo.startswith("select_"):
            # Selects especiais não têm modal rico — só placeholder
            await interacao.response.send_modal(
                ModalSelectEspecial(self, ultimo.tipo, editar_ultimo=True)
            )
        else:
            await interacao.response.send_message(
                f"❌ Tipo `{ultimo.tipo}` ainda sem editor dedicado.",
                ephemeral=True,
            )

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
        # Zera de verdade: 0 blocos, sem recriar defaults
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
            rascunho = obter_rascunho(painel.id_do_usuario)
            if rascunho.blocos:
                self.campo_texto.default = rascunho.blocos[-1].texto

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
            bloco = obter_rascunho(painel.id_do_usuario).blocos[-1]
            self.texto.default = bloco.texto
            self.usar_icone.default = "sim" if bloco.usar_thumbnail_servidor else "não"
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
            self.espacamento.default = (
                obter_rascunho(painel.id_do_usuario).blocos[-1].espacamento
            )

    async def on_submit(self, interacao: discord.Interaction):
        valor = self.espacamento.value.strip().lower()
        if valor not in ("small", "large"):
            valor = "large"
        novo = BlocoTemplate(tipo="separador", espacamento=valor)
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        if self.editar_ultimo and rascunho.blocos:
            rascunho.blocos[-1] = novo
        else:
            rascunho.blocos.append(novo)
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
            bloco = obter_rascunho(painel.id_do_usuario).blocos[-1]
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
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        if self.editar_ultimo and rascunho.blocos:
            rascunho.blocos[-1] = novo
        else:
            rascunho.blocos.append(novo)
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
            self.nome.default = (
                obter_rascunho(painel.id_do_usuario).blocos[-1].nome_arquivo
            )

    async def on_submit(self, interacao: discord.Interaction):
        novo = BlocoTemplate(tipo="arquivo", nome_arquivo=self.nome.value.strip())
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        if self.editar_ultimo and rascunho.blocos:
            rascunho.blocos[-1] = novo
        else:
            rascunho.blocos.append(novo)
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
            bloco = obter_rascunho(painel.id_do_usuario).blocos[-1]
            if bloco.botoes:
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
        if (
            self.editar_ultimo
            and rascunho.blocos
            and rascunho.blocos[-1].tipo == "botoes"
        ):
            # Substitui o primeiro botão do bloco (ou o bloco inteiro se só havia 1)
            if rascunho.blocos[-1].botoes:
                rascunho.blocos[-1].botoes[0] = item
            else:
                rascunho.blocos[-1].botoes = [item]
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
            bloco = obter_rascunho(painel.id_do_usuario).blocos[-1]
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
        if (
            self.editar_ultimo
            and rascunho.blocos
            and rascunho.blocos[-1].tipo == "select_string"
        ):
            bloco = rascunho.blocos[-1]
            bloco.placeholder_select = placeholder
            if bloco.opcoes_select:
                bloco.opcoes_select[0] = (label, value, desc)
            else:
                bloco.opcoes_select = [(label, value, desc)]
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
            self.placeholder.default = (
                obter_rascunho(painel.id_do_usuario)
                .blocos[-1]
                .placeholder_select_especial
            )

    async def on_submit(self, interacao: discord.Interaction):
        novo = BlocoTemplate(
            tipo=self.tipo,
            placeholder_select_especial=self.placeholder.value.strip(),
        )
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        if self.editar_ultimo and rascunho.blocos:
            rascunho.blocos[-1] = novo
        else:
            rascunho.blocos.append(novo)
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
        await interacao.response.send_message(
            view=PainelTemplatesView(interacao.user.id, guilda=interacao.guild),
            ephemeral=True,
        )

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
