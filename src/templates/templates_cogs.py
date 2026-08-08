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
    TEM_CHECKBOX_GROUP,
    TEM_FILE,
    TEM_FILE_UPLOAD,
    TEM_LABEL,
    TEM_RADIO_GROUP,
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
        self.clear_items()
        rascunho = obter_rascunho(self.id_do_usuario)

        linha_conteudo = discord.ui.ActionRow()
        for rotulo, emoji, callback in (
            ("Título", "✏️", self._ao_clicar_titulo),
            ("Texto", "📝", self._ao_clicar_texto),
            ("Seção", "🗂️", self._ao_clicar_secao),
            ("Separador", "➖", self._ao_clicar_separador),
        ):
            botao = discord.ui.Button(
                label=rotulo, style=discord.ButtonStyle.primary, emoji=emoji
            )
            botao.callback = callback
            linha_conteudo.add_item(botao)

        linha_midia = discord.ui.ActionRow()
        for rotulo, emoji, callback in (
            ("Galeria", "🖼️", self._ao_clicar_galeria),
            ("Arquivo", "📎", self._ao_clicar_arquivo),
            ("Botão", "🔘", self._ao_clicar_botao),
            ("Select texto", "📋", self._ao_clicar_select_string),
        ):
            botao = discord.ui.Button(
                label=rotulo, style=discord.ButtonStyle.secondary, emoji=emoji
            )
            botao.callback = callback
            linha_midia.add_item(botao)

        linha_selects = discord.ui.ActionRow()
        for rotulo, emoji, callback in (
            ("UserSelect", "👤", self._ao_clicar_select_user),
            ("RoleSelect", "🎭", self._ao_clicar_select_role),
            ("ChannelSelect", "#️⃣", self._ao_clicar_select_channel),
            ("Remover último", "🗑️", self._ao_clicar_remover_ultimo),
        ):
            botao = discord.ui.Button(
                label=rotulo, style=discord.ButtonStyle.secondary, emoji=emoji
            )
            botao.callback = callback
            linha_selects.add_item(botao)

        linha_extras = discord.ui.ActionRow()
        botao_rodape = discord.ui.Button(
            label="Rodapé", style=discord.ButtonStyle.secondary, emoji="📌"
        )
        botao_rodape.callback = self._ao_clicar_rodape
        linha_extras.add_item(botao_rodape)

        botao_modal = discord.ui.Button(
            label="Código Modal", style=discord.ButtonStyle.secondary, emoji="🪟"
        )
        botao_modal.callback = self._ao_clicar_codigo_modal
        linha_extras.add_item(botao_modal)

        seletor_cor = discord.ui.Select(
            placeholder="Cor do Container…",
            options=[
                discord.SelectOption(label=nome.title(), value=nome)
                for nome in mapa_de_cores().keys()
            ],
        )
        seletor_cor.callback = self._ao_escolher_cor
        linha_extras.add_item(seletor_cor)

        linha_acoes = discord.ui.ActionRow()
        for rotulo, emoji, estilo, callback in (
            ("Preview", "👁️", discord.ButtonStyle.success, self._ao_clicar_preview),
            ("Código msg", "📄", discord.ButtonStyle.danger, self._ao_clicar_codigo),
            ("Resetar", "♻️", discord.ButtonStyle.secondary, self._ao_clicar_resetar),
            ("Ajuda API", "❓", discord.ButtonStyle.primary, self._ao_clicar_ajuda_api),
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
        api_detectada = (
            f"File={TEM_FILE} · Label={TEM_LABEL} · FileUpload={TEM_FILE_UPLOAD} · "
            f"Radio={TEM_RADIO_GROUP} · CheckboxGroup={TEM_CHECKBOX_GROUP}"
        )

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# 🧩 Construtor Bot UI Kit (V2)"),
                discord.ui.TextDisplay(
                    "Monte o **Container** bloco a bloco.\n"
                    "**Preview** mostra o resultado · **Código msg** gera a mensagem · "
                    "**Código Modal** gera Label/FileUpload/Radio/Checkbox."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    f"### Blocos ({len(rascunho.blocos)})\n"
                    f"{resumo_dos_blocos(rascunho)}\n\n"
                    f"### Cor\n`{rascunho.cor_nome}`\n\n"
                    f"### Rodapé\n`{status_rodape}`"
                    + (f"\n{texto_rodape}" if texto_rodape else "")
                    + f"\n\n### API detectada\n-# {api_detectada}"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_conteudo,
                linha_midia,
                linha_selects,
                linha_extras,
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
        obter_rascunho(self.id_do_usuario)
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

    def __init__(self, painel: PainelTemplatesView, tipo: str):
        super().__init__(
            title="Adicionar título" if tipo == "titulo" else "Adicionar texto"
        )
        self.painel = painel
        self.tipo = tipo

    async def on_submit(self, interacao: discord.Interaction):
        obter_rascunho(self.painel.id_do_usuario).blocos.append(
            BlocoTemplate(tipo=self.tipo, texto=self.campo_texto.value.strip())
        )
        await self.painel._atualizar_painel(interacao)


class ModalSecaoCompleta(
    LoggingModalMixin, discord.ui.Modal, title="Seção + accessory"
):
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

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        usar = self.usar_icone.value.strip().lower() in ("sim", "s", "yes", "y", "1")
        obter_rascunho(self.painel.id_do_usuario).blocos.append(
            BlocoTemplate(
                tipo="secao",
                texto=self.texto.value.strip(),
                usar_thumbnail_servidor=usar,
                url_thumbnail=(self.url_thumb.value or "").strip(),
                accessory_botao_rotulo=(self.botao_rotulo.value or "").strip(),
                accessory_botao_url=(self.botao_url.value or "").strip(),
            )
        )
        await self.painel._atualizar_painel(interacao)


class ModalSeparador(LoggingModalMixin, discord.ui.Modal, title="Separador"):
    espacamento = discord.ui.TextInput(
        label="Espaçamento (small ou large)",
        max_length=5,
        required=True,
        default="large",
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        valor = self.espacamento.value.strip().lower()
        if valor not in ("small", "large"):
            valor = "large"
        obter_rascunho(self.painel.id_do_usuario).blocos.append(
            BlocoTemplate(tipo="separador", espacamento=valor)
        )
        await self.painel._atualizar_painel(interacao)


class ModalGaleria(LoggingModalMixin, discord.ui.Modal, title="MediaGallery"):
    urls = discord.ui.TextInput(
        label="URLs (uma por linha, até 10)",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

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
        obter_rascunho(self.painel.id_do_usuario).blocos.append(
            BlocoTemplate(tipo="galeria", urls_midia=lista)
        )
        await self.painel._atualizar_painel(interacao)


class ModalArquivo(LoggingModalMixin, discord.ui.Modal, title="File (attachment)"):
    nome = discord.ui.TextInput(
        label="Nome do arquivo (ex: relatorio.pdf)",
        max_length=100,
        required=True,
        default="arquivo.bin",
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        obter_rascunho(self.painel.id_do_usuario).blocos.append(
            BlocoTemplate(tipo="arquivo", nome_arquivo=self.nome.value.strip())
        )
        await self.painel._atualizar_painel(interacao)


class ModalBotao(LoggingModalMixin, discord.ui.Modal, title="Button"):
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

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

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
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        if rascunho.blocos and rascunho.blocos[-1].tipo == "botoes":
            if len(rascunho.blocos[-1].botoes) >= 5:
                await interacao.response.send_message(
                    "❌ Máximo de 5 botões por ActionRow.", ephemeral=True
                )
                return
            rascunho.blocos[-1].botoes.append(
                (self.rotulo.value.strip(), estilo, valor)
            )
        else:
            rascunho.blocos.append(
                BlocoTemplate(
                    tipo="botoes",
                    botoes=[(self.rotulo.value.strip(), estilo, valor)],
                )
            )
        await self.painel._atualizar_painel(interacao)


class ModalSelectString(LoggingModalMixin, discord.ui.Modal, title="SelectOption"):
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

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        rascunho = obter_rascunho(self.painel.id_do_usuario)
        label = self.label.value.strip()
        value = self.value.value.strip()
        desc = (self.descricao.value or "").strip()
        placeholder = (self.placeholder.value or "Escolha uma opção…").strip()
        if rascunho.blocos and rascunho.blocos[-1].tipo == "select_string":
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
