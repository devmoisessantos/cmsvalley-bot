"""
Montagem dos cards de boas-vindas e adeus (Components V2).

Usa LayoutView + Container + Section com a thumbnail do avatar do membro.
O texto do rodapé reaproveita a formatação de data do projeto.
"""

from __future__ import annotations

import discord

from src.utils.formatacao import formatar_data_hora_rodape


def _url_do_avatar(membro: discord.Member | discord.User) -> str | None:
    """
    Devolve a URL do avatar do membro, se existir.

    display_avatar já cobre avatar customizado e o padrão do Discord.
    """
    try:
        return membro.display_avatar.url
    except Exception:
        return None


def _montar_rodape(guilda: discord.Guild | None) -> str:
    """
    Monta o rodapé discreto com nome do servidor e horário de Brasília.
    """
    partes: list[str] = []
    if guilda is not None:
        partes.append(f"**{guilda.name}**")
    partes.append(f"`{formatar_data_hora_rodape()}`")
    return "-# " + " • ".join(partes)


def montar_card_boas_vindas(
    membro: discord.Member,
) -> discord.ui.LayoutView:
    """
    Monta o card de boas-vindas para um membro que acabou de entrar.

    A thumbnail é sempre o avatar do membro, nunca o ícone do servidor.
    """
    mencao = membro.mention
    id_do_membro = membro.id
    nome_base = membro.name

    texto_titulo = (
        "# 👋 Bem-vindo(a) ao Centro Médico Sul Valley!\n"
        f"➜ **Usuário:** {mencao} (`{id_do_membro}`)\n"
        f"➜ **Nome de usuário:** `@{nome_base}`"
    )
    texto_corpo = (
        "✨ Ficamos muito felizes com a sua chegada!\n"
        "Aproveite tudo o que nosso servidor tem a oferecer."
        "Qualquer dúvida, estamos à disposição."
    )
    texto_rodape = _montar_rodape(membro.guild)

    url_avatar = _url_do_avatar(membro)
    componentes: list = []

    if url_avatar:
        componentes.append(
            discord.ui.Section(
                texto_titulo,
                accessory=discord.ui.Thumbnail(url_avatar),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(texto_titulo))

    componentes.append(discord.ui.TextDisplay(texto_corpo))
    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(discord.ui.TextDisplay(texto_rodape))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            *componentes,
            accent_color=discord.Color.green(),
        )
    )
    return view


def montar_card_adeus(
    membro: discord.Member | discord.User,
    guilda: discord.Guild | None,
) -> discord.ui.LayoutView:
    """
    Monta o card de adeus para um membro que saiu do servidor.

    Aceita Member ou User porque, no leave, o Discord às vezes entrega
    dados parciais. A thumbnail continua sendo o avatar da pessoa.
    """
    id_do_membro = membro.id
    nome_base = getattr(membro, "name", None) or "desconhecido"
    mencao = f"<@{id_do_membro}>"

    texto_titulo = (
        "# 👋 Até logo! — alguém saiu do servidor\n"
        f"➜ **Usuário:** {mencao} (`{id_do_membro}`)\n"
        f"➜ **Nome de usuário:** `@{nome_base}`"
    )
    texto_corpo = "😢 Sentiremos sua falta por aqui.\nEsperamos vê-lo(a) novamente em breve. Boa sorte em sua jornada!"
    texto_rodape = _montar_rodape(guilda)

    url_avatar = _url_do_avatar(membro)
    componentes: list = []

    if url_avatar:
        componentes.append(
            discord.ui.Section(
                texto_titulo,
                accessory=discord.ui.Thumbnail(url_avatar),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(texto_titulo))

    componentes.append(discord.ui.TextDisplay(texto_corpo))
    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(discord.ui.TextDisplay(texto_rodape))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            *componentes,
            accent_color=discord.Color.dark_grey(),
        )
    )
    return view
