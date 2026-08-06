"""Painel de boas-vindas — Categoria 1: Preparação Inicial (Guia do Estagiário).

Components V2: LayoutView + Container + Section/Thumbnail + MediaGallery + Select.
"""

from __future__ import annotations

import discord

from src.config import (
    CANAIS,
    GUIA_BOAS_VINDAS_GALLERY,
    GUILD_ID,
)
from src.utils.error_handling import LoggingViewMixin

# ── Conteúdo de cada opção do select ─────────────────────────────────────

_OPCOES_GUIA: dict[str, dict] = {
    "uniforme": {
        "emoji": "👕",
        "titulo": "Uniforme de Estagiário",
        "texto": (
            "Antes de começar qualquer atividade, **vista o uniforme de Estagiário**.\n\n"
            "Isso identifica você como membro em formação do CMS Valley e é "
            "obrigatório para atuar no hospital."
        ),
        "canal_key": "GUIA_UNIFORME",
        "label_botao": "Abrir canal do uniforme",
    },
    "regras": {
        "emoji": "📜",
        "titulo": "Regras do Hospital Sul",
        "texto": (
            "Esteja **ciente das regras do Hospital Sul** antes de entrar em serviço.\n\n"
            "O descumprimento pode gerar advertências. Leia com atenção e tire "
            "dúvidas com a equipe se necessário."
        ),
        "canal_key": "GUIA_REGRAS_HP",
        "label_botao": "Abrir regras do HP",
    },
    "regras_bau": {
        "emoji": "🧰",
        "titulo": "Regras do Baú",
        "texto": (
            "Conheça os **limites diários de retirada do baú** do Hospital.\n\n"
            "Respeitar essas regras evita punições e garante que o estoque "
            "continue disponível para toda a equipe."
        ),
        "canal_key": "GUIA_REGRAS_BAU",
        "label_botao": "Abrir regras do baú",
    },
    "parceiros": {
        "emoji": "🤝",
        "titulo": "Parceiros",
        "texto": (
            "É importante saber das **parcerias e benefícios** do CMS Valley.\n\n"
            "Conhecer os parceiros ajuda no dia a dia e no atendimento aos pacientes."
        ),
        "canal_key": "GUIA_PARCEIROS",
        "label_botao": "Abrir canal de parceiros",
    },
    "tutoriais": {
        "emoji": "📖",
        "titulo": "Tutoriais",
        "texto": (
            "Sempre que esquecer algo, o **tutorial é o melhor amigo**.\n\n"
            "Consulte o canal de tutoriais para revisar procedimentos, binds e "
            "fluxos do hospital."
        ),
        "canal_key": "GUIA_TUTORIAIS",
        "label_botao": "Abrir tutoriais",
    },
    "duvidas": {
        "emoji": "🎫",
        "titulo": "Dúvidas",
        "texto": (
            "Ficou com alguma dúvida?\n\n"
            "Abra um **ticket** com a equipe. Estamos aqui para ajudar você "
            "nos primeiros passos no CMS Valley."
        ),
        "canal_key": "GUIA_DUVIDAS_TICKET",
        "label_botao": "Abrir canal de tickets",
    },
}


def _montar_resposta_opcao(chave: str) -> discord.ui.LayoutView:
    """Resposta ephemeral em Components V2 para a opção escolhida."""
    dados = _OPCOES_GUIA[chave]
    canal_id = CANAIS.get(dados["canal_key"]) or 0

    componentes: list = [
        discord.ui.TextDisplay(
            f"# {dados['emoji']} {dados['titulo']}\n\n{dados['texto']}"
        ),
    ]

    if canal_id:
        row = discord.ui.ActionRow()
        row.add_item(
            discord.ui.Button(
                label=dados["label_botao"],
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/channels/{GUILD_ID}/{canal_id}",
                emoji="🔗",
            )
        )
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(row)
    else:
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        componentes.append(
            discord.ui.TextDisplay(
                "-# Canal ainda não configurado. Avise a staff se o link estiver ausente."
            )
        )

    container = discord.ui.Container(
        *componentes,
        accent_color=discord.Color.green(),
    )
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(container)
    return view


class PainelBoasVindasLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente — primeiros passos do Enfermeiro / Estagiário."""

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        icon_url = guild.icon.url if guild.icon else None

        # Select menu
        select = discord.ui.Select(
            placeholder="📍 Escolha um primeiro passo…",
            custom_id="guia:boas_vindas_select",
            options=[
                discord.SelectOption(
                    label="Uniforme",
                    value="uniforme",
                    description="Vestir o uniforme de estagiário",
                    emoji="👕",
                ),
                discord.SelectOption(
                    label="Regras",
                    value="regras",
                    description="Esteja ciente das regras do Hospital Sul",
                    emoji="📜",
                ),
                discord.SelectOption(
                    label="Regras do Baú",
                    value="regras_bau",
                    description="Limites diários de retirada do baú",
                    emoji="🧰",
                ),
                discord.SelectOption(
                    label="Parceiros",
                    value="parceiros",
                    description="Parcerias e benefícios do CMS Valley",
                    emoji="🤝",
                ),
                discord.SelectOption(
                    label="Tutoriais",
                    value="tutoriais",
                    description="O tutorial é o melhor amigo",
                    emoji="📖",
                ),
                discord.SelectOption(
                    label="Dúvidas",
                    value="duvidas",
                    description="Abrir ticket com a equipe",
                    emoji="🎫",
                ),
            ],
        )
        select.callback = self._on_select
        row_select = discord.ui.ActionRow()
        row_select.add_item(select)

        # Corpo do painel
        componentes: list = [
            discord.ui.Section(
                "# Centro Médico Sul | CMS Valley",
                ("> Aqui estão os **primeiros passos** Enfermeiro(a)."),
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                "Obrigado por querer se juntar ao **CMS Valley**.\n\n"
                "Este painel é o seu **ponto de partida**: aqui você encontra "
                "tudo o que precisa fazer **antes de começar** a atuar no hospital.\n\n"
            ),
        ]

        # MediaGallery (só se houver URLs configuradas)
        gallery_urls = [u for u in GUIA_BOAS_VINDAS_GALLERY if u]
        if gallery_urls:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
            )
            componentes.append(
                discord.ui.MediaGallery(
                    *[discord.MediaGalleryItem(url) for url in gallery_urls[:10]]
                )
            )

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(
            discord.ui.TextDisplay("**Antes de começar, comece por aqui ↓**")
        )
        componentes.append(row_select)

        self.container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.green(),
        )
        self.add_item(self.container)

    async def _on_select(self, interaction: discord.Interaction):
        chave = interaction.data["values"][0]
        if chave not in _OPCOES_GUIA:
            await interaction.response.send_message(
                "❌ Opção inválida.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            view=_montar_resposta_opcao(chave),
            ephemeral=True,
        )
