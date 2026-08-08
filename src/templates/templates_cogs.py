"""Cog /templates — construtor visual de cards Components V2.

Blocos suportados:
  - título / texto (TextDisplay)
  - separador (small | large)
  - seção (Section + Thumbnail opcional: ícone do servidor ou URL)
  - galeria (MediaGallery)
  - botões de link (ActionRow)
  - select / dropdown (Select + SelectOption)
  - rodapé (-# texto · data/hora · nome do servidor)

Fluxo: /templates abrir → editar → Preview → Gerar código.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from datetime import datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

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
# Modelo do rascunho
# ---------------------------------------------------------------------------

MESES_PT = (
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
)


def formatar_data_hora_rodape(momento: datetime | None = None) -> str:
    """Ex.: 15 jul de 2028 • 22:54"""
    momento = momento or datetime.now()
    mes = MESES_PT[momento.month - 1]
    return f"{momento.day} {mes} de {momento.year} • {momento.strftime('%H:%M')}"


@dataclass
class BlocoTemplate:
    """Um bloco na ordem de montagem do Container."""

    tipo: str
    # texto / secao / rodape
    texto: str = ""
    # separador: "small" | "large"
    espacamento: str = "large"
    # secao: thumbnail
    usar_thumbnail_servidor: bool = False
    url_thumbnail: str = ""
    # galeria
    urls_midia: list[str] = field(default_factory=list)
    # botoes link: (rótulo, url)
    botoes: list[tuple[str, str]] = field(default_factory=list)
    # select
    placeholder_select: str = "Escolha uma opção…"
    opcoes_select: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (label, value, description)


@dataclass
class RascunhoTemplate:
    cor_nome: str = "info"
    blocos: list[BlocoTemplate] = field(default_factory=list)
    # rodapé global (sempre no fim do container, se ativo)
    rodape_ativo: bool = False
    rodape_texto: str = ""
    rodape_nome_servidor: bool = True
    rodape_data_hora: bool = True

    def __post_init__(self):
        if not self.blocos:
            self.blocos = [
                BlocoTemplate(tipo="titulo", texto="Título do card"),
                BlocoTemplate(
                    tipo="texto",
                    texto="Primeira linha do conteúdo.",
                ),
            ]

    @property
    def cor(self) -> discord.Color:
        mapa = {
            "info": discord.Color.blurple(),
            "sucesso": discord.Color.green(),
            "aviso": discord.Color.orange(),
            "erro": discord.Color.red(),
            "escuro": discord.Color.dark_red(),
            "teal": discord.Color.dark_teal(),
            "ouro": discord.Color.gold(),
        }
        return mapa.get(self.cor_nome, discord.Color.blurple())


_rascunhos: dict[int, RascunhoTemplate] = {}


def obter_rascunho(usuario_id: int) -> RascunhoTemplate:
    if usuario_id not in _rascunhos:
        _rascunhos[usuario_id] = RascunhoTemplate()
    return _rascunhos[usuario_id]


def limpar_rascunho(usuario_id: int) -> None:
    _rascunhos.pop(usuario_id, None)


def _url_icone_guilda(guilda: discord.Guild | None) -> str | None:
    if guilda is None or guilda.icon is None:
        return None
    return guilda.icon.url


def _montar_texto_rodape(
    rascunho: RascunhoTemplate,
    guilda: discord.Guild | None,
) -> str:
    partes: list[str] = []
    if rascunho.rodape_texto.strip():
        partes.append(rascunho.rodape_texto.strip())
    if rascunho.rodape_nome_servidor and guilda is not None:
        partes.append(guilda.name)
    if rascunho.rodape_data_hora:
        partes.append(formatar_data_hora_rodape())
    if not partes:
        return ""
    return "-# " + " • ".join(partes)


def montar_componentes_container(
    rascunho: RascunhoTemplate,
    guilda: discord.Guild | None = None,
) -> list[Any]:
    """Converte o rascunho na lista de itens do Container (Components V2)."""
    componentes: list[Any] = []
    icone_servidor = _url_icone_guilda(guilda)

    for bloco in rascunho.blocos:
        if bloco.tipo == "titulo":
            componentes.append(discord.ui.TextDisplay(f"# {bloco.texto}"))

        elif bloco.tipo == "texto":
            if bloco.texto.strip():
                componentes.append(discord.ui.TextDisplay(bloco.texto))

        elif bloco.tipo == "separador":
            spacing = (
                discord.SeparatorSpacing.small
                if bloco.espacamento == "small"
                else discord.SeparatorSpacing.large
            )
            componentes.append(discord.ui.Separator(spacing=spacing))

        elif bloco.tipo == "secao":
            url_thumb = None
            if bloco.usar_thumbnail_servidor and icone_servidor:
                url_thumb = icone_servidor
            elif bloco.url_thumbnail.strip().startswith("http"):
                url_thumb = bloco.url_thumbnail.strip()

            if url_thumb:
                componentes.append(
                    discord.ui.Section(
                        bloco.texto or "—",
                        accessory=discord.ui.Thumbnail(url_thumb),
                    )
                )
            else:
                componentes.append(discord.ui.TextDisplay(bloco.texto or "—"))

        elif bloco.tipo == "galeria":
            urls = [u.strip() for u in bloco.urls_midia if u.strip().startswith("http")]
            if urls:
                itens = [discord.MediaGalleryItem(url) for url in urls[:10]]
                componentes.append(discord.ui.MediaGallery(*itens))

        elif bloco.tipo == "botoes":
            if bloco.botoes:
                linha = discord.ui.ActionRow()
                for rotulo, url in bloco.botoes[:5]:
                    linha.add_item(
                        discord.ui.Button(
                            label=rotulo[:80],
                            style=discord.ButtonStyle.link,
                            url=url,
                        )
                    )
                componentes.append(linha)

        elif bloco.tipo == "select":
            if bloco.opcoes_select:
                opcoes = [
                    discord.SelectOption(
                        label=label[:100],
                        value=value[:100],
                        description=(desc[:100] if desc else None),
                    )
                    for label, value, desc in bloco.opcoes_select[:25]
                ]
                linha = discord.ui.ActionRow()
                select = discord.ui.Select(
                    placeholder=bloco.placeholder_select[:150],
                    options=opcoes,
                    min_values=1,
                    max_values=1,
                )

                async def _noop(interacao: discord.Interaction):
                    await interacao.response.defer()

                select.callback = _noop
                linha.add_item(select)
                componentes.append(linha)

    if rascunho.rodape_ativo:
        texto_rodape = _montar_texto_rodape(rascunho, guilda)
        if texto_rodape:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
            )
            componentes.append(discord.ui.TextDisplay(texto_rodape))

    if not componentes:
        componentes.append(discord.ui.TextDisplay("_Rascunho vazio._"))

    return componentes


def montar_preview(
    rascunho: RascunhoTemplate,
    guilda: discord.Guild | None = None,
) -> discord.ui.LayoutView:
    componentes = montar_componentes_container(rascunho, guilda)
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*componentes, accent_color=rascunho.cor))
    return view


def gerar_codigo_python(
    rascunho: RascunhoTemplate,
    guilda: discord.Guild | None = None,
) -> str:
    """Gera snippet Python legível no padrão do projeto."""
    mapa_cor = {
        "info": "discord.Color.blurple()",
        "sucesso": "discord.Color.green()",
        "aviso": "discord.Color.orange()",
        "erro": "discord.Color.red()",
        "escuro": "discord.Color.dark_red()",
        "teal": "discord.Color.dark_teal()",
        "ouro": "discord.Color.gold()",
    }
    cor_expr = mapa_cor.get(rascunho.cor_nome, "discord.Color.blurple()")

    linhas_codigo: list[str] = [
        "import discord",
        "from datetime import datetime",
        "",
        "componentes = []",
        "",
    ]

    for indice, bloco in enumerate(rascunho.blocos):
        linhas_codigo.append(f"# --- bloco {indice + 1}: {bloco.tipo} ---")
        if bloco.tipo == "titulo":
            titulo_fmt = f"# {bloco.texto}"
            linhas_codigo.append(
                f"componentes.append(discord.ui.TextDisplay({titulo_fmt!r}))"
            )
        elif bloco.tipo == "texto":
            linhas_codigo.append(
                f"componentes.append(discord.ui.TextDisplay({bloco.texto!r}))"
            )
        elif bloco.tipo == "separador":
            spacing = (
                "discord.SeparatorSpacing.small"
                if bloco.espacamento == "small"
                else "discord.SeparatorSpacing.large"
            )
            linhas_codigo.append(
                f"componentes.append(discord.ui.Separator(spacing={spacing}))"
            )
        elif bloco.tipo == "secao":
            if bloco.usar_thumbnail_servidor:
                linhas_codigo.extend(
                    [
                        "url_thumb = guilda.icon.url if guilda and guilda.icon else None",
                        "if url_thumb:",
                        f"    componentes.append(discord.ui.Section({bloco.texto!r}, accessory=discord.ui.Thumbnail(url_thumb)))",
                        "else:",
                        f"    componentes.append(discord.ui.TextDisplay({bloco.texto!r}))",
                    ]
                )
            elif bloco.url_thumbnail.strip():
                linhas_codigo.append(
                    f"componentes.append(discord.ui.Section({bloco.texto!r}, "
                    f"accessory=discord.ui.Thumbnail({bloco.url_thumbnail.strip()!r})))"
                )
            else:
                linhas_codigo.append(
                    f"componentes.append(discord.ui.TextDisplay({bloco.texto!r}))"
                )
        elif bloco.tipo == "galeria":
            urls_repr = repr([u for u in bloco.urls_midia if u.startswith("http")])
            linhas_codigo.extend(
                [
                    f"urls_midia = {urls_repr}",
                    "itens = [discord.MediaGalleryItem(url) for url in urls_midia]",
                    "componentes.append(discord.ui.MediaGallery(*itens))",
                ]
            )
        elif bloco.tipo == "botoes":
            linhas_codigo.append("linha_botoes = discord.ui.ActionRow()")
            for rotulo, url in bloco.botoes:
                linhas_codigo.append(
                    "linha_botoes.add_item(discord.ui.Button("
                    f"label={rotulo!r}, style=discord.ButtonStyle.link, url={url!r}))"
                )
            linhas_codigo.append("componentes.append(linha_botoes)")
        elif bloco.tipo == "select":
            linhas_codigo.append("opcoes = [")
            for label, value, desc in bloco.opcoes_select:
                linhas_codigo.append(
                    f"    discord.SelectOption(label={label!r}, value={value!r}, "
                    f"description={desc!r}),"
                )
            linhas_codigo.extend(
                [
                    "]",
                    "linha_select = discord.ui.ActionRow()",
                    "select = discord.ui.Select(",
                    f"    placeholder={bloco.placeholder_select!r},",
                    "    options=opcoes,",
                    ")",
                    "# select.callback = seu_callback",
                    "linha_select.add_item(select)",
                    "componentes.append(linha_select)",
                ]
            )
        linhas_codigo.append("")

    if rascunho.rodape_ativo:
        linhas_codigo.extend(
            [
                "# --- rodapé ---",
                "partes_rodape = []",
            ]
        )
        if rascunho.rodape_texto.strip():
            linhas_codigo.append(
                f"partes_rodape.append({rascunho.rodape_texto.strip()!r})"
            )
        if rascunho.rodape_nome_servidor:
            linhas_codigo.append(
                "if guilda is not None:\n    partes_rodape.append(guilda.name)"
            )
        if rascunho.rodape_data_hora:
            linhas_codigo.extend(
                [
                    "agora = datetime.now()",
                    "meses = ('jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez')",
                    (
                        "partes_rodape.append("
                        'f"{agora.day} {meses[agora.month-1]} de {agora.year} • '
                        "{agora.strftime('%H:%M')}\")"
                    ),
                ]
            )
        linhas_codigo.extend(
            [
                "componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))",
                'componentes.append(discord.ui.TextDisplay("-# " + " • ".join(partes_rodape)))',
                "",
            ]
        )

    linhas_codigo.extend(
        [
            "view = discord.ui.LayoutView(timeout=None)",
            f"view.add_item(discord.ui.Container(*componentes, accent_color={cor_expr}))",
            "# await canal.send(view=view)",
            "# await membro.send(view=view)",
        ]
    )
    return "\n".join(linhas_codigo)


def _resumo_blocos(rascunho: RascunhoTemplate) -> str:
    if not rascunho.blocos:
        return "_nenhum bloco_"
    linhas = []
    for i, bloco in enumerate(rascunho.blocos, 1):
        extra = ""
        if bloco.tipo in ("titulo", "texto", "secao") and bloco.texto:
            extra = f" — `{bloco.texto[:40]}`"
        elif bloco.tipo == "separador":
            extra = f" ({bloco.espacamento})"
        elif bloco.tipo == "galeria":
            extra = f" ({len(bloco.urls_midia)} img)"
        elif bloco.tipo == "botoes":
            extra = f" ({len(bloco.botoes)} btn)"
        elif bloco.tipo == "select":
            extra = f" ({len(bloco.opcoes_select)} opc)"
        linhas.append(f"`{i}.` **{bloco.tipo}**{extra}")
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Painel de edição
# ---------------------------------------------------------------------------


class PainelTemplatesView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        usuario_id: int,
        guilda: discord.Guild | None = None,
    ):
        super().__init__(timeout=900)
        self.usuario_id = usuario_id
        self.guilda = guilda
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        rascunho = obter_rascunho(self.usuario_id)

        # --- linha 1: conteúdo ---
        row1 = discord.ui.ActionRow()
        for label, emoji, cb in (
            ("Título", "✏️", self._cb_titulo),
            ("Texto", "📝", self._cb_texto),
            ("Seção+thumb", "🖼️", self._cb_secao),
            ("Separador", "➖", self._cb_separador),
        ):
            botao = discord.ui.Button(
                label=label, style=discord.ButtonStyle.primary, emoji=emoji
            )
            botao.callback = cb
            row1.add_item(botao)

        # --- linha 2: mídia / interação ---
        row2 = discord.ui.ActionRow()
        for label, emoji, estilo, cb in (
            ("Galeria", "🖼️", discord.ButtonStyle.secondary, self._cb_galeria),
            ("Botão link", "🔗", discord.ButtonStyle.secondary, self._cb_botao),
            ("Select", "📋", discord.ButtonStyle.secondary, self._cb_select),
            (
                "Remover último",
                "🗑️",
                discord.ButtonStyle.danger,
                self._cb_remover_ultimo,
            ),
        ):
            botao = discord.ui.Button(label=label, style=estilo, emoji=emoji)
            botao.callback = cb
            row2.add_item(botao)

        # --- linha 3: rodapé + cor ---
        row3 = discord.ui.ActionRow()
        b_rodape = discord.ui.Button(
            label="Rodapé",
            style=discord.ButtonStyle.secondary,
            emoji="📌",
        )
        b_rodape.callback = self._cb_rodape
        row3.add_item(b_rodape)

        select_cor = discord.ui.Select(
            placeholder="Cor do container…",
            options=[
                discord.SelectOption(label="Info (azul)", value="info"),
                discord.SelectOption(label="Sucesso (verde)", value="sucesso"),
                discord.SelectOption(label="Aviso (laranja)", value="aviso"),
                discord.SelectOption(label="Erro (vermelho)", value="erro"),
                discord.SelectOption(label="Escuro / punição", value="escuro"),
                discord.SelectOption(label="Teal", value="teal"),
                discord.SelectOption(label="Ouro", value="ouro"),
            ],
        )
        select_cor.callback = self._cb_cor
        row3.add_item(select_cor)

        # --- linha 4: ações finais ---
        row4 = discord.ui.ActionRow()
        for label, emoji, estilo, cb in (
            ("Preview", "👁️", discord.ButtonStyle.success, self._cb_preview),
            ("Código", "📄", discord.ButtonStyle.danger, self._cb_codigo),
            ("Resetar", "♻️", discord.ButtonStyle.secondary, self._cb_reset),
        ):
            botao = discord.ui.Button(label=label, style=estilo, emoji=emoji)
            botao.callback = cb
            row4.add_item(botao)

        rodape_status = "ligado" if rascunho.rodape_ativo else "desligado"
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# 🧩 Construtor de Templates V2"),
                discord.ui.TextDisplay(
                    "Monte o **Container** bloco a bloco. "
                    "Use **Preview** para ver o resultado e **Código** para copiar."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    f"### Blocos ({len(rascunho.blocos)})\n{_resumo_blocos(rascunho)}\n\n"
                    f"### Cor\n`{rascunho.cor_nome}`\n\n"
                    f"### Rodapé\n`{rodape_status}`"
                    + (
                        f" · `{_montar_texto_rodape(rascunho, self.guilda)}`"
                        if rascunho.rodape_ativo
                        else ""
                    )
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                row1,
                row2,
                row3,
                row4,
                accent_color=discord.Color.dark_teal(),
            )
        )

    async def _garantir_dono(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.usuario_id:
            await interacao.response.send_message(
                "❌ Este painel não é seu.", ephemeral=True
            )
            return False
        return True

    async def _atualizar(self, interacao: discord.Interaction):
        if interacao.guild is not None:
            self.guilda = interacao.guild
        self._rebuild()
        if interacao.response.is_done():
            await interacao.edit_original_response(view=self)
        else:
            await interacao.response.edit_message(view=self)

    # ---- callbacks conteúdo ----

    async def _cb_titulo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalTextoBloco(self, "titulo"))

    async def _cb_texto(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalTextoBloco(self, "texto"))

    async def _cb_secao(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalSecao(self))

    async def _cb_separador(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        # alterna small/large via select rápido em modal simples
        await interacao.response.send_modal(ModalSeparador(self))

    async def _cb_galeria(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalGaleria(self))

    async def _cb_botao(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalBotaoLink(self))

    async def _cb_select(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalSelectOpcao(self))

    async def _cb_rodape(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalRodape(self))

    async def _cb_remover_ultimo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        rascunho = obter_rascunho(self.usuario_id)
        if rascunho.blocos:
            rascunho.blocos.pop()
        await self._atualizar(interacao)

    async def _cb_cor(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        valores = interacao.data.get("values") if interacao.data else None
        if valores:
            obter_rascunho(self.usuario_id).cor_nome = valores[0]
        await self._atualizar(interacao)

    async def _cb_preview(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        guilda = interacao.guild or self.guilda
        preview = montar_preview(obter_rascunho(self.usuario_id), guilda)
        await interacao.response.send_message(view=preview, ephemeral=True)

    async def _cb_codigo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        guilda = interacao.guild or self.guilda
        codigo = gerar_codigo_python(obter_rascunho(self.usuario_id), guilda)
        if len(codigo) > 1900:
            codigo = codigo[:1900] + "\n# …truncado"
        await interacao.response.send_message(
            content=f"```python\n{codigo}\n```",
            ephemeral=True,
        )

    async def _cb_reset(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        limpar_rascunho(self.usuario_id)
        obter_rascunho(self.usuario_id)
        await self._atualizar(interacao)


# ---------------------------------------------------------------------------
# Modais
# ---------------------------------------------------------------------------


class ModalTextoBloco(LoggingModalMixin, discord.ui.Modal):
    campo = discord.ui.TextInput(
        label="Texto",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )

    def __init__(self, painel: PainelTemplatesView, tipo: str):
        titulo = "Adicionar título" if tipo == "titulo" else "Adicionar texto"
        super().__init__(title=titulo)
        self.painel = painel
        self.tipo = tipo

    async def on_submit(self, interacao: discord.Interaction):
        obter_rascunho(self.painel.usuario_id).blocos.append(
            BlocoTemplate(tipo=self.tipo, texto=self.campo.value.strip())
        )
        await self.painel._atualizar(interacao)


class ModalSecao(LoggingModalMixin, discord.ui.Modal, title="Seção + Thumbnail"):
    texto = discord.ui.TextInput(
        label="Texto da seção",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )
    usar_icone = discord.ui.TextInput(
        label="Usar ícone do servidor? (sim/não)",
        max_length=3,
        required=True,
        default="sim",
    )
    url_thumb = discord.ui.TextInput(
        label="URL thumbnail (se não usar ícone)",
        required=False,
        max_length=300,
        placeholder="https://…",
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        usar = self.usar_icone.value.strip().lower() in ("sim", "s", "yes", "y", "1")
        obter_rascunho(self.painel.usuario_id).blocos.append(
            BlocoTemplate(
                tipo="secao",
                texto=self.texto.value.strip(),
                usar_thumbnail_servidor=usar,
                url_thumbnail=(self.url_thumb.value or "").strip(),
            )
        )
        await self.painel._atualizar(interacao)


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
        obter_rascunho(self.painel.usuario_id).blocos.append(
            BlocoTemplate(tipo="separador", espacamento=valor)
        )
        await self.painel._atualizar(interacao)


class ModalGaleria(LoggingModalMixin, discord.ui.Modal, title="MediaGallery"):
    urls = discord.ui.TextInput(
        label="URLs das imagens (uma por linha, até 10)",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
        placeholder="https://cdn.discordapp.com/…",
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
        obter_rascunho(self.painel.usuario_id).blocos.append(
            BlocoTemplate(tipo="galeria", urls_midia=lista)
        )
        await self.painel._atualizar(interacao)


class ModalBotaoLink(LoggingModalMixin, discord.ui.Modal, title="Botão de link"):
    rotulo = discord.ui.TextInput(label="Rótulo", max_length=80, required=True)
    url = discord.ui.TextInput(
        label="URL (https://…)",
        max_length=300,
        required=True,
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        url = self.url.value.strip()
        if not url.startswith("http"):
            await interacao.response.send_message(
                "❌ URL precisa começar com http:// ou https://",
                ephemeral=True,
            )
            return
        rascunho = obter_rascunho(self.painel.usuario_id)
        # Agrupa no último bloco de botões, se existir
        if rascunho.blocos and rascunho.blocos[-1].tipo == "botoes":
            if len(rascunho.blocos[-1].botoes) >= 5:
                await interacao.response.send_message(
                    "❌ Máximo de 5 botões por linha.", ephemeral=True
                )
                return
            rascunho.blocos[-1].botoes.append((self.rotulo.value.strip(), url))
        else:
            rascunho.blocos.append(
                BlocoTemplate(
                    tipo="botoes",
                    botoes=[(self.rotulo.value.strip(), url)],
                )
            )
        await self.painel._atualizar(interacao)


class ModalSelectOpcao(LoggingModalMixin, discord.ui.Modal, title="Opção do Select"):
    placeholder = discord.ui.TextInput(
        label="Placeholder do menu (só na 1ª opção)",
        max_length=100,
        required=False,
        default="Escolha uma opção…",
    )
    label = discord.ui.TextInput(label="Label da opção", max_length=100, required=True)
    value = discord.ui.TextInput(
        label="Value (id interno)", max_length=100, required=True
    )
    descricao = discord.ui.TextInput(
        label="Descrição (opcional)",
        max_length=100,
        required=False,
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        rascunho = obter_rascunho(self.painel.usuario_id)
        label = self.label.value.strip()
        value = self.value.value.strip()
        desc = (self.descricao.value or "").strip()
        placeholder = (self.placeholder.value or "Escolha uma opção…").strip()

        if rascunho.blocos and rascunho.blocos[-1].tipo == "select":
            if len(rascunho.blocos[-1].opcoes_select) >= 25:
                await interacao.response.send_message(
                    "❌ Máximo de 25 opções no select.", ephemeral=True
                )
                return
            rascunho.blocos[-1].opcoes_select.append((label, value, desc))
        else:
            rascunho.blocos.append(
                BlocoTemplate(
                    tipo="select",
                    placeholder_select=placeholder,
                    opcoes_select=[(label, value, desc)],
                )
            )
        await self.painel._atualizar(interacao)


class ModalRodape(LoggingModalMixin, discord.ui.Modal, title="Rodapé do card"):
    ativo = discord.ui.TextInput(
        label="Ativar rodapé? (sim/não)",
        max_length=3,
        required=True,
        default="sim",
    )
    texto = discord.ui.TextInput(
        label="Texto extra do rodapé (opcional)",
        max_length=120,
        required=False,
        placeholder="CENTRO MÉDICO SUL | CMS Valley",
    )
    nome_servidor = discord.ui.TextInput(
        label="Incluir nome do servidor? (sim/não)",
        max_length=3,
        required=True,
        default="sim",
    )
    data_hora = discord.ui.TextInput(
        label="Incluir data e hora? (sim/não)",
        max_length=3,
        required=True,
        default="sim",
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel
        rascunho = obter_rascunho(painel.usuario_id)
        self.ativo.default = "sim" if rascunho.rodape_ativo else "não"
        self.texto.default = rascunho.rodape_texto or ""
        self.nome_servidor.default = "sim" if rascunho.rodape_nome_servidor else "não"
        self.data_hora.default = "sim" if rascunho.rodape_data_hora else "não"

    async def on_submit(self, interacao: discord.Interaction):
        def _sim(valor: str) -> bool:
            return valor.strip().lower() in ("sim", "s", "yes", "y", "1")

        rascunho = obter_rascunho(self.painel.usuario_id)
        rascunho.rodape_ativo = _sim(self.ativo.value)
        rascunho.rodape_texto = (self.texto.value or "").strip()
        rascunho.rodape_nome_servidor = _sim(self.nome_servidor.value)
        rascunho.rodape_data_hora = _sim(self.data_hora.value)
        await self.painel._atualizar(interacao)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class TemplatesCog(commands.Cog):
    """Construtor visual de templates Components V2."""

    grupo = app_commands.Group(
        name="templates",
        description="Construtor visual de templates Components V2",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo.command(
        name="abrir",
        description="Abre o painel interativo de criação de templates",
    )
    @apenas_administrador()
    async def abrir(self, interacao: discord.Interaction):
        obter_rascunho(interacao.user.id)
        await interacao.response.send_message(
            view=PainelTemplatesView(
                interacao.user.id,
                guilda=interacao.guild,
            ),
            ephemeral=True,
        )

    @grupo.command(
        name="preview-dm",
        description="Envia o preview do rascunho atual na sua própria DM",
    )
    @apenas_administrador()
    async def preview_dm(self, interacao: discord.Interaction):
        rascunho = obter_rascunho(interacao.user.id)
        await interacao.response.defer(ephemeral=True)
        preview = montar_preview(rascunho, interacao.guild)
        try:
            await interacao.user.send(view=preview)
            ok = True
        except (discord.Forbidden, discord.HTTPException):
            ok = False
        await enviar_card(
            interacao,
            titulo="✅ Preview na DM" if ok else "❌ Falha na DM",
            linhas=["Confira sua caixa de mensagens diretas."],
            cor=COR_SUCESSO if ok else COR_ERRO,
            delay=15,
        )

    @grupo.command(
        name="ajuda",
        description="Como usar o construtor de templates",
    )
    @apenas_administrador()
    async def ajuda(self, interacao: discord.Interaction):
        await enviar_card(
            interacao,
            titulo="🧩 Ajuda · Templates V2",
            linhas=[
                "`/templates abrir` — painel visual completo.",
                "**Blocos:** título, texto, seção+thumb, separador, galeria, botão, select.",
                "**Rodapé:** texto + nome do servidor + data/hora (`15 jul de 2028 • 22:54`).",
                "**Thumbnail:** ícone do servidor ou URL customizada.",
                "**Preview** mostra o Container final; **Código** gera o Python.",
                "Select no preview é só visual (callback noop) — no código você liga o callback.",
            ],
            cor=COR_INFO,
            delay=50,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TemplatesCog(bot))
