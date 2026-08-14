"""
Painéis fixos de abertura de ticket (segmento suporte e denúncias).
"""

from __future__ import annotations

import discord

from src.config import TICKETS_CATEGORIAS
from src.tickets.tickets_service import (
    buscar_ticket_aberto_do_autor,
    criar_ticket,
)
from src.tickets.tickets_views import BotoesTicketView
from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    responder_card,
    responder_erro,
)


def _opcoes_do_segmento(segmento: str) -> list[discord.SelectOption]:
    opcoes: list[discord.SelectOption] = []
    for chave, definicao in TICKETS_CATEGORIAS.items():
        if definicao["segmento"] != segmento:
            continue
        opcoes.append(
            discord.SelectOption(
                label=definicao["rotulo"],
                value=chave,
                emoji=definicao.get("emoji") or None,
                description=f"Abrir ticket: {definicao['rotulo']}",
            )
        )
    return opcoes


class SelectCategoriaTicket(discord.ui.Select):
    """Select de categoria dentro do painel do segmento."""

    def __init__(self, segmento: str) -> None:
        opcoes = _opcoes_do_segmento(segmento)
        super().__init__(
            placeholder="Escolha a categoria do ticket…",
            min_values=1,
            max_values=1,
            options=opcoes,
            custom_id=f"ticket:select:{segmento}",
        )
        self.segmento = segmento

    async def callback(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer(ephemeral=True)

        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Só é possível abrir ticket dentro do servidor."],
            )
            return

        categoria_chave = self.values[0]
        definicao = TICKETS_CATEGORIAS.get(categoria_chave)
        if definicao is None:
            await responder_erro(
                interacao,
                titulo="Categoria inválida",
                linhas=["A categoria selecionada não existe mais."],
            )
            return

        # Evita vários tickets abertos do mesmo tipo
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

        # Mensagem inicial no canal do ticket
        view_botoes = BotoesTicketView()
        texto_abertura = (
            f"# Ticket Criado com Sucesso! 📌\n"
            f"### Todos os responsáveis pelo ticket já estão cientes da abertura\n"
            f"{membro.mention}, evite chamar alguém via DM — "
            f"basta aguardar, alguém já irá lhe atender.\n\n"
            f"**Categoria escolhida:**\n"
            f"`{definicao['emoji']} {definicao['rotulo']}`\n\n"
            f"**Descreva o motivo do contato com o máximo de detalhes possíveis.**\n"
            f"-# Opções exclusivas para o uso dos responsáveis pelo atendimento."
        )
        try:
            await canal.send(content=texto_abertura, view=view_botoes)
        except discord.HTTPException:
            pass

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


class PainelTicketSuporteLayout(discord.ui.LayoutView):
    """Painel fixo do segmento Suporte (dúvidas + revogações)."""

    def __init__(self, guilda: discord.Guild | None = None) -> None:
        super().__init__(timeout=None)
        self.guilda = guilda

        container = discord.ui.Container(
            discord.ui.TextDisplay("# 🙋 Abrir ticket — Suporte"),
            discord.ui.TextDisplay(
                "Escolha a categoria abaixo para abrir um canal privado "
                "com a equipe.\n"
                "• Suporte / Dúvidas\n"
                "• Revogar Advertência\n"
                "• Revogar Exoneração"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.ActionRow(SelectCategoriaTicket(segmento="suporte")),
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)


class PainelTicketDenunciasLayout(discord.ui.LayoutView):
    """Painel fixo do segmento Denúncias."""

    def __init__(self, guilda: discord.Guild | None = None) -> None:
        super().__init__(timeout=None)
        self.guilda = guilda

        container = discord.ui.Container(
            discord.ui.TextDisplay("# ⛔ Abrir ticket — Denúncias"),
            discord.ui.TextDisplay(
                "Escolha a categoria abaixo para abrir um canal privado "
                "com a equipe.\n"
                "• Denúncias Jogador\n"
                "• Denúncias Diretoria\n\n"
                "Descreva o ocorrido com o máximo de detalhes possível."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.ActionRow(SelectCategoriaTicket(segmento="denuncias")),
            accent_color=discord.Color.red(),
        )
        self.add_item(container)
