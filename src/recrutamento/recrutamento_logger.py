# src/recrutamento/recrutamento_logs.py
"""
Os cards de log do recrutamento, publicados no canal da equipe.

Sao dois formatos, porque um recrutamento pode nascer de dois jeitos:
- `NovoRecrutamentoLog`: o candidato entrou pelo fluxo normal do painel.
- `NovoRecrutamentoManualLog`: alguem da equipe cadastrou na mao.

Guardar essa diferenca no log importa: se der problema depois, a equipe sabe se
o cadastro passou pelas conferencias automaticas ou nao.
"""

from datetime import (
    datetime,
    timezone,
)

import discord

from src.utils.error_handling import LoggingViewMixin


class NovoRecrutamentoLog(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        candidato: discord.Member,
        recrutador: discord.Member,
        cargo_role: discord.Role,
        id_fivem: str,
        guild: discord.Guild,
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
            discord.ui.TextDisplay("# 🔴| Novo Recrutamento Iniciado!\n\n"),
            discord.ui.Section(
                linhas, accessory=discord.ui.Thumbnail(candidato.display_avatar.url)
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(rodape),
            accent_color=discord.Color.red(),
        )
        self.add_item(container)


class NovoRecrutamentoManualLog(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        candidato: discord.Member,
        recrutador: discord.Member,
        executor: discord.Member,
        cargo_role: discord.Role,
        id_fivem: str,
        guild: discord.Guild,
    ):
        super().__init__(timeout=None)

        linhas = (
            f"- **Membro recrutado:** {candidato.mention} (`{candidato.id}`)\n"
            f"- **ID FiveM:** `{id_fivem}`\n"
            f"- **Cargo:** {cargo_role.mention}\n"
            f"- **Recrutado por:** {recrutador.mention} (`{recrutador.id}`)\n"
            f"- **Registrado manualmente por:** {executor.mention} (`{executor.id}`)"
        )

        agora = int(datetime.now(timezone.utc).timestamp())
        rodape = f"-# {guild.name} • <t:{agora}:f>"

        container = discord.ui.Container(
            discord.ui.TextDisplay("# 🟠| Recrutamento Manual Registrado!\n\n"),
            discord.ui.Section(
                linhas, accessory=discord.ui.Thumbnail(candidato.display_avatar.url)
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(rodape),
            accent_color=discord.Color.orange(),
        )
        self.add_item(container)
