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


def _coletar_textos_de_componente(objeto, profundidade: int = 0) -> list[str]:
    """
    Percorre componentes Components V2 (TextDisplay, Section, Container, etc.)
    e extrai textos legíveis para o transcript.
    """
    if objeto is None or profundidade > 12:
        return []

    textos: list[str] = []

    if isinstance(objeto, str):
        limpo = objeto.strip()
        return [limpo] if limpo else []

    if isinstance(objeto, dict):
        for chave in (
            "content",
            "label",
            "placeholder",
            "title",
            "description",
            "name",
            "value",
        ):
            valor = objeto.get(chave)
            if isinstance(valor, str) and valor.strip():
                textos.append(valor.strip())
        for chave in ("components", "children", "items", "accessory"):
            filho = objeto.get(chave)
            if isinstance(filho, list):
                for item in filho:
                    textos.extend(_coletar_textos_de_componente(item, profundidade + 1))
            elif filho is not None:
                textos.extend(_coletar_textos_de_componente(filho, profundidade + 1))
        return textos

    for atributo in (
        "content",
        "label",
        "placeholder",
        "title",
        "description",
        "name",
        "value",
    ):
        valor = getattr(objeto, atributo, None)
        if isinstance(valor, str) and valor.strip():
            textos.append(valor.strip())

    for atributo in ("children", "components", "items"):
        filhos = getattr(objeto, atributo, None)
        if not filhos:
            continue
        try:
            iteravel = list(filhos)
        except TypeError:
            continue
        for filho in iteravel:
            textos.extend(_coletar_textos_de_componente(filho, profundidade + 1))

    # Dados brutos da API (quando o content da mensagem está vazio)
    dados_brutos = getattr(objeto, "_data", None)
    if isinstance(dados_brutos, dict):
        textos.extend(_coletar_textos_de_componente(dados_brutos, profundidade + 1))

    return textos


def _texto_da_mensagem_discord(mensagem: discord.Message) -> str:
    """
    Monta o texto visível da mensagem.

    Mensagens Components V2 do bot costumam ter content vazio —
    o texto fica nos TextDisplay / Section dos componentes.
    """
    partes: list[str] = []

    if mensagem.content and mensagem.content.strip():
        partes.append(mensagem.content.strip())

    # Componentes da mensagem (V2 e clássicos)
    componentes = getattr(mensagem, "components", None) or []
    for componente in componentes:
        partes.extend(_coletar_textos_de_componente(componente))

    # Payload bruto (fallback)
    dados = getattr(mensagem, "_data", None)
    if isinstance(dados, dict):
        for componente in dados.get("components") or []:
            partes.extend(_coletar_textos_de_componente(componente))

    # Dedup preservando ordem
    vistos: set[str] = set()
    unicos: list[str] = []
    for texto in partes:
        chave = texto.strip()
        if not chave or chave in vistos:
            continue
        # Ignora custom_ids e lixo técnico
        if chave.startswith("ticket:") or chave.startswith("tpl:"):
            continue
        vistos.add(chave)
        unicos.append(chave)

    return "\n".join(unicos).strip()


def montar_html_transcript(
    ticket: Ticket,
    mensagens: list[discord.Message],
    guilda: discord.Guild,
) -> str:
    """
    HTML do transcript no estilo dos cards do ticket (cyan border + partículas).

    Inspirado no visual dos cards Components V2 e no modelo de referência.
    """
    import html as modulo_html
    import json as modulo_json

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
        return local.strftime("%d/%m/%Y, %H:%M:%S")

    def markdown_simples_para_html(texto: str) -> str:
        """Conversão leve: escapes + quebras + **negrito** + `code` + ```blocos```."""
        import re as modulo_re

        seguro = esc(texto)
        # blocos de código
        seguro = modulo_re.sub(
            r"```(?:\w+)?\n?(.*?)```",
            r"<pre class='code-block'>\1</pre>",
            seguro,
            flags=modulo_re.DOTALL,
        )
        seguro = modulo_re.sub(r"`([^`]+)`", r"<code>\1</code>", seguro)
        seguro = modulo_re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", seguro)
        seguro = modulo_re.sub(r"(?m)^#\s+(.+)$", r"<div class='h1'>\1</div>", seguro)
        seguro = modulo_re.sub(r"(?m)^##\s+(.+)$", r"<div class='h2'>\1</div>", seguro)
        seguro = modulo_re.sub(r"(?m)^###\s+(.+)$", r"<div class='h3'>\1</div>", seguro)
        seguro = modulo_re.sub(
            r"(?m)^-#\s+(.+)$", r"<div class='muted'>\1</div>", seguro
        )
        seguro = seguro.replace("\n", "<br>")
        return seguro

    ids_staff: set[int] = set()
    if ticket.staff_assumiu_id:
        ids_staff.add(int(ticket.staff_assumiu_id))
    if ticket.staff_finalizou_id:
        ids_staff.add(int(ticket.staff_finalizou_id))

    nome_guilda = esc(guilda.name if guilda else "CMS Valley")
    icone = esc(url_icone_guilda())

    # Visual alinhado ao modelo (cards cyan, tags, partículas)
    css = """
@import url('https://fonts.googleapis.com/css2?family=Rock+Salt&display=swap');
:root {
  --bg: #36393f;
  --card: #333;
  --text: #dcddde;
  --muted: #72767d;
  --cyan: #00aff4;
  --cyan-glow: rgba(0, 175, 244, 0.7);
  --cyan-shadow: #00aff438;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body {
  font-family: Whitney, "Helvetica Neue", Helvetica, Arial, sans-serif;
  background-color: var(--bg);
  color: var(--text);
  font-size: 17px;
  padding: 20px;
  overflow-y: auto;
  position: relative;
}
#particles-js {
  position: fixed; top: 0; left: 0; width: 100%; height: 100%;
  z-index: -1; pointer-events: none;
}
.page { position: relative; z-index: 1; max-width: 960px; margin: 0 auto; }
.header, .footer { text-align: center; margin-bottom: 20px; }
.header-box {
  max-width: 71%; padding: 20px; margin: 0 auto;
  display: flex; flex-direction: column; align-items: center;
}
.guild-avatar {
  border-radius: 50%; width: 80px; height: 80px; margin-bottom: 10px;
  box-shadow: 0 0 15px var(--cyan-glow);
  animation: border-glow 2s infinite;
}
.guild-name {
  font-family: 'Rock Salt', cursive;
  font-size: 1.5em; font-weight: 700; margin-top: 10px;
  text-shadow: 0 0 15px var(--cyan-glow);
  animation: text-glow 2s infinite;
  color: #fff;
}
.channel-info { font-size: 1.2em; margin-top: 5px; color: #b0b0b0; }
.ticket-meta {
  background: #252729; border: 1px solid var(--cyan);
  border-radius: 10px; padding: 14px 16px; margin: 0 auto 24px;
  max-width: 70%; box-shadow: 6px 4px 3px 3px var(--cyan-shadow);
  font-size: 14px; color: #c5c6c7; text-align: left;
}
.ticket-meta div { margin: 4px 0; }
.ticket-meta strong { color: #fff; }
.chat {
  display: flex; flex-direction: column; gap: 0;
  align-items: flex-start; width: 100%;
}
.msg-card {
  margin-bottom: 20px; padding: 20px; border-radius: 10px;
  max-width: 70%; align-self: flex-start;
  background-color: var(--card);
  border: 1px solid var(--cyan);
  box-shadow: 6px 4px 3px 3px var(--cyan-shadow);
}
.msg-card.bot { background-color: #333; border: 1px solid var(--cyan); }
.msg-card.staff { background-color: #333; border: 1px solid var(--cyan); }
.msg-card.autor { background-color: #333; border: 1px solid var(--cyan); }
.msg-top {
  display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px;
}
.avatar {
  width: 40px; height: 40px; border-radius: 50%; flex-shrink: 0;
  border: 1px solid var(--cyan);
  box-shadow: 2px 2px 3px 3px var(--cyan-shadow);
}
.meta-col {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 8px;
}
.author { font-weight: 700; color: var(--cyan); }
.timestamp { margin-left: 5px; font-size: .8em; color: var(--muted); }
.tag, .bot-tag, .autor-tag, .staff-tag {
  font-size: 0.8em; padding: 2px 5px; border-radius: 3px;
  color: #fff; display: inline;
}
.tag-bot, .bot-tag { background-color: #02f2ff6e; }
.tag-autor, .autor-tag { background-color: #18f30e78; }
.tag-staff, .staff-tag { background-color: #ff4500; }
.content, .content-message {
  overflow-wrap: break-word; word-break: break-word;
  width: auto; max-width: 100%; font-size: 14px; margin-top: 5px;
  white-space: pre-wrap; color: var(--text);
}
.content .h1 { font-size: 1.5em; font-weight: 700; margin: 4px 0 8px; color: #fff; }
.content .h2 { font-size: 1.25em; font-weight: 700; margin: 4px 0 6px; color: #fff; }
.content .h3 { font-size: 1em; font-weight: 600; margin: 4px 0; color: #e8eaed; }
.content .muted { color: var(--muted); font-size: 0.85rem; }
.content code {
  background: #2F3136; padding: 1px 5px; border-radius: 4px;
  font-family: ui-monospace, monospace; font-size: 0.85em;
}
.content pre.code-block {
  background: #2F3136; border: 1px solid #4f545c; border-radius: 8px;
  padding: 10px 12px; overflow-x: auto; white-space: pre-wrap;
  font-family: ui-monospace, monospace; font-size: 0.82rem; margin: 8px 0;
}
.attachments { margin-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.attachments img {
  max-width: min(440px, 100%); border-radius: 10px; display: block;
  border: 1px solid rgba(0,175,244,0.35);
}
.file-link {
  display: inline-flex; align-items: center; padding: 5px 10px;
  border: 1px solid #ddd; border-radius: 5px; text-decoration: none;
  color: #333; background-color: #f9f9f9; margin-top: 10px;
}
.embed {
  border-radius: 10px; padding: 10px; margin-top: 10px;
  background-color: #252729; border: 1px solid #4f545c;
  box-shadow: 0 2px 6px rgba(0,0,0,0.15); max-width: 520px;
}
.embed-title { font-weight: 700; color: var(--cyan); }
.embed-description {
  margin-top: 5px; color: #c5c6c7; overflow-wrap: break-word;
  font-size: 14px; white-space: pre-wrap;
}
.btn-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.btn-chip {
  background: #2a2f3a; border: 1px solid #3a4150; color: #cfd3db;
  border-radius: 6px; padding: 5px 10px; font-size: 0.75rem;
}
.footer { text-align: center; margin-top: 28px; font-size: 12px; color: #b0b0b0; }
@keyframes text-glow {
  0% { text-shadow: 0 0 15px var(--cyan-glow); }
  50% { text-shadow: 0 0 30px rgba(0,175,244,0.9); }
  100% { text-shadow: 0 0 15px var(--cyan-glow); }
}
@keyframes border-glow {
  0% { box-shadow: 0 0 15px var(--cyan-glow); }
  50% { box-shadow: 0 0 30px rgba(0,175,244,0.9); }
  100% { box-shadow: 0 0 15px var(--cyan-glow); }
}
@media (max-width: 700px) {
  .msg-card { max-width: 100%; padding: 12px; }
  .ticket-meta { max-width: 100%; }
  .attachments img { max-width: 100%; }
  .tag, .bot-tag, .autor-tag, .staff-tag { font-size: 0.7em; padding: 1px 3px; }
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
        "<div id='particles-js'></div>",
        "<div class='page'>",
        "<header class='header'><div class='header-box'>",
        f"<img class='guild-avatar' src='{icone}' alt=''>",
        f"<div class='guild-name'>{nome_guilda}</div>",
        f"<div class='channel-info'>Transcript · "
        f"{esc(ticket.categoria_rotulo or 'Ticket')} · #{ticket.id}</div>",
        "</div></header>",
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

        classes = ["msg-card"]
        tags_html: list[str] = []
        if autor.bot:
            classes.append("bot")
            tags_html.append("<span class='bot-tag'>BOT</span>")
        if autor.id == ticket.autor_discord_id:
            classes.append("autor")
            tags_html.append("<span class='autor-tag'>Autor</span>")
        if autor.id in ids_staff:
            classes.append("staff")
            tags_html.append("<span class='staff-tag'>STAFF</span>")

        texto_bruto = _texto_da_mensagem_discord(mensagem)
        rotulos_botoes: list[str] = []
        # Coleta labels de botões para exibir como chips
        for componente in getattr(mensagem, "components", None) or []:
            for texto_comp in _coletar_textos_de_componente(componente):
                # labels curtos de botão já entram no texto; chips extras se vierem só de Button
                pass
        dados = getattr(mensagem, "_data", None)
        if isinstance(dados, dict):

            def _labels_botao(no):
                if not isinstance(no, dict):
                    return
                # type 2 = button no Discord
                if no.get("type") == 2 and no.get("label"):
                    rotulos_botoes.append(str(no["label"]))
                for filho in no.get("components") or []:
                    _labels_botao(filho)

            for comp in dados.get("components") or []:
                _labels_botao(comp)

        partes.append(f"<article class='{' '.join(classes)}'>")
        partes.append("<div class='msg-top'>")
        partes.append(f"<img class='avatar' src='{avatar}' alt='' loading='lazy'>")
        partes.append("<div class='meta-col'>")
        partes.append(f"<span class='author'>{nome}</span>")
        partes.append(f"<span class='timestamp'>{quando}</span>")
        partes.extend(tags_html)
        partes.append("</div></div>")

        if texto_bruto:
            partes.append(
                f"<div class='content'>{markdown_simples_para_html(texto_bruto)}</div>"
            )

        for embed in mensagem.embeds[:6]:
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

        if rotulos_botoes:
            # Dedup
            visto_btn: set[str] = set()
            chips: list[str] = []
            for rotulo in rotulos_botoes:
                if rotulo in visto_btn:
                    continue
                visto_btn.add(rotulo)
                chips.append(f"<span class='btn-chip'>{esc(rotulo)}</span>")
            if chips:
                partes.append("<div class='btn-row'>" + "".join(chips) + "</div>")

        if (
            not texto_bruto
            and not mensagem.attachments
            and not any(e.title or e.description for e in mensagem.embeds)
            and not rotulos_botoes
        ):
            partes.append(
                "<div class='content'><em>(mensagem sem texto legível)</em></div>"
            )

        partes.append("</article>")

    partes.append("</main>")
    partes.append(
        "<footer class='footer'>CMS Valley · Transcript gerado automaticamente</footer>"
    )
    partes.append("</div>")  # page

    # Particles (CDN) — fundo estilo do modelo
    config_particulas = {
        "particles": {
            "number": {"value": 55, "density": {"enable": True, "value_area": 900}},
            "color": {"value": "#00aff4"},
            "shape": {"type": "circle"},
            "opacity": {"value": 0.4, "random": True},
            "size": {"value": 2.5, "random": True},
            "line_linked": {
                "enable": True,
                "distance": 140,
                "color": "#00aff4",
                "opacity": 0.2,
                "width": 1,
            },
            "move": {"enable": True, "speed": 1.1, "out_mode": "out"},
        },
        "interactivity": {
            "detect_on": "canvas",
            "events": {
                "onhover": {"enable": True, "mode": "grab"},
                "onclick": {"enable": False},
                "resize": True,
            },
            "modes": {"grab": {"distance": 120, "line_linked": {"opacity": 0.35}}},
        },
        "retina_detect": True,
    }
    partes.append(
        '<script src="https://cdn.jsdelivr.net/npm/particles.js@2.0.0/particles.min.js"></script>'
    )
    partes.append(
        "<script>particlesJS('particles-js', "
        + modulo_json.dumps(config_particulas)
        + ");</script>"
    )
    partes.append("</body></html>")
    return "\n".join(partes)
