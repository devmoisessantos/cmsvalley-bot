"""
Views persistentes dos tickets: botões de staff (Assumir / Finalizar).
"""

from __future__ import annotations

import asyncio

import discord

from src.tickets.tickets_service import (
    assumir_ticket,
    buscar_ticket_por_canal,
    coletar_mensagens_do_canal,
    finalizar_ticket,
    membro_eh_staff_ticket,
    montar_html_transcript,
)
from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    responder_card,
    responder_erro,
)


class BotoesTicketView(discord.ui.LayoutView):
    """
    Botões exclusivos de staff dentro do canal do ticket.

    custom_id fixo para sobreviver a reinícios do bot.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

        linha = discord.ui.ActionRow()
        linha.add_item(
            discord.ui.Button(
                label="Assumir atendimento",
                style=discord.ButtonStyle.primary,
                custom_id="ticket:assumir",
                emoji="🎫",
            )
        )
        linha.add_item(
            discord.ui.Button(
                label="Finalizar ticket",
                style=discord.ButtonStyle.danger,
                custom_id="ticket:finalizar",
                emoji="🔒",
            )
        )
        self.add_item(linha)


async def processar_clique_botao_ticket(
    interacao: discord.Interaction,
) -> None:
    """
    Roteia cliques dos botões ticket:assumir e ticket:finalizar.

    Chamado pelo listener/cog global de interações de componente.
    """
    custom_id = interacao.data.get("custom_id") if interacao.data else None
    if custom_id not in ("ticket:assumir", "ticket:finalizar"):
        return

    membro = interacao.user
    if not isinstance(membro, discord.Member):
        await responder_erro(
            interacao,
            titulo="Erro",
            linhas=["Esta ação só funciona dentro do servidor."],
        )
        return

    if not membro_eh_staff_ticket(membro):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Apenas a equipe de tickets pode usar estes botões."],
        )
        return

    canal = interacao.channel
    if not isinstance(canal, discord.TextChannel):
        await responder_erro(
            interacao,
            titulo="Erro",
            linhas=["Canal inválido."],
        )
        return

    ticket = await buscar_ticket_por_canal(canal.id)
    if ticket is None:
        await responder_erro(
            interacao,
            titulo="Ticket não encontrado",
            linhas=["Este canal não está registrado como ticket ativo."],
        )
        return

    if custom_id == "ticket:assumir":
        await _tratar_assumir(interacao, ticket, membro, canal)
        return

    if custom_id == "ticket:finalizar":
        await _tratar_finalizar(interacao, ticket, membro, canal)
        return


async def _tratar_assumir(
    interacao: discord.Interaction,
    ticket,
    membro: discord.Member,
    canal: discord.TextChannel,
) -> None:
    if ticket.status == "finalizado":
        await responder_erro(
            interacao,
            titulo="Ticket já finalizado",
            linhas=["Este ticket já foi encerrado."],
        )
        return

    if ticket.status == "assumido" and ticket.staff_assumiu_id == membro.id:
        await responder_card(
            interacao,
            titulo="Já é seu",
            linhas=["Você já assumiu este atendimento."],
            cor=COR_INFO,
        )
        return

    ticket_atualizado = await assumir_ticket(ticket, membro, canal)

    await responder_card(
        interacao,
        titulo="Atendimento assumido",
        linhas=[
            f"Staff: **{membro.display_name}**",
            f"Categoria: {ticket_atualizado.categoria_rotulo}",
            "O canal foi renomeado.",
        ],
        cor=COR_SUCESSO,
    )

    try:
        await canal.send(
            content=(
                f"🎫 **Atendimento assumido** por {membro.mention}\n"
                f"Canal atualizado para o novo nome."
            )
        )
    except discord.HTTPException:
        pass


async def _tratar_finalizar(
    interacao: discord.Interaction,
    ticket,
    membro: discord.Member,
    canal: discord.TextChannel,
) -> None:
    if ticket.status == "finalizado":
        await responder_erro(
            interacao,
            titulo="Já finalizado",
            linhas=["Este ticket já foi encerrado."],
        )
        return

    # Modal simples para considerações finais (fase 1: texto opcional via follow-up)
    await interacao.response.send_modal(ModalFinalizarTicket(ticket_id=ticket.id))


class ModalFinalizarTicket(discord.ui.Modal, title="Finalizar ticket"):
    """Coleta considerações finais antes de fechar."""

    consideracoes = discord.ui.TextInput(
        label="Considerações finais",
        style=discord.TextStyle.paragraph,
        placeholder="Resumo do atendimento (opcional)",
        required=False,
        max_length=1000,
    )

    def __init__(self, ticket_id: int) -> None:
        super().__init__()
        self.ticket_id = ticket_id

    async def on_submit(self, interacao: discord.Interaction) -> None:
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Ação disponível apenas no servidor."],
            )
            return

        if not membro_eh_staff_ticket(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas a equipe de tickets pode finalizar."],
            )
            return

        canal = interacao.channel
        if not isinstance(canal, discord.TextChannel):
            await responder_erro(
                interacao,
                titulo="Erro",
                linhas=["Canal inválido."],
            )
            return

        ticket = await buscar_ticket_por_canal(canal.id)
        if ticket is None or ticket.id != self.ticket_id:
            await responder_erro(
                interacao,
                titulo="Ticket não encontrado",
                linhas=["Não foi possível localizar este ticket."],
            )
            return

        if ticket.status == "finalizado":
            await responder_erro(
                interacao,
                titulo="Já finalizado",
                linhas=["Este ticket já foi encerrado."],
            )
            return

        texto_consideracoes = str(self.consideracoes.value or "").strip() or None
        ticket_final = await finalizar_ticket(
            ticket,
            membro,
            consideracoes=texto_consideracoes,
        )

        # Gera transcript local (fase 1). URL da API vem na fase 2.
        mensagens = await coletar_mensagens_do_canal(canal)
        html = montar_html_transcript(ticket_final, mensagens, interacao.guild)

        # Por enquanto só confirma e avisa a senha.
        # Na fase 2: envia html + senha para a API e recebe a URL.
        await responder_card(
            interacao,
            titulo="Ticket finalizado",
            linhas=[
                f"Finalizado por: **{membro.display_name}**",
                f"Senha do transcript: `{ticket_final.senha_transcript}`",
                "O canal será apagado em breve.",
                "Transcript gerado (envio para o site na próxima fase).",
            ],
            cor=COR_SUCESSO,
            delay=None,
        )

        try:
            await canal.send(
                content=(
                    f"🎫 **Ticket finalizado** por {membro.mention}\n"
                    f"Senha para visualizar o transcript: "
                    f"`{ticket_final.senha_transcript}`\n"
                    f"Considerações: {texto_consideracoes or '—'}"
                )
            )
        except discord.HTTPException:
            pass

        # Apaga o canal depois de um pequeno atraso (dá tempo de ler a mensagem)
        await asyncio.sleep(8)
        try:
            await canal.delete(
                reason=f"Ticket #{ticket_final.id} finalizado por {membro}"
            )
        except discord.HTTPException:
            pass
