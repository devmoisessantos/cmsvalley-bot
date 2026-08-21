"""
Cards V2 específicos de mensagem apagada e mensagem editada.

Estrutura em dois containers (cards), como pedido no layout de auditoria.
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord

from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
)


def _ts(data: datetime | None, estilo: str = "F") -> str:
    if data is None:
        return "—"
    if data.tzinfo is None:
        data = data.replace(tzinfo=timezone.utc)
    return f"<t:{int(data.timestamp())}:{estilo}>"


def _rodape(guilda: discord.Guild) -> str:
    momento = int(datetime.now(timezone.utc).timestamp())
    return f"-# {guilda.name} • <t:{momento}:f>"


def montar_view_mensagem_apagada(
    *,
    guilda: discord.Guild,
    autor: discord.abc.User | None,
    canal: discord.abc.GuildChannel | discord.Thread | discord.abc.Messageable,
    conteudo: str,
    enviada_em: datetime | None,
    apagada_em: datetime | None,
    id_da_mensagem: int,
    quem_apagou: discord.abc.User | None,
) -> discord.ui.LayoutView:
    """
    Card 1: título + autor + canal.
    Card 2: seção com thumbnail do autor + conteúdo, depois metadados e rodapé.
    """
    mencao_autor = autor.mention if autor else "_desconhecido_"
    mencao_canal = getattr(canal, "mention", str(getattr(canal, "id", "—")))
    url_avatar = autor.display_avatar.url if autor else None
    nome_autor = (
        (autor.display_name if hasattr(autor, "display_name") else autor.name)
        if autor
        else "Desconhecido"
    )
    quem = quem_apagou.mention if quem_apagou else "_desconhecido_"

    # Card 1 — cabeçalho
    texto_card_1 = (
        f"# 🗑️ Mensagem apagada\n"
        f"-  `✍️` **Autor:** {mencao_autor}\n"
        f"-  `#️⃣` **Canal:** {mencao_canal}"
    )
    container_1 = discord.ui.Container(
        discord.ui.TextDisplay(texto_card_1),
        accent_color=COR_ERRO,
    )

    # Card 2 — conteúdo (Section + thumbnail) + meta
    linha_autor_tempo = f"# **{nome_autor}** — {_ts(enviada_em, 'f')}"
    texto_conteudo = (conteudo or "_sem texto_").strip()
    if len(texto_conteudo) > 1800:
        texto_conteudo = texto_conteudo[:1800] + "…"

    componentes_2: list = []
    if url_avatar:
        componentes_2.append(
            discord.ui.Section(
                f"{linha_autor_tempo}\n\n{texto_conteudo}",
                accessory=discord.ui.Thumbnail(url_avatar),
            )
        )
    else:
        componentes_2.append(
            discord.ui.TextDisplay(f"{linha_autor_tempo}\n\n{texto_conteudo}")
        )

    componentes_2.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes_2.append(
        discord.ui.TextDisplay(
            f"-  `📤` **Enviada em:** {_ts(enviada_em)}\n"
            f"-  `🗑️` **Apagada em:** {_ts(apagada_em)}\n"
            f"-  `🆔` **ID da mensagem:** `{id_da_mensagem}`\n"
            f"-  `❓` **Quem apagou:** {quem}"
        )
    )
    componentes_2.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes_2.append(discord.ui.TextDisplay(_rodape(guilda)))

    container_2 = discord.ui.Container(*componentes_2, accent_color=COR_ERRO)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container_1)
    view.add_item(container_2)
    return view


def montar_view_mensagem_editada(
    *,
    guilda: discord.Guild,
    autor: discord.abc.User | None,
    canal: discord.abc.GuildChannel | discord.Thread | discord.abc.Messageable,
    conteudo_anterior: str,
    conteudo_novo: str,
    id_da_mensagem: int,
    url_da_mensagem: str | None,
) -> discord.ui.LayoutView:
    """
    Card 1: título + autor + canal + conteúdo anterior.
    Card 2: seção com thumbnail + conteúdo novo, ID, rodapé e botão de link.
    """
    mencao_autor = autor.mention if autor else "_desconhecido_"
    mencao_canal = getattr(canal, "mention", str(getattr(canal, "id", "—")))
    url_avatar = autor.display_avatar.url if autor else None
    nome_autor = (
        (autor.display_name if hasattr(autor, "display_name") else autor.name)
        if autor
        else "Desconhecido"
    )

    texto_antes = (conteudo_anterior or "_vazio_").strip()
    texto_depois = (conteudo_novo or "_vazio_").strip()
    if len(texto_antes) > 1200:
        texto_antes = texto_antes[:1200] + "…"
    if len(texto_depois) > 1200:
        texto_depois = texto_depois[:1200] + "…"

    # Card 1
    texto_card_1 = (
        f"# ✒️ Mensagem editada\n"
        f"-  `✍️` **Autor:** {mencao_autor}\n"
        f"-  `#️⃣` **Canal:** {mencao_canal}\n"
        f"-  `📝` **Conteúdo anterior:**\n"
        f"> {texto_antes}"
    )
    container_1 = discord.ui.Container(
        discord.ui.TextDisplay(texto_card_1),
        accent_color=COR_AVISO,
    )

    # Card 2
    momento = int(datetime.now(timezone.utc).timestamp())
    linha_autor_tempo = f"**{nome_autor}** · <t:{momento}:f>"
    componentes_2: list = []
    if url_avatar:
        componentes_2.append(
            discord.ui.Section(
                f"{linha_autor_tempo}\n\n{texto_depois}",
                accessory=discord.ui.Thumbnail(url_avatar),
            )
        )
    else:
        componentes_2.append(
            discord.ui.TextDisplay(f"{linha_autor_tempo}\n\n{texto_depois}")
        )

    componentes_2.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes_2.append(
        discord.ui.TextDisplay(f"-  `🆔` **ID da mensagem:** `{id_da_mensagem}`")
    )
    componentes_2.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes_2.append(discord.ui.TextDisplay(_rodape(guilda)))

    if url_da_mensagem:
        linha_botao = discord.ui.ActionRow()
        linha_botao.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.link,
                emoji="📨",
                label="Abrir mensagem",
                url=url_da_mensagem,
            )
        )
        componentes_2.append(linha_botao)

    container_2 = discord.ui.Container(*componentes_2, accent_color=COR_AVISO)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container_1)
    view.add_item(container_2)
    return view
