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


def _eh_no_botao(objeto) -> bool:
    """True se o nó parece um botão Discord (type 2 ou classe Button)."""
    if isinstance(objeto, dict):
        return objeto.get("type") == 2
    tipo = getattr(objeto, "type", None)
    if tipo is not None and getattr(tipo, "value", tipo) == 2:
        return True
    nome_classe = type(objeto).__name__.lower()
    return "button" in nome_classe and hasattr(objeto, "label")


def _coletar_textos_de_componente(
    objeto,
    profundidade: int = 0,
    *,
    ignorar_botoes: bool = True,
) -> list[str]:
    """
    Percorre componentes Components V2 (TextDisplay, Section, Container, etc.)
    e extrai textos legíveis para o transcript.

    Por padrão ignora labels de botão — eles vão no bloco de botões coloridos.
    """
    if objeto is None or profundidade > 14:
        return []

    textos: list[str] = []

    if isinstance(objeto, str):
        limpo = objeto.strip()
        return [limpo] if limpo else []

    if ignorar_botoes and _eh_no_botao(objeto):
        return []

    if isinstance(objeto, dict):
        # type 2 = botão → não puxa label para o texto
        if ignorar_botoes and objeto.get("type") == 2:
            return []
        for chave in (
            "content",
            "placeholder",
            "title",
            "description",
            "name",
            "value",
        ):
            valor = objeto.get(chave)
            if isinstance(valor, str) and valor.strip():
                textos.append(valor.strip())
        # label só se NÃO for botão
        if not ignorar_botoes or objeto.get("type") != 2:
            valor_label = objeto.get("label")
            if isinstance(valor_label, str) and valor_label.strip():
                if objeto.get("type") != 2:
                    textos.append(valor_label.strip())
        for chave in ("components", "children", "items", "accessory"):
            filho = objeto.get(chave)
            if isinstance(filho, list):
                for item in filho:
                    textos.extend(
                        _coletar_textos_de_componente(
                            item,
                            profundidade + 1,
                            ignorar_botoes=ignorar_botoes,
                        )
                    )
            elif filho is not None:
                textos.extend(
                    _coletar_textos_de_componente(
                        filho,
                        profundidade + 1,
                        ignorar_botoes=ignorar_botoes,
                    )
                )
        return textos

    for atributo in (
        "content",
        "placeholder",
        "title",
        "description",
        "name",
        "value",
    ):
        valor = getattr(objeto, atributo, None)
        if isinstance(valor, str) and valor.strip():
            textos.append(valor.strip())

    if not ignorar_botoes or not _eh_no_botao(objeto):
        valor_label = getattr(objeto, "label", None)
        if isinstance(valor_label, str) and valor_label.strip():
            if not _eh_no_botao(objeto):
                textos.append(valor_label.strip())

    for atributo in ("children", "components", "items"):
        filhos = getattr(objeto, atributo, None)
        if not filhos:
            continue
        try:
            iteravel = list(filhos)
        except TypeError:
            continue
        for filho in iteravel:
            textos.extend(
                _coletar_textos_de_componente(
                    filho,
                    profundidade + 1,
                    ignorar_botoes=ignorar_botoes,
                )
            )

    dados_brutos = getattr(objeto, "_data", None)
    if isinstance(dados_brutos, dict):
        textos.extend(
            _coletar_textos_de_componente(
                dados_brutos,
                profundidade + 1,
                ignorar_botoes=ignorar_botoes,
            )
        )

    return textos


def _texto_da_mensagem_discord(mensagem: discord.Message) -> str:
    """
    Monta o texto visível da mensagem.

    Mensagens Components V2 do bot costumam ter content vazio —
    o texto fica nos TextDisplay / Section dos componentes.
    Labels de botão não entram aqui.
    """
    partes: list[str] = []

    if mensagem.content and mensagem.content.strip():
        partes.append(mensagem.content.strip())

    componentes = getattr(mensagem, "components", None) or []
    for componente in componentes:
        partes.extend(_coletar_textos_de_componente(componente, ignorar_botoes=True))

    dados = getattr(mensagem, "_data", None)
    if isinstance(dados, dict):
        for componente in dados.get("components") or []:
            partes.extend(
                _coletar_textos_de_componente(componente, ignorar_botoes=True)
            )

    vistos: set[str] = set()
    unicos: list[str] = []
    for texto in partes:
        chave = texto.strip()
        if not chave or chave in vistos:
            continue
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

    - Avatar fora do card (lado esquerdo)
    - Cards alinhados à esquerda
    - Botões com cores do CardBotoesStaffView (tickets_views.py)
    - Menções como @nome em azul (link visual, sem destino)
    - Sem bloco de meta do ticket (Ticket/Categoria/Autor/…)
    """
    import html as modulo_html
    import json as modulo_json
    import re as modulo_re

    # Estilos Discord Button (API) e fallbacks alinhados a tickets_views.py
    ESTILO_BOTAO_POR_NUMERO = {
        1: "primary",
        2: "secondary",
        3: "success",
        4: "danger",
    }
    ESTILO_BOTAO_POR_ROTULO = {
        "adicionar membro": "secondary",
        "chamar membro": "secondary",
        "remover membro": "secondary",
        "mover canal": "secondary",
        "mover ticket": "secondary",
        "trocar nome do canal": "secondary",
        "adicionar observação interna": "secondary",
        "criar call de atendimento": "secondary",
        "encerrar call de atendimento": "danger",
        "assumir atendimento": "primary",
        "saudar atendimento": "secondary",
        "já saudado": "secondary",
        "transferir atendimento": "secondary",
        "finalizar ticket": "success",
    }
    EMOJI_BOTAO_POR_ROTULO = {
        "adicionar membro": "➕",
        "chamar membro": "👤",
        "remover membro": "➖",
        "mover canal": "📂",
        "mover ticket": "📂",
        "trocar nome do canal": "✏️",
        "adicionar observação interna": "📋",
        "criar call de atendimento": "📞",
        "encerrar call de atendimento": "📞",
        "assumir atendimento": "🙋",
        "saudar atendimento": "👋",
        "já saudado": "👋",
        "transferir atendimento": "🔄",
        "finalizar ticket": "✅",
    }

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

    def mapa_nomes_mencionados(mensagem: discord.Message) -> dict[str, str]:
        """id → nome legível para trocar <@id> por @nome."""
        mapa: dict[str, str] = {}
        for usuario in getattr(mensagem, "mentions", None) or []:
            nome = getattr(usuario, "display_name", None) or usuario.name
            mapa[str(usuario.id)] = nome
        if ticket.autor_discord_id and ticket.autor_nome:
            mapa[str(ticket.autor_discord_id)] = ticket.autor_nome
        if ticket.staff_assumiu_id and ticket.staff_assumiu_nome:
            mapa[str(ticket.staff_assumiu_id)] = ticket.staff_assumiu_nome
        if ticket.staff_finalizou_id and ticket.staff_finalizou_nome:
            mapa[str(ticket.staff_finalizou_id)] = ticket.staff_finalizou_nome
        return mapa

    def substituir_mencoes(texto: str, mapa_nomes: dict[str, str]) -> str:
        def trocar(match) -> str:
            id_usuario = match.group(1)
            nome = mapa_nomes.get(id_usuario)
            if nome:
                return f"@{nome}"
            return "@usuário"

        return modulo_re.sub(r"<@!?(\d+)>", trocar, texto or "")

    def markdown_simples_para_html(texto: str, mapa_nomes: dict[str, str]) -> str:
        """Escapes + menções @nome + markdown leve."""
        com_mencao = substituir_mencoes(texto, mapa_nomes)
        seguro = esc(com_mencao)
        # @nome → span azul estilo menção Discord (sem href real)
        seguro = modulo_re.sub(
            r"@([A-Za-z0-9_.\-]{2,32})",
            r"<span class='mention'>@\1</span>",
            seguro,
        )
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

    def html_emoji_botao(emoji_obj) -> str:
        """
        Monta HTML do emoji do botão (unicode ou imagem do CDN Discord).
        """
        if emoji_obj is None:
            return ""
        if isinstance(emoji_obj, str):
            return f'<span class="button-emoji">{esc(emoji_obj)}</span>'
        if isinstance(emoji_obj, dict):
            emoji_id = emoji_obj.get("id")
            nome = emoji_obj.get("name") or ""
            animado = bool(emoji_obj.get("animated"))
            if emoji_id:
                extensao = "gif" if animado else "png"
                url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extensao}"
                return (
                    f'<img class="button-emoji" src="{esc(url)}" '
                    f'alt="{esc(nome)}" loading="lazy">'
                )
            if nome:
                return f'<span class="button-emoji">{esc(nome)}</span>'
            return ""
        # PartialEmoji / Emoji do discord.py
        emoji_id = getattr(emoji_obj, "id", None)
        nome = getattr(emoji_obj, "name", None) or ""
        animado = bool(getattr(emoji_obj, "animated", False))
        if emoji_id:
            extensao = "gif" if animado else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extensao}"
            return (
                f'<img class="button-emoji" src="{esc(url)}" '
                f'alt="{esc(str(nome))}" loading="lazy">'
            )
        if nome:
            return f'<span class="button-emoji">{esc(str(nome))}</span>'
        return ""

    def coletar_botoes(mensagem: discord.Message) -> list[dict]:
        """Lista de {label, style, emoji_html, disabled} na ordem dos componentes."""
        botoes: list[dict] = []
        vistos: set[str] = set()

        def adicionar(
            rotulo: str,
            estilo_num: int | None,
            desativado: bool,
            emoji_obj,
        ) -> None:
            chave = rotulo.strip().lower()
            if not chave or chave in vistos:
                return
            if chave.startswith("ticket:"):
                return
            vistos.add(chave)
            estilo = ESTILO_BOTAO_POR_NUMERO.get(estilo_num or 2, "secondary")
            if chave in ESTILO_BOTAO_POR_ROTULO:
                estilo = ESTILO_BOTAO_POR_ROTULO[chave]
            elif chave.startswith("assumido por"):
                estilo = "secondary"
                desativado = True
            emoji_html = html_emoji_botao(emoji_obj)
            if not emoji_html:
                fallback = EMOJI_BOTAO_POR_ROTULO.get(chave, "")
                if chave.startswith("assumido por"):
                    fallback = fallback or "🙋"
                if fallback:
                    emoji_html = f'<span class="button-emoji">{esc(fallback)}</span>'
            botoes.append(
                {
                    "label": rotulo,
                    "style": estilo,
                    "emoji_html": emoji_html,
                    "disabled": desativado,
                }
            )

        def percorrer_dict(no) -> None:
            if not isinstance(no, dict):
                return
            # type 2 = Button no payload da API
            if no.get("type") == 2 and no.get("label"):
                adicionar(
                    str(no["label"]),
                    no.get("style"),
                    bool(no.get("disabled")),
                    no.get("emoji"),
                )
            for chave in ("components", "children", "items"):
                filhos = no.get(chave)
                if isinstance(filhos, list):
                    for filho in filhos:
                        percorrer_dict(filho)
                elif isinstance(filhos, dict):
                    percorrer_dict(filhos)

        def percorrer_objeto(no, profundidade: int = 0) -> None:
            if no is None or profundidade > 14:
                return
            if isinstance(no, dict):
                percorrer_dict(no)
                return
            if _eh_no_botao(no):
                rotulo = getattr(no, "label", None)
                if rotulo:
                    estilo_attr = getattr(no, "style", None)
                    estilo_num = getattr(estilo_attr, "value", None)
                    if estilo_num is None and isinstance(estilo_attr, int):
                        estilo_num = estilo_attr
                    adicionar(
                        str(rotulo),
                        estilo_num,
                        bool(getattr(no, "disabled", False)),
                        getattr(no, "emoji", None),
                    )
            for atributo in ("children", "components", "items"):
                filhos = getattr(no, atributo, None)
                if not filhos:
                    continue
                try:
                    lista_filhos = list(filhos)
                except TypeError:
                    continue
                for filho in lista_filhos:
                    percorrer_objeto(filho, profundidade + 1)
            dados_internos = getattr(no, "_data", None)
            if isinstance(dados_internos, dict):
                percorrer_dict(dados_internos)

        # 1) payload bruto da mensagem (mais confiável para style/emoji)
        dados = getattr(mensagem, "_data", None)
        if isinstance(dados, dict):
            for comp in dados.get("components") or []:
                percorrer_dict(comp)

        # 2) objetos tipados do discord.py
        for componente in getattr(mensagem, "components", None) or []:
            percorrer_objeto(componente)

        return botoes

    ids_staff: set[int] = set()
    if ticket.staff_assumiu_id:
        ids_staff.add(int(ticket.staff_assumiu_id))
    if ticket.staff_finalizou_id:
        ids_staff.add(int(ticket.staff_finalizou_id))

    nome_guilda = esc(guilda.name if guilda else "CMS Valley")
    icone = esc(url_icone_guilda())

    css = """
@import url('https://fonts.googleapis.com/css2?family=Rock+Salt&display=swap');
:root {
  --bg: #36393f;
  --card: #2b2d31;
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
  padding: 20px 24px 40px;
  overflow-y: auto;
  position: relative;
}
#particles-js {
  position: fixed; top: 0; left: 0; width: 100%; height: 100%;
  z-index: -1; pointer-events: none;
}
.page {
  position: relative; z-index: 1;
  max-width: 920px; margin: 0; /* alinhado à esquerda */
  width: 100%;
}
.header { text-align: left; margin-bottom: 24px; }
.header-box {
  padding: 8px 0 12px;
  display: flex; flex-direction: column; align-items: flex-start;
}
.guild-avatar {
  border-radius: 50%; width: 72px; height: 72px; margin-bottom: 10px;
  box-shadow: 0 0 15px var(--cyan-glow);
  animation: border-glow 2s infinite;
}
.guild-name {
  font-family: 'Rock Salt', cursive;
  font-size: 1.45em; font-weight: 700; margin-top: 6px;
  text-shadow: 0 0 15px var(--cyan-glow);
  animation: text-glow 2s infinite;
  color: #fff;
}
.channel-info { font-size: 1.05em; margin-top: 4px; color: #b0b0b0; }
.chat {
  display: flex; flex-direction: column; gap: 0;
  align-items: flex-start; width: 100%;
}
/* Avatar fora do card — igual ao modelo */
.message-container {
  display: flex;
  align-items: flex-start;
  margin-bottom: 20px;
  width: 100%;
}
.avatar {
  width: 40px; height: 40px; border-radius: 50%; flex-shrink: 0;
  border: 1px solid var(--cyan);
  box-shadow: 2px 2px 3px 3px var(--cyan-shadow);
  margin-right: 10px;
}
.message-content {
  display: flex;
  flex-direction: column;
  gap: 0;
  width: 100%;
  min-width: 0;
}
.message {
  margin-bottom: 0;
  padding: 16px 18px;
  border-radius: 10px;
  max-width: 70%;
  align-self: flex-start;
  background-color: #333;
  border: 1px solid var(--cyan);
  box-shadow: 6px 4px 3px 3px var(--cyan-shadow);
}
.message.bot, .message.staff, .message.autor {
  background-color: #333;
  border: 1px solid var(--cyan);
}
.author-timestamp {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 8px;
  margin-bottom: 8px;
}
.author { font-weight: 700; color: var(--cyan); }
.timestamp { font-size: .8em; color: var(--muted); }
.bot-tag, .autor-tag, .staff-tag {
  font-size: 0.75em; padding: 2px 6px; border-radius: 3px;
  color: #fff; display: inline;
}
.bot-tag { background-color: #02f2ff6e; }
.autor-tag { background-color: #18f30e78; }
.staff-tag { background-color: #ff4500; }
.content-message, .content {
  overflow-wrap: break-word; word-break: break-word;
  width: auto; max-width: 100%; font-size: 14px;
  white-space: pre-wrap; color: var(--text);
}
.content-message .h1, .content .h1 {
  font-size: 1.5em; font-weight: 700; margin: 4px 0 8px; color: #fff;
}
.content-message .h2, .content .h2 {
  font-size: 1.25em; font-weight: 700; margin: 4px 0 6px; color: #fff;
}
.content-message .h3, .content .h3 {
  font-size: 1em; font-weight: 600; margin: 4px 0; color: #e8eaed;
}
.content-message .muted, .content .muted {
  color: var(--muted); font-size: 0.85rem;
}
.content-message code, .content code {
  background: #1e1f22; padding: 1px 5px; border-radius: 4px;
  font-family: ui-monospace, monospace; font-size: 0.85em;
}
.content-message pre.code-block, .content pre.code-block {
  background: #1e1f22; border: 1px solid #4f545c; border-radius: 8px;
  padding: 10px 12px; overflow-x: auto; white-space: pre-wrap;
  font-family: ui-monospace, monospace; font-size: 0.82rem; margin: 8px 0;
}
.mention {
  color: #00aff4;
  background: rgba(0, 175, 244, 0.12);
  border-radius: 3px;
  padding: 0 2px;
  font-weight: 500;
  cursor: default;
  text-decoration: none;
}
.attachments { margin-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.attachments img {
  max-width: min(440px, 100%); border-radius: 10px; display: block;
  border: 1px solid rgba(0,175,244,0.35);
}
.file-link {
  display: inline-flex; align-items: center; padding: 5px 10px;
  border: 1px solid #4f545c; border-radius: 5px; text-decoration: none;
  color: #dcddde; background-color: #2b2d31; margin-top: 6px;
}
.embed {
  border-radius: 10px; padding: 10px; margin-top: 10px;
  background-color: #1e1f22; border: 1px solid #4f545c;
  box-shadow: 0 2px 6px rgba(0,0,0,0.15); max-width: 520px;
}
.embed-title { font-weight: 700; color: var(--cyan); }
.embed-description {
  margin-top: 5px; color: #c5c6c7; overflow-wrap: break-word;
  font-size: 14px; white-space: pre-wrap;
}
/* Bloco Components V2 + botões (modelo) */
.message-container-v2 {
  background-color: #2f3136;
  border-radius: 8px;
  padding: 12px;
  margin-top: 8px;
  border-left: 4px solid #5865f2;
}
.container-text-block { margin-bottom: 8px; color: var(--text); font-size: 14px; }
.small-text {
  color: #b0b0b0;
  font-size: 0.85rem;
}
.container-divider {
  border: 0;
  border-top: 1px solid rgba(255,255,255,0.1);
  margin: 10px 0;
}
.container-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
  margin-bottom: 8px;
}
.container-row:last-child { margin-bottom: 0; }
.container-button {
  display: inline-flex;
  align-items: center;
  padding: 8px 16px;
  font-weight: 500;
  font-size: 13px;
  font-family: inherit;
  cursor: default;
  user-select: none;
  margin: 2px;
  border-radius: 4px;
  border: none;
  color: #fff;
  line-height: 1.25;
}
.button-emoji {
  margin-right: 6px;
  width: 20px;
  height: 20px;
  vertical-align: middle;
  object-fit: contain;
  display: inline-block;
}
span.button-emoji {
  width: auto;
  height: auto;
  font-size: 15px;
  line-height: 1;
}
.button-primary { background-color: #5865f2; color: #fff; }
.button-secondary { background-color: #4f545c; color: #fff; }
.button-success { background-color: #43b581; color: #fff; }
.button-danger { background-color: #f04747; color: #fff; }
.container-button.disabled {
  opacity: 0.55;
  filter: grayscale(0.12);
}
.footer {
  text-align: left; margin-top: 28px; font-size: 12px; color: #b0b0b0;
}
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
  body { padding: 12px; }
  .message { max-width: 100%; padding: 12px; }
  .message-container { flex-direction: column; }
  .avatar { margin-bottom: 8px; }
  .attachments img { max-width: 100%; }
  .bot-tag, .autor-tag, .staff-tag { font-size: 0.7em; padding: 1px 3px; }
  .container-button { padding: 7px 12px; font-size: 12px; }
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
        "<main class='chat'>",
    ]

    for mensagem in mensagens:
        autor = mensagem.author
        nome = esc(getattr(autor, "display_name", None) or autor.name)
        avatar = esc(url_avatar(autor))
        quando = formatar_horario(mensagem.created_at)
        mapa_nomes = mapa_nomes_mencionados(mensagem)

        classes_mensagem = ["message"]
        tags_html: list[str] = []
        if autor.bot:
            classes_mensagem.append("bot")
            tags_html.append("<span class='bot-tag'>BOT</span>")
        if autor.id == ticket.autor_discord_id:
            classes_mensagem.append("autor")
            tags_html.append("<span class='autor-tag'>Autor</span>")
        if autor.id in ids_staff:
            classes_mensagem.append("staff")
            tags_html.append("<span class='staff-tag'>STAFF</span>")

        botoes = coletar_botoes(mensagem)
        rotulos_botoes = {b["label"].strip().lower() for b in botoes}

        # Texto sem labels de botão (botões vão em container-row)
        texto_bruto = _texto_da_mensagem_discord(mensagem)
        if texto_bruto and rotulos_botoes:
            linhas_filtradas: list[str] = []
            for linha in texto_bruto.split("\n"):
                if linha.strip().lower() in rotulos_botoes:
                    continue
                if linha.strip().lower().startswith("assumido por:"):
                    continue
                linhas_filtradas.append(linha)
            texto_bruto = "\n".join(linhas_filtradas).strip()

        # message-container → avatar fora + message-content → message
        partes.append("<div class='message-container'>")
        partes.append(f"<img class='avatar' src='{avatar}' alt='' loading='lazy'>")
        partes.append("<div class='message-content'>")
        partes.append(f"<div class='{' '.join(classes_mensagem)}'>")
        partes.append("<div class='author-timestamp'>")
        partes.append(f"<span class='author'>{nome}</span>")
        partes.append(f"<span class='timestamp'>{quando}</span>")
        partes.extend(tags_html)
        partes.append("</div>")

        # Mensagem só com botões de staff: texto curto entra no bloco V2
        texto_opcoes_staff = ""
        if botoes and texto_bruto:
            linhas_texto = [linha for linha in texto_bruto.split("\n") if linha.strip()]
            if (
                len(linhas_texto) == 1
                and "opções exclusivas" in linhas_texto[0].lower()
            ):
                texto_opcoes_staff = linhas_texto[0].strip()
                texto_bruto = ""

        # Texto simples (usuário / staff) fica em content-message
        if texto_bruto and not botoes:
            partes.append(
                f"<div class='content-message'>"
                f"{markdown_simples_para_html(texto_bruto, mapa_nomes)}"
                f"</div>"
            )
        elif texto_bruto and botoes:
            # Texto + botões: texto dentro do container V2
            pass

        for embed in mensagem.embeds[:6]:
            titulo = esc(embed.title or "")
            desc_raw = embed.description or ""
            desc = markdown_simples_para_html(desc_raw, mapa_nomes) if desc_raw else ""
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

        if botoes or (texto_bruto and autor.bot):
            # Bloco Components V2 (texto + botões coloridos) — estrutura do modelo
            partes.append("<div class='message-container-v2'>")
            if texto_opcoes_staff:
                partes.append(
                    "<div class='container-text-block'>"
                    f"<span class='small-text'>{esc(texto_opcoes_staff)}</span>"
                    "</div>"
                    "<hr class='container-divider'>"
                )
            elif texto_bruto:
                partes.append(
                    "<div class='container-text-block'>"
                    f"{markdown_simples_para_html(texto_bruto, mapa_nomes)}"
                    "</div>"
                )
                if botoes:
                    partes.append("<hr class='container-divider'>")

            if botoes:
                partes.append("<div class='container-row'>")
                for indice, botao in enumerate(botoes):
                    if indice > 0 and indice % 4 == 0:
                        partes.append("</div><div class='container-row'>")
                    classes_btn = f"container-button button-{botao['style']}"
                    if botao["disabled"]:
                        classes_btn += " disabled"
                    partes.append(
                        f"<div class='{classes_btn}'>"
                        f"{botao.get('emoji_html') or ''}"
                        f"{esc(botao['label'])}"
                        f"</div>"
                    )
                partes.append("</div>")
            partes.append("</div>")

        if (
            not texto_bruto
            and not texto_opcoes_staff
            and not mensagem.attachments
            and not any(e.title or e.description for e in mensagem.embeds)
            and not botoes
        ):
            partes.append(
                "<div class='content-message'>"
                "<em>(mensagem sem texto legível)</em></div>"
            )

        partes.append("</div>")  # .message
        partes.append("</div>")  # .message-content
        partes.append("</div>")  # .message-container

    partes.append("</main>")
    partes.append(
        "<footer class='footer'>CMS Valley · Transcript gerado automaticamente</footer>"
    )
    partes.append("</div>")  # page

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
