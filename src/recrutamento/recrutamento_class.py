# src/recrutamento/recrutamento_class.py
import discord
from datetime import datetime, timezone

from src.utils.error_handling import LoggingViewMixin

class NovoRecrutamento(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, 
                 candidato: discord.Member, 
                 recrutador: discord.Member,
                 cargo_role: discord.Role, 
                 id_fivem: str, 
                 guild: discord.Guild
        ):
        super().__init__(timeout=None)

        linhas = (
            f"- **Membro recrutado:** {candidato.mention} (`{candidato.id}`)\n"
            f"- **ID FiveM:** `{id_fivem}`\n"
            f"- **Cargo:** {cargo_role.mention}\n"
            f"- **Recrutado por:** {recrutador.mention} (`{recrutador.id}`)"
        )

        agora = int(datetime.now(timezone.utc).timestamp())
        rodape = f"-# {guild.name} • <t:{agora}:f>"

        container = discord.ui.Container(
            discord.ui.TextDisplay("# 🔴| Recrutamento Realizado!\n\n"),
            discord.ui.Section(linhas, accessory=discord.ui.Thumbnail(candidato.display_avatar.url)),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(rodape),
            accent_color=discord.Color.red(),
        )
        self.add_item(container)

