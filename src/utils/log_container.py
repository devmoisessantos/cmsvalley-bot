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
) -> discord.ui.Container:
    """
    Monta um Container de log com título, corpo, rodapé e mídia opcional.

    - titulo: texto grande no topo
    - linhas: corpo do log (já formatado com markdown)
    - guilda: usada no rodapé (nome do servidor)
    - cor: cor da barra lateral do container
    - url_do_avatar: se existir, mostra thumbnail ao lado do texto
    - urls_de_midia: lista de URLs para MediaGallery (opcional)
    """
    momento_atual = int(datetime.now(timezone.utc).timestamp())
    texto_do_rodape = f"-# {guilda.name} • <t:{momento_atual}:f>"

    componentes = [discord.ui.TextDisplay(f"# {titulo}\n")]

    if url_do_avatar:
        componentes.append(
            discord.ui.Section(
                linhas,
                accessory=discord.ui.Thumbnail(url_do_avatar),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(linhas))

    if urls_de_midia:
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        itens_da_galeria = [
            discord.MediaGalleryItem(url_da_midia) for url_da_midia in urls_de_midia
        ]
        galeria = discord.ui.MediaGallery(*itens_da_galeria)
        componentes.append(galeria)

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(discord.ui.TextDisplay(texto_do_rodape))

    return discord.ui.Container(*componentes, accent_color=cor)


class LogContainerView(discord.ui.LayoutView):
    """
    LayoutView pronta para enviar um log em um canal.

    Exemplo:
        view_do_log = LogContainerView(
            titulo="Alteração de Cargo",
            linhas="- Membro: ...",
            guilda=guilda,
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
    ):
        # Os nomes dos parâmetros públicos (guild, avatar_url, midia_urls)
        # foram mantidos para não quebrar os arquivos que já usam esta classe.
        super().__init__(timeout=None)

        container_do_log = criar_container_log(
            titulo=titulo,
            linhas=linhas,
            guilda=guild,
            cor=cor,
            url_do_avatar=avatar_url,
            urls_de_midia=midia_urls,
        )
        self.add_item(container_do_log)
