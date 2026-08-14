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
)
from src.tickets.tickets_views import enviar_mensagens_abertura_ticket
from src.utils.mensagens import (
    COR_AVISO,
    COR_SUCESSO,
    responder_card,
    responder_erro,
)


async def abrir_ticket_por_categoria(
    interacao: discord.Interaction,
    categoria_chave: str,
) -> None:
    """
    Fluxo comum: valida, cria canal e envia os 3 cards de abertura.
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

    ticket_existente = await buscar_ticket_aberto_do_autor(
        autor_discord_id=membro.id,
        categoria_chave=categoria_chave,
    )
    if ticket_existente is not None:
        await responder_card(
            interacao,
            titulo="Você já tem um ticket aberto",
            linhas=[
                f"Categoria: **{ticket_existente.categoria_rotulo}**",
                f"Canal: <#{ticket_existente.canal_id}>",
                "Finalize o ticket atual antes de abrir outro nesta categoria.",
            ],
            cor=COR_AVISO,
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
    )


def _montar_linhas_botoes_categoria(segmento: str) -> list[discord.ui.ActionRow]:
    """Monta ActionRows com um botão por categoria do segmento."""
    linhas: list[discord.ui.ActionRow] = []
    linha_atual = discord.ui.ActionRow()
    contador = 0

    for chave, definicao in TICKETS_CATEGORIAS.items():
        if definicao["segmento"] != segmento:
            continue

        botao = discord.ui.Button(
            label=definicao["rotulo"],
            emoji=definicao.get("emoji") or None,
            style=discord.ButtonStyle.secondary,
            custom_id=f"ticket:abrir:{chave}",
        )
        linha_atual.add_item(botao)
        contador += 1

        # Discord: no máximo 5 botões por ActionRow
        if contador % 5 == 0:
            linhas.append(linha_atual)
            linha_atual = discord.ui.ActionRow()

    if len(linha_atual.children) > 0:
        linhas.append(linha_atual)

    return linhas


class PainelTicketSuporteLayout(discord.ui.LayoutView):
    """Painel fixo do segmento Suporte (dúvidas + revogações)."""

    def __init__(self, guilda: discord.Guild | None = None) -> None:
        super().__init__(timeout=None)
        self.guilda = guilda

        componentes: list = [
            discord.ui.TextDisplay("# 🙋 Abrir ticket — Suporte"),
            discord.ui.TextDisplay(
                "Clique na categoria desejada para abrir um canal privado "
                "com a equipe.\n"
                "• Suporte / Dúvidas\n"
                "• Revogar Advertência\n"
                "• Revogar Exoneração"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]
        for linha in _montar_linhas_botoes_categoria("suporte"):
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

        componentes: list = [
            discord.ui.TextDisplay("# ⛔ Abrir ticket — Denúncias"),
            discord.ui.TextDisplay(
                "Clique na categoria desejada para abrir um canal privado "
                "com a equipe.\n"
                "• Denúncias Jogador\n"
                "• Denúncias Diretoria\n\n"
                "Descreva o ocorrido com o máximo de detalhes possível."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]
        for linha in _montar_linhas_botoes_categoria("denuncias"):
            componentes.append(linha)

        container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.red(),
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
