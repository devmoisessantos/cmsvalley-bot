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

from src.utils.error_handling import ignorar_falha_cosmetica

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
    """Ex.: 15 jul de 2028 • 22:54 — sempre horário de Brasília."""
    from src.utils.formatacao import formatar_data_hora_rodape as _rodape_brasilia

    return _rodape_brasilia(momento)


def mapa_de_cores() -> dict[str, discord.Color]:
    """
    Centraliza as cores permitidas pelo construtor em objetos do Discord.

    Retorna um novo mapa para que os componentes visualizem nomes simples sem
    espalhar códigos de cor pela interface. O conjunto também mantém preview e
    código gerado coerentes quando uma opção do painel é escolhida.
    """
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
    """
    Traduz a cor escolhida na interface para código Python exibível ao usuário.

    O retorno é uma expressão textual, não um objeto de cor, porque será inserido
    no snippet didático gerado pelo construtor. Quando o nome não é reconhecido,
    usa azul padrão para que o exemplo continue válido.
    """
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

    # Sem blocos padrão: painel começa vazio e Resetar zera de verdade.

    @property
    def cor(self) -> discord.Color:
        """Converte o nome salvo em uma cor segura para montar o card atual."""
        return mapa_de_cores().get(self.cor_nome, discord.Color.blurple())


_rascunhos_por_usuario: dict[int, RascunhoTemplate] = {}


def obter_rascunho(id_do_usuario: int) -> RascunhoTemplate:
    """
    Entrega o rascunho isolado de quem está editando um template.

    Cria um estado vazio na primeira consulta e reutiliza o mesmo objeto depois,
    preservando os blocos entre cliques. A separação pelo identificador impede que
    uma pessoa altere acidentalmente o preview que outra pessoa está montando.
    """
    if id_do_usuario not in _rascunhos_por_usuario:
        _rascunhos_por_usuario[id_do_usuario] = RascunhoTemplate()
    return _rascunhos_por_usuario[id_do_usuario]


def limpar_rascunho(id_do_usuario: int) -> None:
    """Zera o rascunho mantendo a sessão com lista de blocos vazia."""
    _rascunhos_por_usuario[id_do_usuario] = RascunhoTemplate(blocos=[])


def _url_icone_da_guilda(guilda: discord.Guild | None) -> str | None:
    if guilda is None or guilda.icon is None:
        return None
    return guilda.icon.url


def montar_texto_do_rodape(
    rascunho: RascunhoTemplate,
    guilda: discord.Guild | None,
) -> str:
    """
    Combina as opções de rodapé em uma linha discreta para Components V2.

    Considera o texto livre, o nome da guilda e a data conforme as preferências do
    rascunho. Retorna texto vazio quando nenhuma parte foi habilitada, para impedir
    que a montagem adicione um separador e um rodapé visualmente inúteis.
    """
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


async def ao_clicar_sem_fazer_nada(interacao: discord.Interaction) -> None:
    """Callback vazio para preview (não executa regra de negócio)."""
    try:
        await interacao.response.defer()
    except discord.InteractionResponded as erro_no_botao_sem_acao:
        # Enfeite que falhou: responder a um botao de exemplo.
        # A acao principal ja tinha dado certo, entao so registro.
        ignorar_falha_cosmetica(
            erro_no_botao_sem_acao,
            o_que_falhou="responder a um botao de exemplo",
        )


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
        e_link = estilo == "link" or (
            isinstance(url_ou_id, str)
            and (url_ou_id.startswith("http://") or url_ou_id.startswith("https://"))
        )
        if e_link:
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
            botao.callback = ao_clicar_sem_fazer_nada
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
    select.callback = ao_clicar_sem_fazer_nada
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
    select.callback = ao_clicar_sem_fazer_nada
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
            urls = [
                url_da_midia.strip()
                for url_da_midia in bloco.urls_midia
                if url_da_midia.strip().startswith("http")
            ]
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
    """LayoutView de preview (timeout=None — reutilizável em post persistente)."""
    componentes = montar_componentes_do_container(rascunho, guilda)
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*componentes, accent_color=rascunho.cor))
    return view


def montar_mensagem_persistente(
    rascunho: RascunhoTemplate,
    guilda: discord.Guild | None = None,
) -> discord.ui.LayoutView:
    """
    LayoutView para postar de forma permanente em um canal.
    Mesma montagem do preview; timeout=None para a mensagem permanecer.
    """
    return montar_preview(rascunho, guilda)


def resumo_dos_blocos(rascunho: RascunhoTemplate) -> str:
    """Lista legível de todos os blocos (texto completo, sem corte agressivo)."""
    if not rascunho.blocos:
        return "_Nenhum bloco. Use os botões para montar o card._"
    linhas: list[str] = []
    for indice, bloco in enumerate(rascunho.blocos, 1):
        if bloco.tipo in ("titulo", "texto", "secao") and bloco.texto:
            # Texto integral; quebra visual em linhas curtas se for muito longo
            corpo = bloco.texto.strip()
            linhas.append(f"`{indice}.` **{bloco.tipo}**\n{corpo}")
        elif bloco.tipo == "separador":
            linhas.append(f"`{indice}.` **separador** ({bloco.espacamento})")
        elif bloco.tipo == "galeria":
            urls = ", ".join(bloco.urls_midia[:3])
            mais = f" +{len(bloco.urls_midia) - 3}" if len(bloco.urls_midia) > 3 else ""
            linhas.append(
                f"`{indice}.` **galeria** ({len(bloco.urls_midia)} img) {urls}{mais}"
            )
        elif bloco.tipo == "botoes":
            rotulos = ", ".join(
                rotulo_do_botao for rotulo_do_botao, _, _ in bloco.botoes
            )
            linhas.append(f"`{indice}.` **botoes** ({len(bloco.botoes)}): {rotulos}")
        elif bloco.tipo == "select_string":
            opcoes = ", ".join(lab for lab, _, _ in bloco.opcoes_select)
            linhas.append(
                f"`{indice}.` **select_string** ({len(bloco.opcoes_select)} opc): "
                f"{opcoes}"
            )
        elif bloco.tipo.startswith("select_"):
            linhas.append(
                f"`{indice}.` **{bloco.tipo}** — {bloco.placeholder_select_especial}"
            )
        elif bloco.tipo == "arquivo":
            linhas.append(f"`{indice}.` **arquivo** `{bloco.nome_arquivo or '?'}`")
        else:
            linhas.append(f"`{indice}.` **{bloco.tipo}**")
    return "\n\n".join(linhas)


def _codigo_do_bloco(bloco: BlocoTemplate, indent: str = "    ") -> list[str]:
    indice = indent
    if bloco.tipo == "titulo":
        linha_de_codigo = f"# {bloco.texto}"
        return [
            f"{indice}# TextDisplay com markdown de título",
            f"{indice}componentes.append(discord.ui.TextDisplay({linha_de_codigo!r}))",
        ]
    if bloco.tipo == "texto":
        return [
            f"{indice}# TextDisplay = texto livre (markdown ok)",
            f"{indice}componentes.append(discord.ui.TextDisplay({bloco.texto!r}))",
        ]
    if bloco.tipo == "separador":
        esp = (
            "discord.SeparatorSpacing.small"
            if bloco.espacamento == "small"
            else "discord.SeparatorSpacing.large"
        )
        return [
            f"{indice}# Separator divide blocos visualmente",
            f"{indice}componentes.append(discord.ui.Separator(spacing={esp}))",
        ]
    if bloco.tipo == "secao":
        out = [f"{indice}# Section + accessory (Thumbnail ou Button link)"]
        if bloco.accessory_botao_rotulo and bloco.accessory_botao_url.startswith(
            "http"
        ):
            out.extend(
                [
                    f"{indice}botao_accessory = discord.ui.Button(",
                    f"{indice}    label={bloco.accessory_botao_rotulo!r},",
                    f"{indice}    style=discord.ButtonStyle.link,",
                    f"{indice}    url={bloco.accessory_botao_url!r},",
                    f"{indice})",
                    f"{indice}componentes.append(discord.ui.Section({bloco.texto!r}, "
                    f"accessory=botao_accessory))",
                ]
            )
        elif bloco.usar_thumbnail_servidor:
            out.extend(
                [
                    f"{indice}url_icone = guilda.icon.url if guilda and guilda.icon "
                    f"else None",
                    f"{indice}if url_icone:",
                    f"{indice}    componentes.append(discord.ui.Section(",
                    f"{indice}        {bloco.texto!r},",
                    f"{indice}        accessory=discord.ui.Thumbnail(url_icone),",
                    f"{indice}    ))",
                    f"{indice}else:",
                    f"{indice}    "
                    f"componentes.append(discord.ui.TextDisplay({bloco.texto!r}"
                    f"))",
                ]
            )
        elif bloco.url_thumbnail.strip().startswith("http"):
            out.append(
                f"{indice}componentes.append(discord.ui.Section("
                f"{bloco.texto!r}, "
                f"accessory=discord.ui.Thumbnail({bloco.url_thumbnail.strip()!r})))"
            )
        else:
            out.append(
                f"{indice}componentes.append(discord.ui.TextDisplay({bloco.texto!r}))"
            )
        return out
    if bloco.tipo == "galeria":
        urls = [
            url_da_midia
            for url_da_midia in bloco.urls_midia
            if url_da_midia.startswith("http")
        ]
        return [
            f"{indice}# MediaGallery: 1–10 MediaGalleryItem",
            f"{indice}urls = {urls!r}",
            f"{indice}itens = [discord.MediaGalleryItem(u) for u in urls]",
            f"{indice}componentes.append(discord.ui.MediaGallery(*itens))",
        ]
    if bloco.tipo == "arquivo":
        nome = bloco.nome_arquivo or "arquivo.bin"
        return [
            f"{indice}# ui.File exige enviar o attachment no send",
            f"{indice}# arquivo = discord.File('caminho/{nome}', filename={nome!r})",
            f"{indice}# componentes.append(discord.ui.File(media=arquivo))",
            f"{indice}# await canal.send(view=view, files=[arquivo])",
            f"{indice}componentes.append(discord.ui.TextDisplay('📎 `{nome}` — troque "
            f"por ui.File'))",
        ]
    if bloco.tipo == "botoes":
        out = [
            f"{indice}# ActionRow: ≤5 Buttons OU 1 Select sozinho",
            f"{indice}linha = discord.ui.ActionRow()",
        ]
        for rotulo, estilo, valor in bloco.botoes:
            if estilo == "link":
                out.append(
                    f"{indice}linha.add_item(discord.ui.Button(label={rotulo!r}, "
                    f"style=discord.ButtonStyle.link, url={valor!r}))"
                )
            else:
                out.extend(
                    [
                        f"{indice}btn = discord.ui.Button(label={rotulo!r}, "
                        f"style=discord.ButtonStyle.{estilo}, "
                        f"custom_id={('tpl:' + valor)!r})",
                        f"{indice}# btn.callback = seu_callback",
                        f"{indice}linha.add_item(btn)",
                    ]
                )
        out.append(f"{indice}componentes.append(linha)")
        return out
    if bloco.tipo == "select_string":
        out = [
            f"{indice}# Select string com SelectOption manuais",
            f"{indice}opcoes = [",
        ]
        for label, value, desc in bloco.opcoes_select:
            out.append(
                f"{indice}    discord.SelectOption(label={label!r}, value={value!r}, "
                f"description={desc!r}),"
            )
        out.extend(
            [
                f"{indice}]",
                f"{indice}select = "
                f"discord.ui.Select(placeholder={bloco.placeholder_select!r}"
                f", options=opcoes)",
                f"{indice}# select.callback = seu_callback",
                f"{indice}linha = discord.ui.ActionRow()",
                f"{indice}linha.add_item(select)",
                f"{indice}componentes.append(linha)",
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
            f"{indice}# Select preenchido pelo Discord automaticamente",
            f"{indice}select = {mapa[bloco.tipo]}(",
            f"{indice}    placeholder={bloco.placeholder_select_especial!r},",
            f"{indice}    min_values={bloco.min_valores},",
            f"{indice}    max_values={bloco.max_valores},",
            f"{indice})",
            f"{indice}# select.callback = seu_callback",
            f"{indice}linha = discord.ui.ActionRow()",
            f"{indice}linha.add_item(select)",
            f"{indice}componentes.append(linha)",
        ]
    return [f"{indice}# tipo desconhecido: {bloco.tipo}"]


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
    for numero, bloco in enumerate(rascunho.blocos, 1):
        linhas.append(f"    # --- bloco {numero}: {bloco.tipo} ---")
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
                    "    from zoneinfo import ZoneInfo",
                    "    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))",
                    "    meses = "
                    "('jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez')",
                    '    partes.append(f"{agora.day} {meses[agora.month-1]} de '
                    "{agora.year} • {agora.strftime('%H:%M')}\")",
                ]
            )
        linhas.extend(
            [
                "    "
                "componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))",
                '    componentes.append(discord.ui.TextDisplay("-# " + " • '
                '".join(partes)))',
                "",
            ]
        )
    cor = expressao_cor_python(rascunho.cor_nome)
    linhas.extend(
        [
            "    # LayoutView = raiz V2; Container = grupo visual com accent_color",
            "    view = discord.ui.LayoutView(timeout=None)",
            f"    view.add_item(discord.ui.Container(*componentes, accent_color={cor}"
            f"))",
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
        "from src.utils.mensagens import responder_sucesso",
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
                    "        # FileUpload só em Modal, dentro de Label (discord.py "
                    "2.6+)",
                    "        self.envio = discord.ui.FileUpload(min_values=1, "
                    "max_values=1, required=False)",
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
                "        self.add_item(discord.ui.Label(text='Confirma?', "
                "component=self.radio))",
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
                "        self.add_item(discord.ui.Label(text='Marque o que se "
                "aplica', component=self.checks))",
                "",
            ]
        )
    linhas.extend(
        [
            "    async def on_submit(self, interacao: discord.Interaction) -> None:",
            "        # Toda resposta ao membro passa por src/utils/mensagens.py.",
            "        await responder_sucesso(",
            "            interacao,",
            "            titulo='Formulário recebido',",
            "            linhas=['Seus dados foram registrados.'],",
            "        )",
            "",
            "# Uso: await interacao.response.send_modal(ModalExemplo())",
            "",
        ]
    )
    return "\n".join(linhas)
