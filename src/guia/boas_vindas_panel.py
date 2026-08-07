# src/guia/boas_vindas_panel.py
"""
Painel de boas-vindas — Categoria 1: Preparação Inicial (Guia do Estagiário).

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
from src.utils.mensagens import (
    responder_erro,
    responder_view,
)

# Conteúdo de cada opção do select
OPCOES_DO_GUIA: dict[str, dict] = {
    "uniforme": {
        "emoji": "👕",
        "titulo": "Uniforme de Estagiário",
        "texto": (
            "Antes de começar qualquer atividade, **vista o uniforme de Estagiário**.\n\n"
            "Isso identifica você como membro em formação do CMS Valley e é "
            "obrigatório para atuar no hospital."
        ),
        "chave_do_canal": "GUIA_UNIFORME",
        "rotulo_do_botao": "Abrir canal do uniforme",
    },
    "regras": {
        "emoji": "📜",
        "titulo": "Regras do Hospital Sul",
        "texto": (
            "Esteja **ciente das regras do Hospital Sul** antes de entrar em serviço.\n\n"
            "O descumprimento pode gerar advertências. Leia com atenção e tire "
            "dúvidas com a equipe se necessário."
        ),
        "chave_do_canal": "GUIA_REGRAS_HP",
        "rotulo_do_botao": "Abrir regras do HP",
    },
    "regras_bau": {
        "emoji": "🧰",
        "titulo": "Regras do Baú",
        "texto": (
            "Conheça os **limites diários de retirada do baú** do Hospital.\n\n"
            "Respeitar essas regras evita punições e garante que o estoque "
            "continue disponível para toda a equipe."
        ),
        "chave_do_canal": "GUIA_REGRAS_BAU",
        "rotulo_do_botao": "Abrir regras do baú",
    },
    "parceiros": {
        "emoji": "🤝",
        "titulo": "Parceiros",
        "texto": (
            "É importante saber das **parcerias e benefícios** do CMS Valley.\n\n"
            "Conhecer os parceiros ajuda no dia a dia e no atendimento aos pacientes."
        ),
        "chave_do_canal": "GUIA_PARCEIROS",
        "rotulo_do_botao": "Abrir canal de parceiros",
    },
    "tutoriais": {
        "emoji": "📖",
        "titulo": "Tutoriais",
        "texto": (
            "Sempre que esquecer algo, o **tutorial é o melhor amigo**.\n\n"
            "Consulte o canal de tutoriais para revisar procedimentos, binds e "
            "fluxos do hospital."
        ),
        "chave_do_canal": "GUIA_TUTORIAIS",
        "rotulo_do_botao": "Abrir tutoriais",
    },
    "duvidas": {
        "emoji": "🎫",
        "titulo": "Dúvidas",
        "texto": (
            "Ficou com alguma dúvida?\n\n"
            "Abra um **ticket** com a equipe. Estamos aqui para ajudar você "
            "nos primeiros passos no CMS Valley."
        ),
        "chave_do_canal": "GUIA_DUVIDAS_TICKET",
        "rotulo_do_botao": "Abrir canal de tickets",
    },
}


def montar_resposta_da_opcao(chave_da_opcao: str) -> discord.ui.LayoutView:
    """Monta a resposta ephemeral (Components V2) da opção escolhida no select."""
    dados_da_opcao = OPCOES_DO_GUIA[chave_da_opcao]
    id_do_canal = CANAIS.get(dados_da_opcao["chave_do_canal"]) or 0

    componentes: list = [
        discord.ui.TextDisplay(
            f"# {dados_da_opcao['emoji']} {dados_da_opcao['titulo']}\n\n"
            f"{dados_da_opcao['texto']}"
        ),
    ]

    if id_do_canal:
        linha_do_botao = discord.ui.ActionRow()
        linha_do_botao.add_item(
            discord.ui.Button(
                label=dados_da_opcao["rotulo_do_botao"],
                style=discord.ButtonStyle.link,
                url=f"https://discord.com/channels/{GUILD_ID}/{id_do_canal}",
                emoji="🔗",
            )
        )
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(linha_do_botao)
    else:
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        componentes.append(
            discord.ui.TextDisplay(
                "-# Canal ainda não configurado. "
                "Avise a staff se o link estiver ausente."
            )
        )

    container = discord.ui.Container(
        *componentes,
        accent_color=discord.Color.green(),
    )
    view_da_resposta = discord.ui.LayoutView(timeout=180)
    view_da_resposta.add_item(container)
    return view_da_resposta


class PainelBoasVindasLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente — primeiros passos do Enfermeiro / Estagiário."""

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        url_do_icone = guild.icon.url if guild.icon else None
        thumbnail_do_servidor = (
            discord.ui.Thumbnail(url_do_icone) if url_do_icone else None
        )

        select_do_guia = discord.ui.Select(
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
        select_do_guia.callback = self._ao_selecionar_opcao

        linha_do_select = discord.ui.ActionRow()
        linha_do_select.add_item(select_do_guia)

        componentes: list = [
            discord.ui.Section(
                "# 🏥 Centro Médico Sul | CMS Valley",
                "> 📚 Guia do Estagiário – **primeiros passos** Enfermeiro(a).",
                accessory=thumbnail_do_servidor,
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                "Obrigado por querer se juntar ao **CMS Valley**.\n\n"
                "Para que você se sinta seguro(a) e preparado(a) desde o "
                "primeiro minuto, criamos este painel com tudo o que você "
                "precisa saber antes de colocar o uniforme.\n"
                "Não pule as etapas! Cada item aqui foi pensado para garantir "
                "a sua melhor experiência em sua integração.\n\n"
            ),
        ]

        urls_da_galeria = [url for url in GUIA_BOAS_VINDAS_GALLERY if url]
        if urls_da_galeria:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
            )
            componentes.append(
                discord.ui.MediaGallery(
                    *[discord.MediaGalleryItem(url) for url in urls_da_galeria[:10]]
                )
            )

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(
            discord.ui.TextDisplay("**👉 Comece sua jornada pelos tópicos abaixo: ↓**")
        )
        componentes.append(linha_do_select)

        self.container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.green(),
        )
        self.add_item(self.container)

    async def _ao_selecionar_opcao(self, interacao: discord.Interaction):
        chave_escolhida = interacao.data["values"][0]

        if chave_escolhida not in OPCOES_DO_GUIA:
            await responder_erro(
                interacao,
                titulo="Opção inválida",
                linhas=["Essa opção do guia não existe."],
            )
            return

        view_da_resposta = montar_resposta_da_opcao(chave_escolhida)
        await responder_view(interacao, view_da_resposta, ephemeral=True)
