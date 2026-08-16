"""
Painéis fixos de abertura de ticket (segmento suporte e denúncias).

Usa botões (não select) para cada categoria.
"""

from __future__ import annotations

import discord

from src.config import TICKETS_CATEGORIAS
from src.tickets.tickets_service import (
    buscar_ticket_aberto_do_autor,
    criar_ticket,
    membro_tem_cargo_obrigatorio_ticket,
)
from src.tickets.tickets_views import enviar_mensagens_abertura_ticket
from src.utils.mensagens import (
    COR_AVISO,
    COR_SUCESSO,
    responder_card,
    responder_erro,
)


def _montar_linha_botao_canal(canal: discord.TextChannel) -> discord.ui.ActionRow:
    """Botão de link que leva direto ao canal do ticket."""
    linha = discord.ui.ActionRow()
    linha.add_item(
        discord.ui.Button(
            label="Ir para o canal do ticket",
            style=discord.ButtonStyle.link,
            url=canal.jump_url,
            emoji="🔗",
        )
    )
    return linha


async def abrir_ticket_por_categoria(
    interacao: discord.Interaction,
    categoria_chave: str,
) -> None:
    """
    Fluxo comum: valida (1 aberto + cargos), cria canal e envia cards de abertura.
    """
    if interacao.response.is_done() is False:
        await interacao.response.defer(ephemeral=True)

    membro = interacao.user
    if not isinstance(membro, discord.Member):
        await responder_erro(
            interacao,
            titulo="Erro",
            linhas=["Só é possível abrir ticket dentro do servidor."],
        )
        return

    definicao = TICKETS_CATEGORIAS.get(categoria_chave)
    if definicao is None:
        await responder_erro(
            interacao,
            titulo="Categoria inválida",
            linhas=["A categoria selecionada não existe mais."],
        )
        return

    # Trava de cargo (revogar_adv / revogar_exo etc.)
    if not membro_tem_cargo_obrigatorio_ticket(membro, definicao):
        nomes = definicao.get("cargos_obrigatorios") or []
        lista_legivel = ", ".join(f"`{nome.strip()}`" for nome in nomes) or "—"
        await responder_erro(
            interacao,
            titulo="Sem permissão para esta categoria",
            linhas=[
                f"Para abrir **{definicao['emoji']} {definicao['rotulo']}** "
                "você precisa ter um destes cargos:",
                lista_legivel,
            ],
        )
        return

    # Apenas 1 ticket aberto por membro (qualquer categoria)
    ticket_existente = await buscar_ticket_aberto_do_autor(
        autor_discord_id=membro.id,
        categoria_chave=None,
    )
    if ticket_existente is not None:
        await responder_card(
            interacao,
            titulo="Você já tem um ticket aberto",
            linhas=[
                f"Categoria: **{ticket_existente.categoria_rotulo}**",
                f"Canal: <#{ticket_existente.canal_id}>",
                "Só é permitido **1 ticket aberto** por vez.",
                "Finalize o atual antes de abrir outro (em qualquer categoria).",
            ],
            cor=COR_AVISO,
            extra_row=_montar_linha_botao_canal_id(
                ticket_existente.canal_id, interacao.guild
            ),
        )
        return

    guilda = interacao.guild
    if guilda is None:
        await responder_erro(
            interacao,
            titulo="Erro",
            linhas=["Guilda não encontrada."],
        )
        return

    resultado = await criar_ticket(guilda, membro, categoria_chave)
    if resultado is None:
        await responder_erro(
            interacao,
            titulo="Falha ao criar ticket",
            linhas=[
                "Não foi possível criar o canal.",
                "Verifique se as categorias estão configuradas corretamente.",
            ],
        )
        return

    ticket, canal = resultado

    try:
        await enviar_mensagens_abertura_ticket(
            canal=canal,
            autor=membro,
            definicao=definicao,
            ticket_id=ticket.id,
        )
    except discord.HTTPException as erro_envio:
        print(f"⚠️ Falha ao enviar cards de abertura do ticket: {erro_envio}")

    await responder_card(
        interacao,
        titulo="Ticket aberto",
        linhas=[
            f"Categoria: **{definicao['emoji']} {definicao['rotulo']}**",
            f"Canal: {canal.mention}",
            "Descreva o ocorrido no canal que acabamos de criar.",
        ],
        cor=COR_SUCESSO,
        extra_row=_montar_linha_botao_canal(canal),
        delay=None,
    )


def _montar_linha_botao_canal_id(
    canal_id: int,
    guilda: discord.Guild | None,
) -> discord.ui.ActionRow | None:
    """Monta botão de link quando ainda temos só o ID do canal."""
    if guilda is None:
        return None
    canal = guilda.get_channel(int(canal_id))
    if canal is None or not isinstance(canal, discord.TextChannel):
        return None
    return _montar_linha_botao_canal(canal)


class PainelTicketSuporteLayout(discord.ui.LayoutView):
    """Painel fixo do segmento Suporte (dúvidas + revogações)."""

    def __init__(self, guilda: discord.Guild | None = None) -> None:
        super().__init__(timeout=None)
        self.guilda = guilda

        componentes: list = []

        # Bloco 1 — título com ícone da guilda
        url_icone = guilda.icon.url if guilda and guilda.icon else None
        texto_titulo = (
            "# 🎫 Sistema de Tickets — Suporte\n"
            "> **Precisa de ajuda?** Escolha uma das opções abaixo para abrir "
            "um canal privado com nossa equipe."
        )
        if url_icone:
            componentes.append(
                discord.ui.Section(
                    texto_titulo,
                    accessory=discord.ui.Thumbnail(url_icone),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(texto_titulo))

        # Bloco 2 — separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Bloco 3 — categorias disponíveis
        componentes.append(
            discord.ui.TextDisplay(
                "## 📋 Categorias Disponíveis\n\n"
                "- `💬` **Suporte / Dúvidas**: Tire dúvidas ou solicite ajuda "
                "com qualquer assunto\n"
                "- `⚠️` **Revogar Advertência**: Solicite a revisão de uma "
                "advertência aplicada\n"
                "- `🔄` **Revogar Exoneração**: Peça a revisão de um processo "
                "de exoneração"
            )
        )

        # Bloco 4 — separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Bloco 5 — como funciona
        componentes.append(
            discord.ui.TextDisplay(
                "### `❓` __Como funciona__\n"
                "**1.** Clique na categoria desejada\n"
                "**2.** Um canal privado será criado automaticamente\n"
                "**3.** Nossa equipe responderá o mais breve possível"
            )
        )

        # Bloco 6 — separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Bloco 7 — botões (custom_id = ticket:abrir:<chave>)
        linha = discord.ui.ActionRow()
        linha.add_item(
            discord.ui.Button(
                label="Suporte / Dúvidas",
                emoji="🙋",
                style=discord.ButtonStyle.secondary,
                custom_id="ticket:abrir:suporte_duvidas",
            )
        )
        linha.add_item(
            discord.ui.Button(
                label="Revogar Advertência",
                emoji="✏️",
                style=discord.ButtonStyle.danger,
                custom_id="ticket:abrir:revogar_adv",
            )
        )
        linha.add_item(
            discord.ui.Button(
                label="Revogar Exoneração",
                emoji="🚫",
                style=discord.ButtonStyle.danger,
                custom_id="ticket:abrir:revogar_exo",
            )
        )
        componentes.append(linha)

        container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)


class PainelTicketDenunciasLayout(discord.ui.LayoutView):
    """Painel fixo do segmento Denúncias."""

    def __init__(self, guilda: discord.Guild | None = None) -> None:
        super().__init__(timeout=None)
        self.guilda = guilda

        componentes: list = []

        # Bloco 1 — título com ícone da guilda
        url_icone = guilda.icon.url if guilda and guilda.icon else None
        texto_titulo = (
            "# 🎫 Sistema de Tickets — Denúncias\n"
            "> **Identificou alguma irregularidade?** Escolha uma das opções "
            "abaixo para abrir um canal privado com nossa equipe."
        )
        if url_icone:
            componentes.append(
                discord.ui.Section(
                    texto_titulo,
                    accessory=discord.ui.Thumbnail(url_icone),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(texto_titulo))

        # Bloco 2 — separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Bloco 3 — categorias disponíveis
        componentes.append(
            discord.ui.TextDisplay(
                "## 📋 Categorias Disponíveis\n\n"
                "- `👤` **Denúncias Jogador**: Reporte comportamentos "
                "inadequados de jogadores\n"
                "- `🏢` **Denúncias Diretoria**: Reporte irregularidades "
                "envolvendo a diretoria"
            )
        )

        # Bloco 4 — separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Bloco 5 — como fazer a denúncia
        componentes.append(
            discord.ui.TextDisplay(
                "### ✏️ __Como fazer sua denúncia__\n"
                "-# Para que possamos analisar seu caso com eficiência, inclua:\n\n"
                "- **Descrição detalhada** dos fatos\n"
                "- **Provas disponíveis** (prints, vídeos, links)\n\n"
                "> `⚠️` **Importante:** Denúncias sem informações suficientes "
                "podem ter o processo atrasado."
            )
        )

        # Bloco 6 — separador
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Bloco 7 — botões
        linha = discord.ui.ActionRow()
        linha.add_item(
            discord.ui.Button(
                label="Denúnciar Jogador",
                emoji="⛔",
                style=discord.ButtonStyle.danger,
                custom_id="ticket:abrir:denuncias_jogador",
            )
        )
        linha.add_item(
            discord.ui.Button(
                label="Denúnciar Diretoria",
                emoji="🛡️",
                style=discord.ButtonStyle.primary,
                custom_id="ticket:abrir:denuncias_diretoria",
            )
        )
        componentes.append(linha)

        container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)


async def processar_clique_abrir_ticket(interacao: discord.Interaction) -> None:
    """Roteia botões ticket:abrir:<categoria_chave> do painel."""
    custom_id = ""
    if interacao.data:
        custom_id = interacao.data.get("custom_id") or ""

    if not custom_id.startswith("ticket:abrir:"):
        return

    categoria_chave = custom_id.removeprefix("ticket:abrir:")
    await abrir_ticket_por_categoria(interacao, categoria_chave)
