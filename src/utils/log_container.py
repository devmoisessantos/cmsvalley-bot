import discord
from datetime import datetime, timezone


def criar_container_log(
        titulo: str,
        linhas: str,
        guild: discord.Guild,
        cor: discord.Color = discord.Color.blurple(),
        avatar_url: str | None = None,
        midia_urls: list[str] | None = None) -> discord.ui.Container:  # 👈 novo parâmetro

    agora = int(datetime.now(timezone.utc).timestamp())
    rodape_texto = f"-# {guild.name} • <t:{agora}:f>"

    componentes = [discord.ui.TextDisplay(f"# {titulo}\n")]

    if avatar_url:
        componentes.append(discord.ui.Section(linhas, accessory=discord.ui.Thumbnail(avatar_url)))
    else:
        componentes.append(discord.ui.TextDisplay(linhas))

    if midia_urls:
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        galeria = discord.ui.MediaGallery(*[discord.MediaGalleryItem(url) for url in midia_urls])
        componentes.append(galeria)

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(discord.ui.TextDisplay(rodape_texto))

    return discord.ui.Container(*componentes, accent_color=cor)


class LogContainerView(discord.ui.LayoutView):
    def __init__(self, titulo: str, linhas: str, guild: discord.Guild,
                 cor: discord.Color = discord.Color.blurple(), avatar_url: str | None = None,
                 midia_urls: list[str] | None = None):  # 👈 novo parâmetro
        super().__init__(timeout=None)
        self.add_item(criar_container_log(titulo, linhas, guild, cor, avatar_url, midia_urls))

