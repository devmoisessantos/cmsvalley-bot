# src/utils/log_container.py
"""
Container visual padrão para logs no Discord (Components V2).

Use LogContainerView quando for enviar um log em um canal.
Use criar_container_log se precisar só do Container (sem a View).
"""

from datetime import (
    datetime,
    timezone,
)

import discord

from src.utils.mensagens import COR_INFO


def criar_container_log(
    titulo: str,
    linhas: str,
    guilda: discord.Guild,
    cor: discord.Color = COR_INFO,
    url_do_avatar: str | None = None,
    urls_de_midia: list[str] | None = None,
    url_do_link: str | None = None,
    rotulo_do_link: str = "Abrir mensagem",
    blocos_extra: list[str] | None = None,
) -> discord.ui.Container:
    """
    Monta um Container de log com título, corpo, rodapé e mídia opcional.

    - titulo: texto grande no topo
    - linhas: corpo principal (markdown)
    - blocos_extra: textos extras separados por Separator large
    - url_do_link: se existir, adiciona botão de link no final
    """
    momento_atual = int(datetime.now(timezone.utc).timestamp())
    texto_do_rodape = f"-# {guilda.name} • <t:{momento_atual}:f>"

    componentes: list = [discord.ui.TextDisplay(f"# {titulo}")]

    if url_do_avatar:
        componentes.append(
            discord.ui.Section(
                linhas,
                accessory=discord.ui.Thumbnail(url_do_avatar),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(linhas))

    if blocos_extra:
        for bloco in blocos_extra:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
            )
            componentes.append(discord.ui.TextDisplay(bloco))

    if urls_de_midia:
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        itens_da_galeria = [
            discord.MediaGalleryItem(url_da_midia) for url_da_midia in urls_de_midia
        ]
        componentes.append(discord.ui.MediaGallery(*itens_da_galeria))

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(discord.ui.TextDisplay(texto_do_rodape))

    if url_do_link:
        linha_do_botao = discord.ui.ActionRow()
        linha_do_botao.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.link,
                label=rotulo_do_link[:80],
                url=url_do_link,
            )
        )
        componentes.append(linha_do_botao)

    return discord.ui.Container(*componentes, accent_color=cor)


class LogContainerView(discord.ui.LayoutView):
    """
    LayoutView pronta para enviar um log em um canal.

    Exemplo:
        view_do_log = LogContainerView(
            titulo="Alteração de Cargo",
            linhas="- Membro: ...",
            guild=guilda,
        )
        await canal.send(view=view_do_log)
    """

    def __init__(
        self,
        titulo: str,
        linhas: str,
        guild: discord.Guild,
        cor: discord.Color = COR_INFO,
        avatar_url: str | None = None,
        midia_urls: list[str] | None = None,
        link_url: str | None = None,
        link_label: str = "Abrir mensagem",
        blocos_extra: list[str] | None = None,
    ):
        super().__init__(timeout=None)

        container_do_log = criar_container_log(
            titulo=titulo,
            linhas=linhas,
            guilda=guild,
            cor=cor,
            url_do_avatar=avatar_url,
            urls_de_midia=midia_urls,
            url_do_link=link_url,
            rotulo_do_link=link_label,
            blocos_extra=blocos_extra,
        )
        self.add_item(container_do_log)
