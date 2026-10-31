"""
Monta o card Components V2 da mensagem de beneficios de ser booster.

Aqui só monta o visual. Quem decide quando postar e quando apagar a mensagem
antiga é pela tarefa em booster_tasks.py.
"""

from __future__ import annotations

import discord

from src.config import BOOSTER


def montar_mensagem_booster(
    servidor: discord.Guild | None,
) -> discord.ui.layoutView:
    """
    Monta o LayoutView com os beneficios de ser Bosster no Servidor do CMS.

    Usa a thumbnail do ícone do servidor quando existir; se nao só texto.
    """

    componentes: list = []

    emoji_booster = BOOSTER["EMOJI_BOOSTER"]
    texto_titulo = (
        f"# {emoji_booster} VANTAGENS DE SER BOOSTER"
        "⠀ ⠀ ⠀ ⠀ ⠀ ⠀ ⠀ ⠀ ⠀ Centro Médico Sul Valley\n"
        "> **Quais são as vantagens após usar o boost no servidor?** "
        "|| @everyone ||"
    )

    icone_do_servidor = None
    if servidor is not None and servidor.icon is not None:
        icone_do_servidor = servidor.icon.url

    if icone_do_servidor:
        componentes.append(
            discord.ui.Section(
                texto_titulo, accessory=discord.ui.Thumbnail(icone_do_servidor)
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(texto_titulo))

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    cargo_booster_id = BOOSTER["CARGO_BOOSTER_ID"]
    texto_vantagens = (
        f"## :small_blue_diamond:  CARGO EXCLUSIVO NO DISCORD: "
        f"<@&{cargo_booster_id}>\n"
        "## :small_blue_diamond:  Ganhe **1x Crédito Concessionária**\n"
        "-# ( Escolha um carro de até **R$ 1.000.000** ou valor no PIX )\n"
        "## :small_blue_diamond:  Permissão para **Alterar o Apelido** "
        "no Servidor do Hospital"
    )
    componentes.append(discord.ui.TextDisplay(texto_vantagens))

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    urls_galeria = BOOSTER.get("URLS_GALERIA") or []
    if urls_galeria:
        itens_galeria = [
            discord.MediaGalleryItem(media=url_da_imagem)
            for url_da_imagem in urls_galeria
        ]
        componentes.append(discord.ui.MediaGallery(*itens_galeria))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        item=discord.ui.Container(
            *componentes,
            accent_color=discord.Color.__hash__("#9A4EB8FD"),
        )
    )
    return view
