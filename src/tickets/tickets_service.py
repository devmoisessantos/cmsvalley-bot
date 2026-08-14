"""
Lógica de tickets: criar canal, assumir, finalizar e preparar transcript.
"""

from __future__ import annotations

import secrets

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    CARGOS,
    CARGOS_TICKET_STAFF,
    TICKETS_CATEGORIAS,
)
from src.database.connection import async_session
from src.database.models import Ticket, agora


def _ids_cargos_staff(guilda: discord.Guild) -> list[discord.Object]:
    """Resolve os IDs dos cargos de staff de ticket presentes na guilda."""
    objetos: list[discord.Object] = []
    for nome_cargo in CARGOS_TICKET_STAFF:
        cargo_id = CARGOS.get(nome_cargo)
        if cargo_id:
            objetos.append(discord.Object(id=int(cargo_id)))
    return objetos


def membro_eh_staff_ticket(membro: discord.Member) -> bool:
    """True se o membro tem cargo de equipe de ticket ou diretoria."""
    nomes_dos_cargos = {cargo.name for cargo in membro.roles}
    return bool(nomes_dos_cargos.intersection(set(CARGOS_TICKET_STAFF)))


def gerar_senha_transcript() -> str:
    """Gera senha curta para visualização do transcript."""
    return secrets.token_hex(3)


async def buscar_ticket_por_canal(canal_id: int) -> Ticket | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Ticket).where(Ticket.canal_id == canal_id)
        )
        return resultado.scalar_one_or_none()


async def buscar_ticket_aberto_do_autor(
    autor_discord_id: int,
    categoria_chave: str | None = None,
) -> Ticket | None:
    """Retorna ticket ainda aberto/assumido do autor (opcionalmente da mesma categoria)."""
    async with async_session() as sessao:
        consulta = select(Ticket).where(
            Ticket.autor_discord_id == autor_discord_id,
            Ticket.status.in_(["aberto", "assumido"]),
        )
        if categoria_chave:
            consulta = consulta.where(Ticket.categoria_chave == categoria_chave)
        resultado = await sessao.execute(consulta)
        return resultado.scalar_one_or_none()


async def criar_ticket(
    guilda: discord.Guild,
    autor: discord.Member,
    categoria_chave: str,
) -> tuple[Ticket, discord.TextChannel] | None:
    """
    Cria o canal privado do ticket e registra no banco.

    Retorna (ticket, canal) ou None se a categoria for inválida.
    """
    definicao = TICKETS_CATEGORIAS.get(categoria_chave)
    if definicao is None:
        return None

    chave_categoria_config = definicao["categoria_config"]
    categoria_discord_id = CANAIS.get(chave_categoria_config)
    if not categoria_discord_id:
        return None

    categoria_discord = guilda.get_channel(int(categoria_discord_id))
    if categoria_discord is None or not isinstance(
        categoria_discord, discord.CategoryChannel
    ):
        return None

    nome_base = f"{definicao['prefixo_canal']}-{autor.display_name}"
    # Discord limita nome de canal a 100 caracteres e caracteres especiais
    nome_canal = (
        nome_base.lower()
        .replace(" ", "-")
        .replace("/", "-")[:90]
    )

    overwrites: dict[
        discord.Role | discord.Member | discord.Object,
        discord.PermissionOverwrite,
    ] = {
        guilda.default_role: discord.PermissionOverwrite(view_channel=False),
        autor: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            read_message_history=True,
        ),
        guilda.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            attach_files=True,
            embed_links=True,
            read_message_history=True,
        ),
    }

    for objeto_cargo in _ids_cargos_staff(guilda):
        overwrites[objeto_cargo] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            read_message_history=True,
        )

    canal = await guilda.create_text_channel(
        name=nome_canal,
        category=categoria_discord,
        overwrites=overwrites,
        topic=f"Ticket de {autor.display_name} ({autor.id}) | {definicao['rotulo']}",
        reason=f"Ticket aberto por {autor} — {definicao['rotulo']}",
    )

    async with async_session() as sessao:
        ticket = Ticket(
            categoria_chave=categoria_chave,
            categoria_rotulo=definicao["rotulo"],
            status="aberto",
            autor_discord_id=autor.id,
            autor_nome=autor.display_name,
            canal_id=canal.id,
            aberto_em=agora(),
        )
        sessao.add(ticket)
        await sessao.commit()
        await sessao.refresh(ticket)

    return ticket, canal


async def assumir_ticket(
    ticket: Ticket,
    staff: discord.Member,
    canal: discord.TextChannel,
) -> Ticket:
    """Marca o ticket como assumido e renomeia o canal com o nick do staff."""
    novo_nome = f"{ticket.categoria_rotulo[:20]}・{staff.display_name}"
    novo_nome = novo_nome.lower().replace(" ", "-").replace("/", "-")[:90]

    try:
        await canal.edit(
            name=novo_nome,
            reason=f"Ticket assumido por {staff}",
        )
    except discord.HTTPException:
        pass

    async with async_session() as sessao:
        ticket_db = await sessao.get(Ticket, ticket.id)
        if ticket_db is None:
            return ticket
        ticket_db.status = "assumido"
        ticket_db.staff_assumiu_id = staff.id
        ticket_db.staff_assumiu_nome = staff.display_name
        ticket_db.assumido_em = agora()
        await sessao.commit()
        await sessao.refresh(ticket_db)
        return ticket_db


async def finalizar_ticket(
    ticket: Ticket,
    staff: discord.Member,
    consideracoes: str | None = None,
) -> Ticket:
    """Marca o ticket como finalizado e gera senha de transcript."""
    senha = gerar_senha_transcript()

    async with async_session() as sessao:
        ticket_db = await sessao.get(Ticket, ticket.id)
        if ticket_db is None:
            return ticket
        ticket_db.status = "finalizado"
        ticket_db.staff_finalizou_id = staff.id
        ticket_db.staff_finalizou_nome = staff.display_name
        ticket_db.consideracoes_finais = consideracoes
        ticket_db.senha_transcript = senha
        ticket_db.finalizado_em = agora()
        await sessao.commit()
        await sessao.refresh(ticket_db)
        return ticket_db


async def coletar_mensagens_do_canal(
    canal: discord.TextChannel,
    limite: int = 500,
) -> list[discord.Message]:
    """Coleta mensagens do canal (mais antigas primeiro) para o transcript."""
    mensagens: list[discord.Message] = []
    async for mensagem in canal.history(limit=limite, oldest_first=True):
        mensagens.append(mensagem)
    return mensagens


def montar_html_transcript(
    ticket: Ticket,
    mensagens: list[discord.Message],
    guilda: discord.Guild,
) -> str:
    """
    Gera HTML simples e legível do transcript.

    Fase 1: HTML estático. Depois enviamos para a API / site.
    """
    import html as modulo_html

    linhas_html: list[str] = []
    linhas_html.append("<!DOCTYPE html>")
    linhas_html.append("<html lang='pt-BR'><head><meta charset='utf-8'>")
    linhas_html.append(
        f"<title>Transcript #{ticket.id} — {ticket.categoria_rotulo}</title>"
    )
    linhas_html.append(
        "<style>"
        "body{font-family:system-ui,sans-serif;background:#1e1f22;color:#dbdee1;"
        "max-width:900px;margin:24px auto;padding:16px}"
        ".msg{margin:12px 0;padding:10px 14px;background:#2b2d31;border-radius:8px}"
        ".meta{font-size:12px;color:#949ba4;margin-bottom:4px}"
        ".autor{font-weight:600;color:#fff}"
        ".anexo{color:#00a8fc;font-size:13px}"
        "h1{font-size:1.4rem} .info{color:#b5bac1;margin-bottom:20px}"
        "</style></head><body>"
    )
    linhas_html.append(f"<h1>Ticket #{ticket.id} — {ticket.categoria_rotulo}</h1>")
    linhas_html.append("<div class='info'>")
    linhas_html.append(
        f"<div>Autor: {modulo_html.escape(ticket.autor_nome)} "
        f"({ticket.autor_discord_id})</div>"
    )
    if ticket.staff_assumiu_nome:
        linhas_html.append(
            f"<div>Assumido por: {modulo_html.escape(ticket.staff_assumiu_nome)}</div>"
        )
    if ticket.staff_finalizou_nome:
        linhas_html.append(
            f"<div>Finalizado por: "
            f"{modulo_html.escape(ticket.staff_finalizou_nome)}</div>"
        )
    if ticket.consideracoes_finais:
        linhas_html.append(
            f"<div>Considerações: "
            f"{modulo_html.escape(ticket.consideracoes_finais)}</div>"
        )
    linhas_html.append(
        f"<div>Aberto em: "
        f"{ticket.aberto_em.isoformat() if ticket.aberto_em else '—'}</div>"
    )
    linhas_html.append("</div>")

    for mensagem in mensagens:
        autor_nome = modulo_html.escape(mensagem.author.display_name)
        timestamp = mensagem.created_at.strftime("%d/%m/%Y %H:%M")
        conteudo_bruto = mensagem.content or ""
        conteudo = modulo_html.escape(conteudo_bruto).replace("\n", "<br>")
        if not conteudo and mensagem.attachments:
            conteudo = "<em>(sem texto)</em>"

        linhas_html.append("<div class='msg'>")
        linhas_html.append(
            f"<div class='meta'><span class='autor'>{autor_nome}</span> · {timestamp}</div>"
        )
        if conteudo:
            linhas_html.append(f"<div>{conteudo}</div>")
        for anexo in mensagem.attachments:
            nome_arquivo = modulo_html.escape(anexo.filename)
            url_segura = modulo_html.escape(anexo.url)
            linhas_html.append(
                f"<div class='anexo'>📎 <a href='{url_segura}' target='_blank'>"
                f"{nome_arquivo}</a></div>"
            )
        linhas_html.append("</div>")

    linhas_html.append("</body></html>")
    return "\n".join(linhas_html)
