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
from src.database.models import (
    Ticket,
    agora,
)


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


def nome_usuario_discord(membro: discord.Member | discord.User) -> str:
    """
    Retorna o username global do Discord (não o apelido do servidor).

    Ex.: 'guxta' em vez de '⟦RESP · HP⟧ Guxta ᵛᵃˡˡᵉʸ | 1763'
    """
    return membro.name


def sanitizar_nome_canal(texto: str) -> str:
    """Normaliza texto para uso em nome de canal Discord."""
    return texto.lower().replace(" ", "-").replace("/", "-").replace("|", "-")[:90]


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

    username = nome_usuario_discord(autor)
    nome_canal = sanitizar_nome_canal(f"{definicao['prefixo_canal']}-{username}")

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
        topic=f"Ticket criado para o usuário: {username}",
        reason=f"Ticket aberto por {username} — {definicao['rotulo']}",
    )

    async with async_session() as sessao:
        ticket = Ticket(
            categoria_chave=categoria_chave,
            categoria_rotulo=definicao["rotulo"],
            status="aberto",
            autor_discord_id=autor.id,
            autor_nome=username,
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
) -> tuple[Ticket, str]:
    """
    Marca o ticket como assumido e renomeia o canal.

    Nome final: `{emoji}・{username}` (ex.: 🙋・i.m.guxta)
    Retorna (ticket, nome_aplicado).
    """
    username_staff = nome_usuario_discord(staff)
    definicao = TICKETS_CATEGORIAS.get(ticket.categoria_chave) or {}
    emoji = definicao.get("emoji") or "🎫"
    # Discord aceita unicode no nome; mantém pontos do username
    novo_nome = f"{emoji}・{username_staff}"[:100]

    nome_aplicado = novo_nome
    try:
        canal_editado = await canal.edit(
            name=novo_nome,
            reason=f"Ticket assumido por {username_staff}",
        )
        nome_aplicado = canal_editado.name
    except discord.HTTPException as erro:
        print(f"⚠️ Falha ao renomear canal do ticket: {erro}")

    async with async_session() as sessao:
        ticket_db = await sessao.get(Ticket, ticket.id)
        if ticket_db is None:
            return ticket, nome_aplicado
        ticket_db.status = "assumido"
        ticket_db.staff_assumiu_id = staff.id
        ticket_db.staff_assumiu_nome = username_staff
        ticket_db.assumido_em = agora()
        await sessao.commit()
        await sessao.refresh(ticket_db)
        return ticket_db, nome_aplicado


async def marcar_ticket_saudado(ticket_id: int) -> Ticket | None:
    """Marca que a saudação inicial já foi enviada neste ticket."""
    async with async_session() as sessao:
        ticket_db = await sessao.get(Ticket, ticket_id)
        if ticket_db is None:
            return None
        ticket_db.saudado = True
        await sessao.commit()
        await sessao.refresh(ticket_db)
        return ticket_db


def listar_membros_com_acesso_extra(
    canal: discord.TextChannel,
    autor_discord_id: int,
) -> list[discord.Member]:
    """
    Membros com overwrite explícito de ver o canal,
    excluindo autor e bots (candidatos a 'remover do ticket').
    """
    lista: list[discord.Member] = []
    for alvo, overwrite in canal.overwrites.items():
        if not isinstance(alvo, discord.Member):
            continue
        if alvo.id == autor_discord_id:
            continue
        if alvo.bot:
            continue
        if overwrite.view_channel is True:
            lista.append(alvo)
    return lista


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
        ticket_db.staff_finalizou_nome = nome_usuario_discord(staff)
        ticket_db.consideracoes_finais = consideracoes
        ticket_db.senha_transcript = senha
        ticket_db.finalizado_em = agora()
        await sessao.commit()
        await sessao.refresh(ticket_db)
        return ticket_db


async def salvar_mensagem_botoes_id(ticket_id: int, mensagem_id: int) -> None:
    """Guarda o ID da mensagem do card de botões de staff."""
    async with async_session() as sessao:
        ticket_db = await sessao.get(Ticket, ticket_id)
        if ticket_db is None:
            return
        ticket_db.mensagem_botoes_id = mensagem_id
        await sessao.commit()


async def salvar_call_canal_id(ticket_id: int, call_canal_id: int | None) -> None:
    """Associa ou limpa o canal de voz de atendimento do ticket."""
    async with async_session() as sessao:
        ticket_db = await sessao.get(Ticket, ticket_id)
        if ticket_db is None:
            return
        ticket_db.call_canal_id = call_canal_id
        await sessao.commit()


async def buscar_ticket_por_id(ticket_id: int) -> Ticket | None:
    async with async_session() as sessao:
        return await sessao.get(Ticket, ticket_id)


async def enviar_card_no_canal_ticket(
    canal: discord.TextChannel,
    titulo: str,
    linhas: list[str],
    cor: discord.Color | None = None,
) -> discord.Message | None:
    """
    Publica um CardView no canal do ticket (não ephemeral).

    Usado para feedback visível de todas as ações de staff.
    """
    from src.utils.mensagens import (
        COR_INFO,
        CardView,
    )

    view = CardView(
        titulo=titulo,
        linhas=linhas,
        cor=cor or COR_INFO,
        timeout=None,
        com_marcador=False,
    )
    try:
        return await canal.send(view=view)
    except discord.HTTPException:
        return None


async def apagar_call_do_ticket(
    guilda: discord.Guild,
    ticket: Ticket,
) -> None:
    """Apaga o canal de voz ligado ao ticket, se ainda existir."""
    if not ticket.call_canal_id:
        return
    canal_voz = guilda.get_channel(int(ticket.call_canal_id))
    if canal_voz is None:
        try:
            canal_voz = await guilda.fetch_channel(int(ticket.call_canal_id))
        except discord.HTTPException:
            canal_voz = None
    if canal_voz is not None:
        try:
            await canal_voz.delete(reason=f"Call do ticket #{ticket.id} encerrada")
        except discord.HTTPException:
            pass
    await salvar_call_canal_id(ticket.id, None)


async def coletar_mensagens_do_canal(
    canal: discord.TextChannel,
    limite: int = 500,
) -> list[discord.Message]:
    """Coleta mensagens do canal (mais antigas primeiro) para o transcript."""
    mensagens: list[discord.Message] = []
    async for mensagem in canal.history(limit=limite, oldest_first=True):
        mensagens.append(mensagem)
    return mensagens


async def adicionar_membro_ao_ticket(
    canal: discord.TextChannel,
    membro_alvo: discord.Member,
) -> None:
    """Libera visão e envio de mensagens no canal do ticket para o membro."""
    await canal.set_permissions(
        membro_alvo,
        view_channel=True,
        send_messages=True,
        attach_files=True,
        embed_links=True,
        read_message_history=True,
        reason="Membro adicionado ao ticket",
    )


async def remover_membro_do_ticket(
    canal: discord.TextChannel,
    membro_alvo: discord.Member,
    autor_discord_id: int,
) -> str | None:
    """
    Remove acesso do membro ao canal.

    Não remove o autor do ticket nem o próprio bot.
    Retorna mensagem de erro ou None se ok.
    """
    if membro_alvo.id == autor_discord_id:
        return "Não é possível remover o autor do ticket."
    if membro_alvo.bot:
        return "Não é possível remover o bot do ticket."

    await canal.set_permissions(
        membro_alvo,
        overwrite=None,
        reason="Membro removido do ticket",
    )
    return None


async def trocar_nome_do_canal(
    canal: discord.TextChannel,
    novo_nome: str,
    staff: discord.Member,
) -> str:
    """Renomeia o canal. Retorna o nome aplicado."""
    nome_limpo = sanitizar_nome_canal(novo_nome)
    if not nome_limpo:
        nome_limpo = "ticket"
    await canal.edit(
        name=nome_limpo,
        reason=f"Nome alterado por {nome_usuario_discord(staff)}",
    )
    return nome_limpo


async def mover_canal_ticket(
    canal: discord.TextChannel,
    categoria_destino: discord.CategoryChannel,
    staff: discord.Member,
) -> None:
    """Move o canal do ticket para outra categoria Discord."""
    await canal.edit(
        category=categoria_destino,
        reason=f"Canal movido por {nome_usuario_discord(staff)}",
    )


async def transferir_atendimento(
    ticket: Ticket,
    novo_staff: discord.Member,
    canal: discord.TextChannel,
) -> tuple[Ticket, str]:
    """Transfere o atendimento para outro staff e renomeia o canal."""
    return await assumir_ticket(ticket, novo_staff, canal)


async def criar_call_atendimento(
    guilda: discord.Guild,
    canal_texto: discord.TextChannel,
    ticket: Ticket,
    staff: discord.Member,
) -> discord.VoiceChannel:
    """
    Cria um canal de voz na mesma categoria do ticket.

    Permissões: autor + staff + cargos de ticket.
    """
    categoria = canal_texto.category
    username_autor = ticket.autor_nome or "usuario"
    nome_call = sanitizar_nome_canal(f"📞・atendimento-{username_autor}")

    overwrites: dict[
        discord.Role | discord.Member | discord.Object,
        discord.PermissionOverwrite,
    ] = {
        guilda.default_role: discord.PermissionOverwrite(view_channel=False),
        guilda.me: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            manage_channels=True,
        ),
        staff: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            move_members=True,
        ),
    }

    autor = guilda.get_member(ticket.autor_discord_id)
    if autor is not None:
        overwrites[autor] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
        )

    for objeto_cargo in _ids_cargos_staff(guilda):
        overwrites[objeto_cargo] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
        )

    canal_voz = await guilda.create_voice_channel(
        name=nome_call,
        category=categoria,
        overwrites=overwrites,
        reason=f"Call de atendimento — ticket #{ticket.id}",
    )
    return canal_voz


def listar_categorias_ticket_na_guilda(
    guilda: discord.Guild,
) -> list[tuple[str, discord.CategoryChannel]]:
    """
    Retorna pares (rótulo, categoria) das categorias de ticket configuradas
    e existentes na guilda.
    """
    lista: list[tuple[str, discord.CategoryChannel]] = []
    for definicao in TICKETS_CATEGORIAS.values():
        chave_config = definicao["categoria_config"]
        categoria_id = CANAIS.get(chave_config)
        if not categoria_id:
            continue
        canal = guilda.get_channel(int(categoria_id))
        if isinstance(canal, discord.CategoryChannel):
            lista.append((definicao["rotulo"], canal))
    return lista


def montar_html_transcript(
    ticket: Ticket,
    mensagens: list[discord.Message],
    guilda: discord.Guild,
) -> str:
    """
    Gera HTML do transcript no estilo Discord (inspirado no modelo CMS).

    - Tema escuro Discord (#36393f)
    - Avatar via CDN (display_avatar / cdn.discordapp.com)
    - Cabeçalho com ícone do servidor e canal
    - Tags autor / staff / bot
    - Anexos de imagem embutidos; demais como link
    """
    import html as modulo_html

    def esc(texto: str) -> str:
        return modulo_html.escape(texto or "")

    def url_avatar(usuario: discord.abc.User) -> str:
        try:
            return str(usuario.display_avatar.replace(size=64).url)
        except Exception:
            return f"https://cdn.discordapp.com/embed/avatars/{int(usuario.id) % 6}.png"

    def url_icone_guilda() -> str:
        if guilda is not None and guilda.icon is not None:
            return str(guilda.icon.replace(size=128).url)
        return "https://cdn.discordapp.com/embed/avatars/0.png"

    def formatar_horario(dt) -> str:
        if dt is None:
            return "—"
        local = dt
        try:
            from src.utils.formatacao import para_horario_brasilia

            convertido = para_horario_brasilia(dt)
            if convertido is not None:
                local = convertido
        except Exception:
            pass
        return local.strftime("%d/%m/%Y %H:%M")

    ids_staff: set[int] = set()
    if ticket.staff_assumiu_id:
        ids_staff.add(int(ticket.staff_assumiu_id))
    if ticket.staff_finalizou_id:
        ids_staff.add(int(ticket.staff_finalizou_id))

    nome_guilda = esc(guilda.name if guilda else "CMS Valley")
    nome_canal = esc(
        f"#{ticket.categoria_rotulo}" if ticket.categoria_rotulo else "#ticket"
    )
    icone = esc(url_icone_guilda())

    css = """
:root {
  --bg: #36393f;
  --surface: #2f3136;
  --text: #dcddde;
  --muted: #72767d;
  --accent: #00aff4;
  --border: #4f545c;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Whitney, "Helvetica Neue", Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 16px;
  line-height: 1.375;
  padding: 24px 16px 48px;
}
.header {
  max-width: 900px;
  margin: 0 auto 28px;
  text-align: center;
}
.guild-avatar {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  border: 2px solid var(--accent);
  box-shadow: 0 0 16px rgba(0, 175, 244, 0.45);
}
.guild-name {
  margin: 12px 0 4px;
  font-size: 1.45rem;
  font-weight: 700;
  color: #fff;
}
.channel-info {
  color: #b0b0b0;
  font-size: 1.05rem;
}
.ticket-meta {
  max-width: 900px;
  margin: 0 auto 24px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  font-size: 14px;
  color: #c5c6c7;
}
.ticket-meta div { margin: 4px 0; }
.ticket-meta strong { color: #fff; }
.chat {
  max-width: 900px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.message-container {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 10px;
}
.message-container:hover { background: rgba(4, 4, 5, 0.18); }
.avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  flex-shrink: 0;
  border: 1px solid rgba(0, 175, 244, 0.35);
}
.message-body { min-width: 0; flex: 1; }
.author-line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 2px;
}
.author {
  font-weight: 600;
  color: var(--accent);
}
.timestamp {
  font-size: 0.75rem;
  color: var(--muted);
}
.tag {
  font-size: 0.7rem;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 3px;
  color: #fff;
  text-transform: uppercase;
}
.tag-bot { background: rgba(2, 242, 255, 0.35); }
.tag-autor { background: rgba(24, 243, 14, 0.35); }
.tag-staff { background: rgba(255, 69, 0, 0.55); }
.content-message {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  font-size: 0.95rem;
  color: var(--text);
}
.attachments { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.attachments img {
  max-width: min(420px, 100%);
  border-radius: 8px;
  display: block;
  border: 1px solid var(--border);
}
.file-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--accent);
  text-decoration: none;
  font-size: 0.9rem;
}
.file-link:hover { text-decoration: underline; }
.embed {
  margin-top: 8px;
  border-left: 4px solid var(--accent);
  background: #252729;
  border-radius: 4px;
  padding: 10px 12px;
  max-width: 520px;
}
.embed-title { font-weight: 700; color: #fff; margin-bottom: 4px; }
.embed-description { color: #c5c6c7; font-size: 0.9rem; white-space: pre-wrap; }
.footer {
  max-width: 900px;
  margin: 32px auto 0;
  text-align: center;
  font-size: 12px;
  color: var(--muted);
}
@media (max-width: 640px) {
  .message-container { padding: 8px 4px; }
  .attachments img { max-width: 100%; }
}
"""

    partes: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='pt-BR'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Transcript #{ticket.id} — {esc(ticket.categoria_rotulo or 'Ticket')}</title>",
        f"<style>{css}</style>",
        "</head>",
        "<body>",
        "<header class='header'>",
        f"<img class='guild-avatar' src='{icone}' alt='Servidor'>",
        f"<div class='guild-name'>{nome_guilda}</div>",
        f"<div class='channel-info'>Transcript · {nome_canal}</div>",
        "</header>",
        "<section class='ticket-meta'>",
        f"<div><strong>Ticket:</strong> #{ticket.id}</div>",
        f"<div><strong>Categoria:</strong> {esc(ticket.categoria_rotulo or '—')}</div>",
        f"<div><strong>Autor:</strong> {esc(ticket.autor_nome or '—')} "
        f"(`{ticket.autor_discord_id}`)</div>",
    ]
    if ticket.staff_assumiu_nome:
        partes.append(
            f"<div><strong>Assumido por:</strong> {esc(ticket.staff_assumiu_nome)}</div>"
        )
    if ticket.staff_finalizou_nome:
        partes.append(
            f"<div><strong>Finalizado por:</strong> {esc(ticket.staff_finalizou_nome)}</div>"
        )
    if ticket.consideracoes_finais:
        partes.append(
            f"<div><strong>Considerações:</strong> {esc(ticket.consideracoes_finais)}</div>"
        )
    partes.append(
        f"<div><strong>Aberto em:</strong> {formatar_horario(ticket.aberto_em)}</div>"
    )
    partes.append("</section>")
    partes.append("<main class='chat'>")

    for mensagem in mensagens:
        autor = mensagem.author
        nome = esc(getattr(autor, "display_name", None) or autor.name)
        avatar = esc(url_avatar(autor))
        quando = formatar_horario(mensagem.created_at)

        tags: list[str] = []
        if autor.bot:
            tags.append("<span class='tag tag-bot'>BOT</span>")
        if autor.id == ticket.autor_discord_id:
            tags.append("<span class='tag tag-autor'>AUTOR</span>")
        if autor.id in ids_staff:
            tags.append("<span class='tag tag-staff'>STAFF</span>")

        conteudo_bruto = mensagem.content or ""
        conteudo = esc(conteudo_bruto)

        partes.append("<article class='message-container'>")
        partes.append(f"<img class='avatar' src='{avatar}' alt='' loading='lazy'>")
        partes.append("<div class='message-body'>")
        partes.append("<div class='author-line'>")
        partes.append(f"<span class='author'>{nome}</span>")
        partes.extend(tags)
        partes.append(f"<span class='timestamp'>{quando}</span>")
        partes.append("</div>")

        if conteudo:
            partes.append(f"<div class='content-message'>{conteudo}</div>")

        # Embeds simples (título + descrição)
        for embed in mensagem.embeds[:5]:
            titulo = esc(embed.title or "")
            desc = esc(embed.description or "")
            if not titulo and not desc:
                continue
            partes.append("<div class='embed'>")
            if titulo:
                partes.append(f"<div class='embed-title'>{titulo}</div>")
            if desc:
                partes.append(f"<div class='embed-description'>{desc}</div>")
            partes.append("</div>")

        # Anexos
        if mensagem.attachments:
            partes.append("<div class='attachments'>")
            for anexo in mensagem.attachments:
                nome_arq = esc(anexo.filename)
                url_anexo = esc(anexo.url)
                tipo = (anexo.content_type or "").lower()
                eh_imagem = tipo.startswith("image/") or nome_arq.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp")
                )
                if eh_imagem:
                    partes.append(
                        f"<a href='{url_anexo}' target='_blank' rel='noopener'>"
                        f"<img src='{url_anexo}' alt='{nome_arq}' loading='lazy'></a>"
                    )
                else:
                    partes.append(
                        f"<a class='file-link' href='{url_anexo}' "
                        f"target='_blank' rel='noopener'>📎 {nome_arq}</a>"
                    )
            partes.append("</div>")

        if (
            not conteudo
            and not mensagem.attachments
            and not any(e.title or e.description for e in mensagem.embeds)
        ):
            partes.append(
                "<div class='content-message'><em>(mensagem sem texto)</em></div>"
            )

        partes.append("</div></article>")

    partes.append("</main>")
    partes.append(
        "<footer class='footer'>CMS Valley · Transcript gerado automaticamente</footer>"
    )
    partes.append("</body></html>")
    return "\n".join(partes)
