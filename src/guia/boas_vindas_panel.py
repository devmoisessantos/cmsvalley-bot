# src/guia/boas_vindas_panel.py
"""
Painel de boas-vindas — Categoria 1: Preparação Inicial (Guia do Estagiário).

Components V2: LayoutView + Container + Section/Thumbnail + MediaGallery + Select.
Cada opção redireciona o membro ao canal correspondente (botão de link).
"""

from __future__ import annotations

import discord

from src.config import GUIA_BOAS_VINDAS_GALLERY
from src.guia.guia_helpers import (
    buscar_id_do_canal,
    montar_botao_link,
    montar_thumbnail_do_servidor,
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
        "descricao": "Vestir o uniforme de estagiário",
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
        "descricao": "Regras do hospital antes do serviço",
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
        "descricao": "Limites diários de retirada do baú",
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
        "descricao": "Parcerias e benefícios do CMS Valley",
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
        "descricao": "Procedimentos, binds e fluxos",
        "texto": (
            "Sempre que esquecer algo, o **tutorial é o melhor amigo**.\n\n"
            "Consulte o canal de tutoriais para revisar procedimentos, binds e "
            "fluxos do hospital."
        ),
        "chave_do_canal": "GUIA_TUTORIAIS",
        "rotulo_do_botao": "Abrir canal de tutoriais",
    },
    "duvidas": {
        "emoji": "🎫",
        "titulo": "Dúvidas",
        "descricao": "Abrir ticket com a equipe",
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
    """
    Monta a resposta ephemeral (Components V2) da opção escolhida no select.

    Mostra o texto explicativo e um botão de link para o canal do tópico.
    """
    dados_da_opcao = OPCOES_DO_GUIA[chave_da_opcao]

    componentes: list = [
        discord.ui.TextDisplay(
            f"# {dados_da_opcao['emoji']} {dados_da_opcao['titulo']}\n\n"
            f"{dados_da_opcao['texto']}"
        ),
    ]

    chave_do_canal = dados_da_opcao["chave_do_canal"]
    canal_esta_configurado = buscar_id_do_canal(chave_do_canal) > 0

    if canal_esta_configurado:
        botao_de_link = montar_botao_link(
            rotulo=dados_da_opcao["rotulo_do_botao"],
            chave_do_canal=chave_do_canal,
        )
        linha_do_botao = discord.ui.ActionRow()
        linha_do_botao.add_item(botao_de_link)

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

        thumbnail_do_servidor = montar_thumbnail_do_servidor(guild)

        opcoes_do_select = [
            discord.SelectOption(
                label=dados["titulo"][:100],
                value=chave,
                description=dados.get("descricao", "")[:100],
                emoji=dados["emoji"],
            )
            for chave, dados in OPCOES_DO_GUIA.items()
        ]

        select_do_guia = discord.ui.Select(
            placeholder="📍 Escolha um primeiro passo…",
            custom_id="guia:boas_vindas_select",
            options=opcoes_do_select,
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
        galeria_tem_imagens = len(urls_da_galeria) > 0
        if galeria_tem_imagens:
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

        opcao_existe = chave_escolhida in OPCOES_DO_GUIA
        if not opcao_existe:
            await responder_erro(
                interacao,
                titulo="Opção inválida",
                linhas=["Essa opção do guia não existe."],
            )
            return

        view_da_resposta = montar_resposta_da_opcao(chave_escolhida)
        await responder_view(interacao, view_da_resposta, ephemeral=True)
