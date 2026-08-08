"""
Modelo, montagem e geração de código do construtor /templates.

Responsabilidades:
  - RascunhoTemplate / BlocoTemplate (estado da sessão)
  - montar_preview → LayoutView Components V2
  - gerar_codigo_mensagem / gerar_codigo_modal → snippets didáticos

Componentes cobertos (discord.ui Bot UI Kit):
  LayoutView, Container, TextDisplay, Separator, Section, Thumbnail,
  MediaGallery, File, ActionRow, Button, Select, UserSelect, RoleSelect,
  ChannelSelect, MentionableSelect, Modal, TextInput, Label, FileUpload,
  RadioGroup, CheckboxGroup
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from datetime import datetime
from typing import Any

import discord

# ---------------------------------------------------------------------------
# Detecção da API (versões antigas não quebram o bot)
# ---------------------------------------------------------------------------

TEM_FILE = hasattr(discord.ui, "File")
TEM_LABEL = hasattr(discord.ui, "Label")
TEM_FILE_UPLOAD = hasattr(discord.ui, "FileUpload")
TEM_RADIO_GROUP = hasattr(discord.ui, "RadioGroup")
TEM_CHECKBOX_GROUP = hasattr(discord.ui, "CheckboxGroup")
TEM_USER_SELECT = hasattr(discord.ui, "UserSelect")
TEM_ROLE_SELECT = hasattr(discord.ui, "RoleSelect")
TEM_CHANNEL_SELECT = hasattr(discord.ui, "ChannelSelect")
TEM_MENTIONABLE_SELECT = hasattr(discord.ui, "MentionableSelect")

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
    return (
        f"{momento.day} {MESES_PT[momento.month - 1]} de {momento.year} "
        f"• {momento.strftime('%H:%M')}"
    )


def mapa_de_cores() -> dict[str, discord.Color]:
    return {
        "info": discord.Color.blurple(),
        "sucesso": discord.Color.green(),
        "aviso": discord.Color.orange(),
        "erro": discord.Color.red(),
        "escuro": discord.Color.dark_red(),
        "teal": discord.Color.dark_teal(),
        "ouro": discord.Color.gold(),
        "roxo": discord.Color.purple(),
    }


def expressao_cor_python(nome: str) -> str:
    mapa = {
        "info": "discord.Color.blurple()",
        "sucesso": "discord.Color.green()",
        "aviso": "discord.Color.orange()",
        "erro": "discord.Color.red()",
        "escuro": "discord.Color.dark_red()",
        "teal": "discord.Color.dark_teal()",
        "ouro": "discord.Color.gold()",
        "roxo": "discord.Color.purple()",
    }
    return mapa.get(nome, "discord.Color.blurple()")


@dataclass
class BlocoTemplate:
    """Um item na ordem de montagem do Container."""

    tipo: str
    texto: str = ""
    espacamento: str = "large"
    usar_thumbnail_servidor: bool = False
    url_thumbnail: str = ""
    accessory_botao_rotulo: str = ""
    accessory_botao_url: str = ""
    urls_midia: list[str] = field(default_factory=list)
    nome_arquivo: str = ""
    # botões: (rótulo, estilo, url_ou_custom_id)
    botoes: list[tuple[str, str, str]] = field(default_factory=list)
    placeholder_select: str = "Escolha uma opção…"
    opcoes_select: list[tuple[str, str, str]] = field(default_factory=list)
    placeholder_select_especial: str = "Selecione…"
    min_valores: int = 1
    max_valores: int = 1


@dataclass
class RascunhoTemplate:
    """Estado completo do template em edição."""

    cor_nome: str = "info"
    blocos: list[BlocoTemplate] = field(default_factory=list)
    rodape_ativo: bool = False
    rodape_texto: str = ""
    rodape_nome_servidor: bool = True
    rodape_data_hora: bool = True
    modal_titulo: str = "Formulário de exemplo"
    modal_com_text_input: bool = True
    modal_com_file_upload: bool = True
    modal_com_radio: bool = True
    modal_com_checkbox: bool = True

    def __post_init__(self) -> None:
        if not self.blocos:
            self.blocos = [
                BlocoTemplate(tipo="titulo", texto="Título do card"),
                BlocoTemplate(
                    tipo="texto",
                    texto=(
                        "Corpo do card. Adicione separadores, seções, "
                        "galeria, botões e selects pelo painel."
                    ),
                ),
            ]

    @property
    def cor(self) -> discord.Color:
        return mapa_de_cores().get(self.cor_nome, discord.Color.blurple())


_rascunhos_por_usuario: dict[int, RascunhoTemplate] = {}


def obter_rascunho(id_do_usuario: int) -> RascunhoTemplate:
    if id_do_usuario not in _rascunhos_por_usuario:
        _rascunhos_por_usuario[id_do_usuario] = RascunhoTemplate()
    return _rascunhos_por_usuario[id_do_usuario]


def limpar_rascunho(id_do_usuario: int) -> None:
    _rascunhos_por_usuario.pop(id_do_usuario, None)


def _url_icone_da_guilda(guilda: discord.Guild | None) -> str | None:
    if guilda is None or guilda.icon is None:
        return None
    return guilda.icon.url


def montar_texto_do_rodape(
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


async def callback_noop(interacao: discord.Interaction) -> None:
    """Callback vazio para preview (não executa regra de negócio)."""
    try:
        await interacao.response.defer()
    except discord.InteractionResponded:
        pass


def _estilo_botao(nome: str) -> discord.ButtonStyle:
    return {
        "primary": discord.ButtonStyle.primary,
        "secondary": discord.ButtonStyle.secondary,
        "success": discord.ButtonStyle.success,
        "danger": discord.ButtonStyle.danger,
        "link": discord.ButtonStyle.link,
    }.get(nome, discord.ButtonStyle.secondary)


def _montar_secao(bloco: BlocoTemplate, url_icone: str | None) -> Any:
    texto = bloco.texto or "—"
    if bloco.accessory_botao_rotulo and bloco.accessory_botao_url.startswith("http"):
        return discord.ui.Section(
            texto,
            accessory=discord.ui.Button(
                label=bloco.accessory_botao_rotulo[:80],
                style=discord.ButtonStyle.link,
                url=bloco.accessory_botao_url,
            ),
        )
    url_thumb = None
    if bloco.usar_thumbnail_servidor and url_icone:
        url_thumb = url_icone
    elif bloco.url_thumbnail.strip().startswith("http"):
        url_thumb = bloco.url_thumbnail.strip()
    if url_thumb:
        return discord.ui.Section(texto, accessory=discord.ui.Thumbnail(url_thumb))
    return discord.ui.TextDisplay(texto)


def _montar_linha_de_botoes(botoes: list[tuple[str, str, str]]) -> discord.ui.ActionRow:
    linha = discord.ui.ActionRow()
    for rotulo, estilo, url_ou_id in botoes[:5]:
        if estilo == "link":
            linha.add_item(
                discord.ui.Button(
                    label=rotulo[:80],
                    style=discord.ButtonStyle.link,
                    url=url_ou_id,
                )
            )
        else:
            botao = discord.ui.Button(
                label=rotulo[:80],
                style=_estilo_botao(estilo),
                custom_id=f"tpl:{url_ou_id}"[:100],
            )
            botao.callback = callback_noop
            linha.add_item(botao)
    return linha


def _montar_select_string(bloco: BlocoTemplate) -> discord.ui.ActionRow:
    opcoes = [
        discord.SelectOption(
            label=label[:100],
            value=value[:100],
            description=(desc[:100] if desc else None),
        )
        for label, value, desc in bloco.opcoes_select[:25]
    ]
    select = discord.ui.Select(
        placeholder=bloco.placeholder_select[:150],
        options=opcoes,
        min_values=1,
        max_values=1,
    )
    select.callback = callback_noop
    linha = discord.ui.ActionRow()
    linha.add_item(select)
    return linha


def _montar_select_especial(
    classe_select: type,
    placeholder: str,
    min_valores: int,
    max_valores: int,
) -> discord.ui.ActionRow:
    select = classe_select(
        placeholder=placeholder[:150],
        min_values=max(0, min_valores),
        max_values=max(1, min(max_valores, 25)),
    )
    select.callback = callback_noop
    linha = discord.ui.ActionRow()
    linha.add_item(select)
    return linha


def montar_componentes_do_container(
    rascunho: RascunhoTemplate,
    guilda: discord.Guild | None = None,
) -> list[Any]:
    """Lista de itens que entram no Container (ordem = blocos + rodapé)."""
    componentes: list[Any] = []
    url_icone = _url_icone_da_guilda(guilda)

    for bloco in rascunho.blocos:
        if bloco.tipo == "titulo":
            componentes.append(discord.ui.TextDisplay(f"# {bloco.texto}"))
        elif bloco.tipo == "texto" and bloco.texto.strip():
            componentes.append(discord.ui.TextDisplay(bloco.texto))
        elif bloco.tipo == "separador":
            espaco = (
                discord.SeparatorSpacing.small
                if bloco.espacamento == "small"
                else discord.SeparatorSpacing.large
            )
            componentes.append(discord.ui.Separator(spacing=espaco))
        elif bloco.tipo == "secao":
            componentes.append(_montar_secao(bloco, url_icone))
        elif bloco.tipo == "galeria":
            urls = [u.strip() for u in bloco.urls_midia if u.strip().startswith("http")]
            if urls:
                itens = [discord.MediaGalleryItem(url) for url in urls[:10]]
                componentes.append(discord.ui.MediaGallery(*itens))
        elif bloco.tipo == "arquivo":
            nome = bloco.nome_arquivo or "arquivo.bin"
            componentes.append(
                discord.ui.TextDisplay(
                    f"📎 **Arquivo (ui.File):** `{nome}`\n"
                    "-# No envio real use discord.ui.File + files=[...] no send."
                )
            )
        elif bloco.tipo == "botoes" and bloco.botoes:
            componentes.append(_montar_linha_de_botoes(bloco.botoes))
        elif bloco.tipo == "select_string" and bloco.opcoes_select:
            componentes.append(_montar_select_string(bloco))
        elif bloco.tipo == "select_user" and TEM_USER_SELECT:
            componentes.append(
                _montar_select_especial(
                    discord.ui.UserSelect,
                    bloco.placeholder_select_especial,
                    bloco.min_valores,
                    bloco.max_valores,
                )
            )
        elif bloco.tipo == "select_role" and TEM_ROLE_SELECT:
            componentes.append(
                _montar_select_especial(
                    discord.ui.RoleSelect,
                    bloco.placeholder_select_especial,
                    bloco.min_valores,
                    bloco.max_valores,
                )
            )
        elif bloco.tipo == "select_channel" and TEM_CHANNEL_SELECT:
            componentes.append(
                _montar_select_especial(
                    discord.ui.ChannelSelect,
                    bloco.placeholder_select_especial,
                    bloco.min_valores,
                    bloco.max_valores,
                )
            )
        elif bloco.tipo == "select_mentionable" and TEM_MENTIONABLE_SELECT:
            componentes.append(
                _montar_select_especial(
                    discord.ui.MentionableSelect,
                    bloco.placeholder_select_especial,
                    bloco.min_valores,
                    bloco.max_valores,
                )
            )

    if rascunho.rodape_ativo:
        texto = montar_texto_do_rodape(rascunho, guilda)
        if texto:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
            )
            componentes.append(discord.ui.TextDisplay(texto))

    if not componentes:
        componentes.append(discord.ui.TextDisplay("_Rascunho vazio._"))
    return componentes


def montar_preview(
    rascunho: RascunhoTemplate,
    guilda: discord.Guild | None = None,
) -> discord.ui.LayoutView:
    """LayoutView pronta para preview efêmero."""
    componentes = montar_componentes_do_container(rascunho, guilda)
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*componentes, accent_color=rascunho.cor))
    return view


def resumo_dos_blocos(rascunho: RascunhoTemplate) -> str:
    if not rascunho.blocos:
        return "_nenhum bloco_"
    linhas: list[str] = []
    for i, bloco in enumerate(rascunho.blocos, 1):
        extra = ""
        if bloco.tipo in ("titulo", "texto", "secao") and bloco.texto:
            extra = f" — `{bloco.texto[:36]}`"
        elif bloco.tipo == "separador":
            extra = f" ({bloco.espacamento})"
        elif bloco.tipo == "galeria":
            extra = f" ({len(bloco.urls_midia)} img)"
        elif bloco.tipo == "botoes":
            extra = f" ({len(bloco.botoes)} btn)"
        elif bloco.tipo == "select_string":
            extra = f" ({len(bloco.opcoes_select)} opc)"
        elif bloco.tipo.startswith("select_"):
            extra = f" ({bloco.placeholder_select_especial[:24]})"
        elif bloco.tipo == "arquivo":
            extra = f" (`{bloco.nome_arquivo or '?'}`)"
        linhas.append(f"`{i}.` **{bloco.tipo}**{extra}")
    return "\n".join(linhas)


def _codigo_do_bloco(bloco: BlocoTemplate, indent: str = "    ") -> list[str]:
    i = indent
    if bloco.tipo == "titulo":
        t = f"# {bloco.texto}"
        return [
            f"{i}# TextDisplay com markdown de título",
            f"{i}componentes.append(discord.ui.TextDisplay({t!r}))",
        ]
    if bloco.tipo == "texto":
        return [
            f"{i}# TextDisplay = texto livre (markdown ok)",
            f"{i}componentes.append(discord.ui.TextDisplay({bloco.texto!r}))",
        ]
    if bloco.tipo == "separador":
        esp = (
            "discord.SeparatorSpacing.small"
            if bloco.espacamento == "small"
            else "discord.SeparatorSpacing.large"
        )
        return [
            f"{i}# Separator divide blocos visualmente",
            f"{i}componentes.append(discord.ui.Separator(spacing={esp}))",
        ]
    if bloco.tipo == "secao":
        out = [f"{i}# Section + accessory (Thumbnail ou Button link)"]
        if bloco.accessory_botao_rotulo and bloco.accessory_botao_url.startswith(
            "http"
        ):
            out.extend(
                [
                    f"{i}botao_accessory = discord.ui.Button(",
                    f"{i}    label={bloco.accessory_botao_rotulo!r},",
                    f"{i}    style=discord.ButtonStyle.link,",
                    f"{i}    url={bloco.accessory_botao_url!r},",
                    f"{i})",
                    f"{i}componentes.append(discord.ui.Section({bloco.texto!r}, accessory=botao_accessory))",
                ]
            )
        elif bloco.usar_thumbnail_servidor:
            out.extend(
                [
                    f"{i}url_icone = guilda.icon.url if guilda and guilda.icon else None",
                    f"{i}if url_icone:",
                    f"{i}    componentes.append(discord.ui.Section(",
                    f"{i}        {bloco.texto!r},",
                    f"{i}        accessory=discord.ui.Thumbnail(url_icone),",
                    f"{i}    ))",
                    f"{i}else:",
                    f"{i}    componentes.append(discord.ui.TextDisplay({bloco.texto!r}))",
                ]
            )
        elif bloco.url_thumbnail.strip().startswith("http"):
            out.append(
                f"{i}componentes.append(discord.ui.Section("
                f"{bloco.texto!r}, accessory=discord.ui.Thumbnail({bloco.url_thumbnail.strip()!r})))"
            )
        else:
            out.append(
                f"{i}componentes.append(discord.ui.TextDisplay({bloco.texto!r}))"
            )
        return out
    if bloco.tipo == "galeria":
        urls = [u for u in bloco.urls_midia if u.startswith("http")]
        return [
            f"{i}# MediaGallery: 1–10 MediaGalleryItem",
            f"{i}urls = {urls!r}",
            f"{i}itens = [discord.MediaGalleryItem(u) for u in urls]",
            f"{i}componentes.append(discord.ui.MediaGallery(*itens))",
        ]
    if bloco.tipo == "arquivo":
        nome = bloco.nome_arquivo or "arquivo.bin"
        return [
            f"{i}# ui.File exige enviar o attachment no send",
            f"{i}# arquivo = discord.File('caminho/{nome}', filename={nome!r})",
            f"{i}# componentes.append(discord.ui.File(media=arquivo))",
            f"{i}# await canal.send(view=view, files=[arquivo])",
            f"{i}componentes.append(discord.ui.TextDisplay('📎 `{nome}` — troque por ui.File'))",
        ]
    if bloco.tipo == "botoes":
        out = [
            f"{i}# ActionRow: ≤5 Buttons OU 1 Select sozinho",
            f"{i}linha = discord.ui.ActionRow()",
        ]
        for rotulo, estilo, valor in bloco.botoes:
            if estilo == "link":
                out.append(
                    f"{i}linha.add_item(discord.ui.Button(label={rotulo!r}, "
                    f"style=discord.ButtonStyle.link, url={valor!r}))"
                )
            else:
                out.extend(
                    [
                        f"{i}btn = discord.ui.Button(label={rotulo!r}, "
                        f"style=discord.ButtonStyle.{estilo}, custom_id={('tpl:' + valor)!r})",
                        f"{i}# btn.callback = seu_callback",
                        f"{i}linha.add_item(btn)",
                    ]
                )
        out.append(f"{i}componentes.append(linha)")
        return out
    if bloco.tipo == "select_string":
        out = [f"{i}# Select string com SelectOption manuais", f"{i}opcoes = ["]
        for label, value, desc in bloco.opcoes_select:
            out.append(
                f"{i}    discord.SelectOption(label={label!r}, value={value!r}, description={desc!r}),"
            )
        out.extend(
            [
                f"{i}]",
                f"{i}select = discord.ui.Select(placeholder={bloco.placeholder_select!r}, options=opcoes)",
                f"{i}# select.callback = seu_callback",
                f"{i}linha = discord.ui.ActionRow()",
                f"{i}linha.add_item(select)",
                f"{i}componentes.append(linha)",
            ]
        )
        return out
    mapa = {
        "select_user": "discord.ui.UserSelect",
        "select_role": "discord.ui.RoleSelect",
        "select_channel": "discord.ui.ChannelSelect",
        "select_mentionable": "discord.ui.MentionableSelect",
    }
    if bloco.tipo in mapa:
        return [
            f"{i}# Select preenchido pelo Discord automaticamente",
            f"{i}select = {mapa[bloco.tipo]}(",
            f"{i}    placeholder={bloco.placeholder_select_especial!r},",
            f"{i}    min_values={bloco.min_valores},",
            f"{i}    max_values={bloco.max_valores},",
            f"{i})",
            f"{i}# select.callback = seu_callback",
            f"{i}linha = discord.ui.ActionRow()",
            f"{i}linha.add_item(select)",
            f"{i}componentes.append(linha)",
        ]
    return [f"{i}# tipo desconhecido: {bloco.tipo}"]


def gerar_codigo_mensagem(
    rascunho: RascunhoTemplate,
    guilda: discord.Guild | None = None,
) -> str:
    """Snippet Python didático da mensagem LayoutView + Container."""
    linhas = [
        '"""Template gerado por /templates — Components V2 (mensagem)."""',
        "from __future__ import annotations",
        "from datetime import datetime",
        "import discord",
        "",
        "async def enviar_template_no_canal(",
        "    canal: discord.abc.Messageable,",
        "    guilda: discord.Guild | None = None,",
        ") -> discord.Message:",
        '    """Monta o LayoutView e envia no canal (sem content/embeds)."""',
        "    componentes: list = []",
        "",
    ]
    for n, bloco in enumerate(rascunho.blocos, 1):
        linhas.append(f"    # --- bloco {n}: {bloco.tipo} ---")
        linhas.extend(_codigo_do_bloco(bloco))
        linhas.append("")
    if rascunho.rodape_ativo:
        linhas.extend(
            [
                "    # --- rodapé (-# discreto) ---",
                "    partes: list[str] = []",
            ]
        )
        if rascunho.rodape_texto.strip():
            linhas.append(f"    partes.append({rascunho.rodape_texto.strip()!r})")
        if rascunho.rodape_nome_servidor:
            linhas.extend(
                [
                    "    if guilda is not None:",
                    "        partes.append(guilda.name)",
                ]
            )
        if rascunho.rodape_data_hora:
            linhas.extend(
                [
                    "    agora = datetime.now()",
                    "    meses = ('jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez')",
                    "    partes.append(f\"{agora.day} {meses[agora.month-1]} de {agora.year} • {agora.strftime('%H:%M')}\")",
                ]
            )
        linhas.extend(
            [
                "    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))",
                '    componentes.append(discord.ui.TextDisplay("-# " + " • ".join(partes)))',
                "",
            ]
        )
    cor = expressao_cor_python(rascunho.cor_nome)
    linhas.extend(
        [
            "    # LayoutView = raiz V2; Container = grupo visual com accent_color",
            "    view = discord.ui.LayoutView(timeout=None)",
            f"    view.add_item(discord.ui.Container(*componentes, accent_color={cor}))",
            "    return await canal.send(view=view)",
            "",
        ]
    )
    return "\n".join(linhas)


def gerar_codigo_modal(rascunho: RascunhoTemplate) -> str:
    """Snippet Python didático de Modal com Label / FileUpload / Radio / Checkbox."""
    linhas = [
        '"""Template gerado por /templates — Modal (Label + inputs)."""',
        "from __future__ import annotations",
        "import discord",
        "",
        f"class ModalExemplo(discord.ui.Modal, title={rascunho.modal_titulo!r}):",
        '    """Modal V2: inputs preferencialmente dentro de Label."""',
        "",
        "    def __init__(self) -> None:",
        "        super().__init__()",
        "",
    ]
    if rascunho.modal_com_text_input:
        linhas.extend(
            [
                "        self.campo_texto = discord.ui.TextInput(",
                "            label='Observação',",
                "            style=discord.TextStyle.paragraph,",
                "            required=True,",
                "            max_length=500,",
                "        )",
                "        if hasattr(discord.ui, 'Label'):",
                "            self.add_item(discord.ui.Label(",
                "                text='Observação',",
                "                description='Campo de texto livre',",
                "                component=self.campo_texto,",
                "            ))",
                "        else:",
                "            self.add_item(self.campo_texto)",
                "",
            ]
        )
    if rascunho.modal_com_file_upload:
        if TEM_FILE_UPLOAD and TEM_LABEL:
            linhas.extend(
                [
                    "        # FileUpload só em Modal, dentro de Label (discord.py 2.6+)",
                    "        self.envio = discord.ui.FileUpload(min_values=1, max_values=1, required=False)",
                    "        self.add_item(discord.ui.Label(",
                    "            text='Anexo',",
                    "            description='Até 1 arquivo',",
                    "            component=self.envio,",
                    "        ))",
                    "",
                ]
            )
        else:
            linhas.append(
                "        # FileUpload indisponível nesta versão do discord.py\n"
            )
    if rascunho.modal_com_radio and TEM_RADIO_GROUP and TEM_LABEL:
        linhas.extend(
            [
                "        # RadioGroup: exatamente 1 opção (modal only, 2.7+)",
                "        self.radio = discord.ui.RadioGroup(",
                "            discord.RadioGroupOption(label='Sim', value='sim'),",
                "            discord.RadioGroupOption(label='Não', value='nao'),",
                "        )",
                "        self.add_item(discord.ui.Label(text='Confirma?', component=self.radio))",
                "",
            ]
        )
    if rascunho.modal_com_checkbox and TEM_CHECKBOX_GROUP and TEM_LABEL:
        linhas.extend(
            [
                "        # CheckboxGroup: várias opções (modal only, 2.7+)",
                "        self.checks = discord.ui.CheckboxGroup(",
                "            discord.CheckboxGroupOption(label='Opção A', value='a'),",
                "            discord.CheckboxGroupOption(label='Opção B', value='b'),",
                "            min_values=0, max_values=2,",
                "        )",
                "        self.add_item(discord.ui.Label(text='Marque o que se aplica', component=self.checks))",
                "",
            ]
        )
    linhas.extend(
        [
            "    async def on_submit(self, interacao: discord.Interaction) -> None:",
            "        await interacao.response.send_message('Formulário recebido.', ephemeral=True)",
            "",
            "# Uso: await interacao.response.send_modal(ModalExemplo())",
            "",
        ]
    )
    return "\n".join(linhas)
