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

    Evita duplicar: prefere payload bruto (_data); se vazio, usa objetos.
    """
    partes: list[str] = []

    if mensagem.content and mensagem.content.strip():
        partes.append(mensagem.content.strip())

    # Uma fonte só para componentes — evita texto em dobro
    dados = getattr(mensagem, "_data", None)
    textos_componentes: list[str] = []
    if isinstance(dados, dict) and dados.get("components"):
        for componente in dados.get("components") or []:
            textos_componentes.extend(
                _coletar_textos_de_componente(componente, ignorar_botoes=True)
            )
    if not textos_componentes:
        for componente in getattr(mensagem, "components", None) or []:
            textos_componentes.extend(
                _coletar_textos_de_componente(componente, ignorar_botoes=True)
            )
    partes.extend(textos_componentes)

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

    def mapa_usuarios_mencionados(
        mensagem: discord.Message,
        texto_extra: str = "",
    ) -> dict[str, str]:
        """id → username Discord (não apelido do servidor)."""
        mapa: dict[str, str] = {}
        for usuario in getattr(mensagem, "mentions", None) or []:
            mapa[str(usuario.id)] = nome_usuario_discord(usuario)
        try:
            mapa[str(mensagem.author.id)] = nome_usuario_discord(mensagem.author)
        except Exception:
            pass
        # Resolve IDs soltos no texto via cache da guilda
        texto_busca = f"{mensagem.content or ''}\n{texto_extra or ''}"
        if guilda is not None:
            for match in modulo_re.finditer(r"<@!?(\d+)>", texto_busca):
                id_usuario = match.group(1)
                if id_usuario in mapa:
                    continue
                membro = guilda.get_member(int(id_usuario))
                if membro is not None:
                    mapa[id_usuario] = nome_usuario_discord(membro)
        return mapa

    def mapa_cargos_mencionados(
        mensagem: discord.Message,
        texto_extra: str = "",
    ) -> dict[str, tuple[str, str]]:
        """id → (nome_do_cargo, cor_hex)."""
        mapa: dict[str, tuple[str, str]] = {}

        def registrar_cargo(cargo) -> None:
            if cargo is None:
                return
            cor = getattr(cargo, "color", None)
            cor_valor = getattr(cor, "value", 0) or 0
            cor_hex = f"#{cor_valor:06x}" if cor_valor else "#99aab5"
            mapa[str(cargo.id)] = (cargo.name, cor_hex)

        for cargo in getattr(mensagem, "role_mentions", None) or []:
            registrar_cargo(cargo)

        # Resolve IDs que ainda aparecem no texto (content ou components)
        texto_busca = f"{mensagem.content or ''}\n{texto_extra or ''}"
        if guilda is not None:
            for match in modulo_re.finditer(r"<@&(\d+)>", texto_busca):
                id_cargo = match.group(1)
                if id_cargo in mapa:
                    continue
                registrar_cargo(guilda.get_role(int(id_cargo)))
        return mapa

    def preparar_texto_com_mencoes(
        texto: str,
        mapa_usuarios: dict[str, str],
        mapa_cargos: dict[str, tuple[str, str]],
    ) -> str:
        """
        Troca tokens Discord por marcadores temporários antes do escape HTML.
        Usuários → {{U:id}}  |  Cargos → {{R:id}}  |  Emojis custom → {{E:...}}
        """
        if not texto:
            return ""

        def trocar_usuario(match) -> str:
            return f"{{{{U:{match.group(1)}}}}}"

        def trocar_cargo(match) -> str:
            return f"{{{{R:{match.group(1)}}}}}"

        def trocar_emoji(match) -> str:
            animado = match.group(1) == "a"
            nome = match.group(2)
            emoji_id = match.group(3)
            return f"{{{{E:{'a' if animado else 's'}:{nome}:{emoji_id}}}}}"

        saida = texto
        saida = modulo_re.sub(r"<@!?(\d+)>", trocar_usuario, saida)
        saida = modulo_re.sub(r"<@&(\d+)>", trocar_cargo, saida)
        saida = modulo_re.sub(r"<(a?):([A-Za-z0-9_]+):(\d+)>", trocar_emoji, saida)
        return saida

    def markdown_simples_para_html(
        texto: str,
        mapa_usuarios: dict[str, str],
        mapa_cargos: dict[str, tuple[str, str]],
    ) -> str:
        """
        Converte markdown Discord (o mesmo dos TextDisplay em tickets_views)
        para HTML.

        Suporta:
        - # / ## / ### (títulos)
        - -# (subtexto muted)
        - **negrito** / __sublinhado__ / *itálico* / _itálico_
        - ~~riscado~~ / ||spoiler||
        - `código` / ```bloco```
        - listas com - no início da linha
        - menções user/cargo e emoji custom
        """
        preparado = preparar_texto_com_mencoes(texto, mapa_usuarios, mapa_cargos)
        seguro = esc(preparado)

        # Emoji custom Discord → <img>
        def html_emoji(match) -> str:
            animado = match.group(1) == "a"
            nome = match.group(2)
            emoji_id = match.group(3)
            extensao = "gif" if animado else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extensao}"
            return (
                f"<img class='emoji' src='{esc(url)}' "
                f"alt=':{esc(nome)}:' loading='lazy'>"
            )

        seguro = modulo_re.sub(
            r"\{\{E:(a|s):([A-Za-z0-9_]+):(\d+)\}\}",
            html_emoji,
            seguro,
        )

        def html_usuario(match) -> str:
            id_usuario = match.group(1)
            nome = mapa_usuarios.get(id_usuario) or "usuário"
            return f"<span class='author'>@{esc(nome)}</span>"

        seguro = modulo_re.sub(r"\{\{U:(\d+)\}\}", html_usuario, seguro)

        def html_cargo(match) -> str:
            id_cargo = match.group(1)
            dados_cargo = mapa_cargos.get(id_cargo)
            if dados_cargo:
                nome_cargo, cor_hex = dados_cargo
                return (
                    f"<span class='role' style='color:{esc(cor_hex)};"
                    f"background-color:{esc(cor_hex)}22'>"
                    f"@{esc(nome_cargo)}</span>"
                )
            return "<span class='role'>@cargo</span>"

        seguro = modulo_re.sub(r"\{\{R:(\d+)\}\}", html_cargo, seguro)

        # Blocos de código primeiro (não mexer no interior)
        blocos_codigo: list[str] = []

        def guardar_bloco(match) -> str:
            indice = len(blocos_codigo)
            blocos_codigo.append(f"<pre class='code-block'>{match.group(1)}</pre>")
            return f"{{{{CODE{indice}}}}}"

        seguro = modulo_re.sub(
            r"```(?:\w+)?\n?(.*?)```",
            guardar_bloco,
            seguro,
            flags=modulo_re.DOTALL,
        )

        # Código inline
        seguro = modulo_re.sub(r"`([^`\n]+)`", r"<code>\1</code>", seguro)

        # Títulos e subtexto (ordem: -# → ### → ## → #)
        # Tags alinhadas ao HTML final do modelo (h1/h2/h3 + classes)
        seguro = modulo_re.sub(
            r"(?m)^-#\s+(.+)$",
            r"<span class='small-text'>\1</span>",
            seguro,
        )
        seguro = modulo_re.sub(
            r"(?m)^###\s+(.+)$",
            r"<h3 class='h3'>\1</h3>",
            seguro,
        )
        seguro = modulo_re.sub(
            r"(?m)^##\s+(.+)$",
            r"<h2 class='h2'>\1</h2>",
            seguro,
        )
        seguro = modulo_re.sub(
            r"(?m)^#\s+(.+)$",
            r"<h1 class='h1'>\1</h1>",
            seguro,
        )

        # Listas com hífen no início da linha (não confundir com -#)
        seguro = modulo_re.sub(
            r"(?m)^-\s+(.+)$",
            r"<li class='list-item'>\1</li>",
            seguro,
        )

        # Formatação inline (mais externo → interno)
        # **__texto__** e __**texto**__
        seguro = modulo_re.sub(
            r"\*\*__(.+?)__\*\*",
            r"<strong><u>\1</u></strong>",
            seguro,
        )
        seguro = modulo_re.sub(
            r"__\*\*(.+?)\*\*__",
            r"<u><strong>\1</strong></u>",
            seguro,
        )
        seguro = modulo_re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", seguro)
        seguro = modulo_re.sub(r"__(.+?)__", r"<u>\1</u>", seguro)
        seguro = modulo_re.sub(r"~~(.+?)~~", r"<s>\1</s>", seguro)
        seguro = modulo_re.sub(
            r"\|\|(.+?)\|\|",
            r"<span class='spoiler'>\1</span>",
            seguro,
        )
        # Itálico: *texto* ou _texto_ (evitar confundir com **)
        seguro = modulo_re.sub(
            r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)",
            r"<em>\1</em>",
            seguro,
        )
        seguro = modulo_re.sub(
            r"(?<![A-Za-z0-9_])_(?!_)(.+?)(?<!_)_(?![A-Za-z0-9_])",
            r"<em>\1</em>",
            seguro,
        )

        # Restaura blocos de código
        for indice, bloco in enumerate(blocos_codigo):
            seguro = seguro.replace(f"{{{{CODE{indice}}}}}", bloco)

        # Quebras de linha
        seguro = seguro.replace("\n", "<br>")
        # Limpa <br> excessivo em torno de títulos / listas / pre
        seguro = modulo_re.sub(
            r"<br>\s*(?=<(?:h[123]|li|pre|hr)\b)",
            "",
            seguro,
        )
        seguro = modulo_re.sub(
            r"(</(?:h[123]|li|pre)>)<br>",
            r"\1",
            seguro,
        )

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
/* ============ IMPORTS ============ */
			@import url("https://fonts.googleapis.com/css?family=Montserrat:400,600,700|Work+Sans:300,400,700,900");
			@import url("https://fonts.googleapis.com/css2?family=Rock+Salt&display=swap");
			/* ============ KEYFRAMES ============ */
			@keyframes border-glow {
				0% {
					box-shadow: rgba(0, 175, 244, 0.7) 0px
						0px 15px;
				}
				50% {
					box-shadow: rgba(0, 175, 244, 0.9) 0px
						0px 30px;
				}
				100% {
					box-shadow: rgba(0, 175, 244, 0.7) 0px
						0px 15px;
				}
			}
			@keyframes gradient {
				0% {
					background-position: 0% 50%;
				}
				50% {
					background-position: 100% 50%;
				}
				100% {
					background-position: 0% 50%;
				}
			}
			@keyframes pulse {
				0% {
					transform: scale(1);
				}
				50% {
					transform: scale(1.05);
				}
				100% {
					transform: scale(1);
				}
			}
			@keyframes shake {
				0% {
					transform: translateX(0px);
				}
				25% {
					transform: translateX(-5px);
					box-shadow: rgba(220, 53, 69, 0.8) 0px
						0px 10px;
				}
				50% {
					transform: translateX(5px);
					box-shadow: rgba(220, 53, 69, 0.8) 0px
						0px 10px;
				}
				75% {
					transform: translateX(-5px);
					box-shadow: rgba(220, 53, 69, 0.8) 0px
						0px 10px;
				}
				100% {
					transform: translateX(0px);
				}
			}
			@keyframes text-glow {
				0% {
					text-shadow: rgba(0, 175, 244, 0.7) 0px
						0px 15px;
				}
				50% {
					text-shadow: rgba(0, 175, 244, 0.9) 0px
						0px 30px;
				}
				100% {
					text-shadow: rgba(0, 175, 244, 0.7) 0px
						0px 15px;
				}
			}
			@keyframes zoom {
				0% {
					transform: scale(0);
				}
				100% {
					transform: scale(1);
				}
			}
			@-webkit-keyframes zoom {
				0% {
					transform: scale(0);
				}
				100% {
					transform: scale(1);
				}
			} /* ============ GLOBAL
============ */
			body {
				font-family:
					Whitney, "Helvetica Neue", Helvetica,
					Arial, sans-serif;
				background-color: rgb(54, 57, 63);
				color: rgb(220, 221, 222);
				font-size: 17px;
				margin: 0px;
				padding: 20px;
				overflow-y: auto;
			}
			.hidden-content {
				display: none;
			}
			.h1 {
				font-size: 1.5em;
			}
			.h2 {
				font-size: 1.25em;
			}
			.h3 {
				font-size: 1em;
			} /* ============ IDS ============ */
			#avisoSenha {
				display: none;
				color: rgb(220, 53, 69);
				font-weight: bold;
				margin-top: 10px;
				text-align: center;
			}
			#avisoSenha i {
				margin-right: 5px;
			}
			#caption {
				margin: auto;
				display: block;
				width: 80%;
				max-width: 700px;
				text-align: center;
				color: rgb(204, 204, 204);
				padding: 10px 0px;
				height: 150px;
			}
			#containerFree {
				user-select: none;
				outline-width: 0px;
				height: 100vh;
				display: flex;
				-webkit-box-pack: center;
				justify-content: center;
				-webkit-box-align: center;
				align-items: center;
				font-family: Montserrat !important;
				background-size: cover !important;
			}
			#containerFree,
			.acceptContainer::before,
			#logoContainer::before {
				background: url("https://i.imgur.com/Zce0Nxi.jpeg")
					center center fixed;
			}
			#inviteContainer {
				display: flex;
				overflow: hidden;
				position: relative;
				border-radius: 15px;
				height: auto;
			}
			#inviteContainer .acceptContainer {
				padding: 5%;
				width: 100%;
				margin-left: -100%;
				overflow: hidden;
				height: 0px;
				opacity: 0;
			}
			#inviteContainer .acceptContainer.loadIn {
				opacity: 1;
				margin-left: 0px;
				transition: 0.5s;
			}
			#inviteContainer .acceptContainer::before {
				content: "";
				box-shadow: rgba(40, 43, 48, 0.75) 0px 0px 0px
					3000px inset;
				filter: blur(10px);
				position: absolute;
				width: 150%;
				height: 150%;
				top: -50px;
				left: -50px;
				background-size: cover !important;
			}
			#myImg {
				border-radius: 15px;
				cursor: grab;
				transition: 0.5s;
			}
			#myImg:hover {
				opacity: 0.7;
			}
			#particles-js {
				position: fixed;
				top: 0px;
				left: 0px;
				width: 100%;
				height: 100%;
				z-index: -1;
			}
			#passwordContainer.error {
				animation: 0.5s ease-in-out 0s 1 normal none
					running shake;
			} /* ============ CLASSES - A ============ */
			.acceptContainer.loadIn {
				opacity: 1;
				margin-left: 0px;
				transition: 0.5s;
			}
			.animated-emoji {
				image-rendering: auto;
			}
			.author {
				font-weight: 700;
				color: rgb(0, 175, 244);
			}
			.author:hover {
				background-color: rgb(79, 84, 92);
				cursor: pointer;
			}
			.author-timestamp {
				align-items: baseline;
			}
			.autor-tag {
				background-color: rgba(24, 243, 14, 0.47);
				color: rgb(255, 255, 255);
				padding: 2px 5px;
				border-radius: 3px;
				font-size: 0.8em;
				display: inline;
			}
			.avatar {
				border: 1px solid rgb(0, 175, 244);
				box-shadow: rgba(0, 175, 244, 0.22) 2px 2px 3px
					3px;
				width: 40px;
				height: 40px;
				border-radius: 50%;
				margin-right: 10px;
			} /*
============ CLASSES - B ============ */
			.bot-tag {
				background-color: rgba(2, 242, 255, 0.43);
				color: rgb(255, 255, 255);
				padding: 2px 5px;
				border-radius: 3px;
				font-size: 0.8em;
				display: inline;
			}
			.button-disabled {
				opacity: 0.5;
				cursor: not-allowed;
			}
			.button-emoji {
				margin-right: 6px;
				width: 20px;
				height: 20px;
				vertical-align: middle;
			}
			.button-emoji img {
				width: 100%;
				height: 100%;
				object-fit: contain;
			}
			.button-primary {
				background-color: rgb(88, 101, 242);
				color: white;
			}
			.button-secondary {
				background-color: rgb(79, 84, 92);
				color: white;
			}
			.button-success {
				background-color: rgb(67, 181, 129);
				color: white;
			}
			.button-danger {
				background-color: rgb(240, 71, 71);
				color: white;
			} /*
============ CLASSES - C ============ */
			.carregando {
				display: flex;
				flex-direction: column;
				align-items: center;
				justify-content: center;
				height: 100%;
				width: 100%;
				position: fixed;
				top: 0px;
				left: 0px;
				z-index: 9999;
			}
			.carregando i {
				font-size: 48px;
				color: rgb(0, 175, 244);
				margin-bottom: 20px;
			}
			.carregando-text {
				font-size: 24px;
				font-weight: bold;
				color: rgb(0, 175, 244);
			}
			.channel {
				color: rgb(0, 175, 244);
				text-decoration: none;
				padding: 2px 5px;
				border-radius: 5px;
				font-weight: 500;
				transition: background-color 0.2s;
			}
			.channel:hover {
				background-color: rgb(79, 84, 92);
				cursor: pointer;
			}
			.channel-info {
				font-size: 1.2em;
				margin-top: 5px;
				color: rgb(176, 176, 176);
			}
			.close {
				position: absolute;
				top: 15px;
				right: 35px;
				color: rgb(241, 241, 241);
				font-size: 40px;
				font-weight: bold;
				transition: 0.3s;
			}
			.close:hover,
			.close:focus {
				color: rgb(187, 187, 187);
				text-decoration: none;
				cursor: pointer;
			}
			code {
				background: #1e1f22;
				padding: 1px 5px;
				border-radius: 4px;
				font-family: ui-monospace, monospace;
				font-size: 0.85em;
				color: #dcddde;
			}
			.container-button {
				display: inline-flex;
				align-items: center;
				padding: 8px 16px;
				font-weight: 500;
				cursor: pointer;
				margin: 2px;
			}
			.container-divider {
				border: 0px;
				height: 1px;
				background-color: rgba(255, 255, 255, 0.1);
				margin: 8px 0px;
			}
			.container-row {
				display: flex;
				flex-wrap: wrap;
				gap: 4px;
				align-items: center;
				margin-bottom: 8px;
			}
			.container-row-content {
				flex-grow: 1;
			}
			.container-text-block {
				margin-bottom: 8px;
			}
			.content-message {
				overflow-wrap: break-word; /* Permite quebrar palavras longas */
				word-break: normal; /* Volta ao comportamento padrão */
				word-wrap: break-word; /* Garante compatibilidade com navegadores antigos */
				white-space: normal; /* Permite quebra de linha automática */

				width: auto;
				max-width: 100%;
				font-size: 14px;
				margin-top: 5px;
				color: rgb(220, 221, 222);
			}
			.contentnew {
				display: none;
				margin-top: 5px;
				color: rgb(220, 221, 222);
			}
			.custom-id-info {
				font-size: 12px;
				color: rgb(185, 187, 190);
				margin-top: 4px;
				font-style: italic;
			} /* ============ CLASSES - D ============ */
			.desbloquear {
				color: black;
				display: block;
				margin: 0px auto;
				border-radius: 20px;
				width: 70%;
				height: 7vh;
				animation: 1.5s ease 0s infinite normal none
					running pulse;
				box-shadow: rgb(57, 228, 215) 0px 0px 5px;
				background: linear-gradient(
						45deg,
						rgb(0, 242, 255),
						rgb(255, 255, 255),
						rgb(3, 226, 255)
					)
					0% 0% / 400% 400%;
				transition: background-position 0.5s;
				cursor: pointer;
			}
			.desbloquear:hover {
				transform: scale(1.05);
				background-position: 100% 50%;
			} /* ============ CLASSES
- E ============ */
			.embed {
				border-radius: 10px;
				padding: 10px;
				margin-top: 10px;
				background-color: rgb(37, 39, 41);
				border: 1px solid rgb(79, 84, 92);
				box-shadow: rgba(0, 0, 0, 0.15) 0px 2px 6px;
			}
			.embed-author {
				font-weight: 700;
				color: rgb(0, 175, 244);
			}
			.embed-container {
				align-self: flex-start;
			}
			.embed-description {
				margin-top: 5px;
				color: rgb(197, 198, 199);
				overflow-wrap: break-word;
				word-break: break-all;
				width: auto;
				max-width: 100%;
				font-size: 14px;
			}
			.embed-field {
				margin-top: 10px;
			}
			.embed-field-name {
				font-weight: 700;
				color: rgb(255, 255, 255);
			}
			.embed-field-value {
				margin-top: 2px;
				color: rgb(197, 198, 199);
			}
			.embed-footer {
				margin-top: 10px;
				font-size: 0.8em;
				color: rgb(176, 176, 176);
			}
			.embed-image {
				margin-top: 10px;
				max-width: 100%;
				border-radius: 5px;
			}
			.embed-thumbnail {
				display: block;
			}
			.embed-thumbnail.small {
				width: 80px;
				height: 80px;
				float: right;
			}
			.embed-thumbnail.large {
				margin: 0px 0px 10px 10px;
				float: right;
				width: auto;
				height: auto;
				max-width: 100%;
			}
			.embed-title {
				font-weight: 700;
				color: rgb(0, 175, 244);
			}
			.emoji {
				max-width: 28px;
				height: auto;
				vertical-align: middle;
			}
			.emoji-placeholder {
				background-color: rgb(47, 49, 54);
				color: rgb(255, 9, 9);
				border-radius: 4px;
				line-height: 28px;
			} /* ============ CLASSES - F
============ */
			.file-link {
				display: inline-flex;
				align-items: center;
				padding: 5px 10px;
				border: 1px solid rgb(221, 221, 221);
				border-radius: 5px;
				text-decoration: none;
				color: rgb(51, 51, 51);
				background-color: rgb(249, 249, 249);
				transition: background-color 0.2s;
				margin-top: 10px;
			}
			.file-link:hover {
				background-color: rgb(240, 240, 240);
			}
			.file-link i {
				margin-right: 5px;
				font-size: 1.2em;
			}
			.footer,
			.header {
				text-align: center;
				margin-bottom: 20px;
			}
			.footer-box,
			.header-box {
				max-width: 71%;
				padding: 20px;
				display: flex;
				flex-direction: column;
				align-items: center;
			}
			.footer-text {
				text-align: center;
				margin-top: 20px;
				font-size: 10px;
				color: rgba(255, 255, 255, 0.52);
			}
			.formContainer {
				display: block;
				text-align: center;
			}
			.formContainer .formDiv {
				margin-bottom: 30px;
				left: -25px;
				opacity: 0;
				transition: 0.5s;
				position: relative;
			}
			.formContainer .formDiv.loadIn {
				opacity: 1;
				left: 0px;
			}
			.formContainer .formDiv:last-child {
				padding-top: 10px;
				margin-bottom: 0px;
			}
			.formContainer p {
				padding-bottom: 10px;
				margin: 0px;
				font-weight: 700;
				color: rgb(170, 170, 170);
				font-size: 10px;
				user-select: none;
			}
			.formContainer input[type="password"],
			.formContainer input[type="text"] {
				margin: 0px auto;
				display: inline-block;
				background: transparent;
				text-align: center;
				border-width: medium;
				border-style: none;
				border-color: currentcolor;
				border-image: none;
				padding: 20px 10px;
				box-sizing: border-box;
				color: rgb(255, 255, 255);
				width: auto;
				font-size: 200%;
				line-height: 48px;
				box-shadow: rgba(255, 255, 255, 0.15) 0px -1px
					0px inset;
			}
			.formContainer input[type="password"]:focus,
			.formContainer input[type="text"]:focus {
				outline: none;
				box-shadow: rgba(0, 242, 255, 0.5) 0px 0px 10px;
			}
			.formDiv input:focus {
				outline: none;
				box-shadow: rgb(0, 123, 255) 0px 0px 5px;
			}
			form {
				position: relative;
				text-align: center;
				height: 100%;
			}
			form h1 {
				margin: 0px 0px 15px;
				font-weight: 700;
				font-size: 20px;
				color: rgb(255, 255, 255);
				user-select: none;
				opacity: 0;
				left: -30px;
				position: relative;
				transition: 0.5s;
				font-family: "Work Sans" !important;
			}
			form h1.loadIn {
				left: 0px;
				opacity: 1;
			}
			.futuristic-bg {
				background: linear-gradient(
						45deg,
						rgb(15, 15, 15),
						rgb(26, 26, 26),
						rgb(31, 31, 31),
						rgb(42, 42, 42)
					)
					0% 0% / 400% 400%;
				animation: 10s ease 0s infinite normal none
					running gradient;
			} /* ============ CLASSES - G
============ */
			.guild-avatar {
				border-radius: 50%;
				width: 80px;
				height: 80px;
				margin-bottom: 10px;
				box-shadow: rgba(0, 175, 244, 0.7) 0px 0px 15px;
				animation: 2s ease 0s infinite normal none
					running border-glow;
			}
			.guild-name {
				font-family: "Rock Salt", cursive;
				font-size: 1.5em;
				font-weight: 700;
				margin-top: 10px;
				text-shadow: rgba(0, 175, 244, 0.7) 0px 0px 15px;
				animation: 2s ease 0s infinite normal none
					running text-glow;
			} /* ============ CLASSES - L
============ */
			.list-item {
				margin-left: 20px;
				list-style-type: disc;
			}
			.logoContainer {
				padding: 70px;
				box-sizing: border-box;
				z-index: 2;
				position: relative;
				overflow: hidden;
				display: flex;
				-webkit-box-align: center;
				align-items: center;
				-webkit-box-pack: center;
				justify-content: center;
				-webkit-box-orient: vertical;
				-webkit-box-direction: normal;
				flex-direction: column;
				transform: scale(0, 0);
			}
			.logoContainer img {
				width: 150px;
				margin-bottom: -5px;
				display: block;
				position: relative;
			}
			.logoContainer img:first-child {
				width: 150px;
			}
			.logoContainer .logo {
				position: relative;
				top: -20px;
				opacity: 0;
			}
			.logoContainer .logo.loadIn {
				top: 0px;
				opacity: 1;
				transition: 0.8s;
			}
			.logoContainer .text {
				padding: 25px 0px 10px;
				margin-top: -70px;
				opacity: 0;
			}
			.logoContainer .text.loadIn {
				margin-top: 0px;
				opacity: 1;
				transition: 0.8s;
			}
			.logoContainer::before {
				content: "";
				position: absolute;
				top: -50px;
				left: -50px;
				width: 150%;
				height: 150%;
				background-size: cover !important;
			} /* ============ CLASSES - M ============ */
			.message {
				margin-bottom: 20px;
				padding: 20px;
				border-radius: 10px;
				max-width: 70%;
				align-self: flex-start;
				box-shadow: rgba(0, 175, 244, 0.22) 6px 4px 3px
					3px;
			}
			.message.autor {
				border: 1px solid rgb(0, 175, 244);
				background-color: rgb(51, 51, 51);
			}
			.message.bot {
				border: 1px solid rgb(0, 175, 244);
				background-color: rgb(51, 51, 51);
			}
			.message.staff {
				border: 1px solid rgb(0, 175, 244);
				background-color: rgb(51, 51, 51);
				align-self: flex-end;
				text-align: left;
				padding: 10px;
				margin-bottom: 10px;
			}
			.message-container {
				display: flex;
				align-items: flex-start;
				margin-bottom: 20px;
				width: 100%;
			}
			.message-container-v2 {
				background-color: rgb(47, 49, 54);
				border-radius: 8px;
				padding: 12px;
				margin-top: 8px;
				border-left: 4px solid rgb(88, 101, 242);
			}
			.message-content {
				flex-direction: column;
				gap: 10px;
				width: 100%;
			}
			.modal {
				display: none;
				position: fixed;
				z-index: 1;
				padding-top: 100px;
				left: 0px;
				top: 0px;
				width: 100%;
				height: 100%;
				overflow: auto;
				background-color: rgba(0, 0, 0, 0.9);
			}
			.modal-content {
				border-radius: 15px;
				margin: auto;
				display: block;
				width: 80%;
				max-width: 50vh;
			}
			.modal-content,
			#caption {
				animation-name: zoom;
				animation-duration: 0.6s;
			} /* ============ CLASSES - P ============ */
			pre.code-block {
				background: #1e1f22;
				border: 1px solid #4f545c;
				border-radius: 8px;
				padding: 10px 12px;
				overflow-x: auto;
				white-space: pre-wrap;
				font-family: ui-monospace, monospace;
				font-size: 0.82rem;
				margin: 8px 0;
				color: #dcddde;
			}
			.pdf-preview {
				border: 1px solid rgb(221, 221, 221);
				border-radius: 5px;
				padding: 10px;
				background-color: rgb(249, 249, 249);
				margin-bottom: 10px;
			}
			.pdf-embed {
				width: 100%;
				height: 400px;
				border-width: medium;
				border-style: none;
				border-color: currentcolor;
				border-image: none;
			}
			.perdeuSenha {
				color: rgb(170, 170, 170);
				opacity: 0.8;
				text-decoration: none;
				font-weight: 700;
				font-size: 10px;
				margin-top: 15px;
				display: block;
				transition: 0.2s;
			}
			.perdeuSenha:hover {
				opacity: 1;
				color: rgb(255, 255, 255);
			} /* ============
CLASSES - R ============ */
			.role {
				color: rgb(255, 255, 255);
				text-decoration: none;
				padding: 2px 5px;
				border-radius: 3px;
				transition: background-color 0.2s;
				box-shadow: rgba(0, 0, 0, 0.1) 0px 1px 2px;
			} /* ============ CLASSES - S
============ */
			.spoiler {
				background-color: rgb(47, 49, 54);
				color: transparent;
				cursor: pointer;
			}
			.spoiler:hover {
				color: white;
			}
			.staff-tag {
				background-color: rgb(255, 69, 0);
				color: rgb(255, 255, 255);
				padding: 2px 5px;
				border-radius: 3px;
				font-size: 0.8em;
				display: inline;
			}
			.sticker {
				max-width: 150px;
				height: auto;
				display: block;
			} /* ============ CLASSES - T ============
*/
			.timestamp {
				margin-right: 5px;
				margin-left: 5px;
				font-size: 0.8em;
				color: rgb(114, 118, 125);
			}
			.tnds {
				font-family: monospace;
				text-align: center;
				margin-top: 20px;
				font-size: 15px;
				color: rgb(8, 246, 255);
				text-shadow: rgb(115, 115, 115) 0px 0px 10px;
			}
			.tooltip {
				position: relative;
				display: inline-block;
			}
			.tooltip .tooltiptext {
				visibility: hidden;
				width: 200px;
				background-color: rgb(24, 25, 28);
				color: rgb(255, 255, 255);
				text-align: center;
				border-radius: 4px;
				padding: 8px;
				position: absolute;
				z-index: 1;
				bottom: 125%;
				left: 50%;
				margin-left: -100px;
				opacity: 0;
				transition: opacity 0.3s;
				font-size: 14px;
				border: 1px solid rgb(79, 84, 92);
			}
			.tooltip:hover .tooltiptext {
				visibility: visible;
				opacity: 1;
			} /* ============ CLASSES - V
============ */
			.video-embed {
				width: 100%;
				height: 400px;
				border-width: medium;
				border-style: none;
				border-color: currentcolor;
				border-image: none;
				margin-bottom: 10px;
			}
			.video-link {
				display: inline-block;
				margin: 10px 0px;
				padding: 10px;
				background-color: rgb(111, 228, 255);
				border-radius: 5px;
				text-decoration: none;
				color: rgb(51, 51, 51);
				font-weight: bold;
			}
			.video-link:hover {
				background-color: rgb(224, 224, 224);
			} /* ============
MEDIA QUERIES ============ */
			@media (max-width: 768px) {
				.logoContainer {
					display: none;
				}
				#inviteContainer {
					width: auto;
					height: auto;
				}
				#faq {
					width: 90%;
				}
				.desbloquear {
					width: 80%;
				}
				.formContainer input[type="password"],
				.formContainer input[type="text"] {
					width: 90%;
				}
				.logoContainer img {
					max-width: 90px;
				}
			}
			@media only screen and (max-width: 700px) {
				.bot-tag,
				.bot-icon,
				.autor-tag,
				.autor-icon,
				.staff-tag,
				.staff-icon,
				.author-timestamp,
				.timestamp,
				.message-self,
				.message-staff,
				.message-bot {
					font-size: 0.7em !important;
					padding: 1px 3px !important;
				}
				.modal-content {
					width: 100%;
				}
				.message-container {
					flex-direction: column;
					align-items: flex-start;
				}
				.avatar {
					margin-bottom: 10px;
				}
				.message,
				.embed-container {
					max-width: 100%;
				}
			}
"""

    def preparar_conteudo_mensagem(mensagem: discord.Message) -> dict:
        """Extrai texto, botões e mapas de menção de uma mensagem."""
        botoes = coletar_botoes(mensagem)
        rotulos_botoes = {b["label"].strip().lower() for b in botoes}
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

        texto_opcoes_staff = ""
        if botoes and texto_bruto:
            linhas_texto = [linha for linha in texto_bruto.split("\n") if linha.strip()]
            if (
                len(linhas_texto) == 1
                and "opções exclusivas" in linhas_texto[0].lower()
            ):
                texto_opcoes_staff = linhas_texto[0].strip()
                texto_bruto = ""

        mapa_usuarios = mapa_usuarios_mencionados(mensagem, texto_extra=texto_bruto)
        mapa_cargos = mapa_cargos_mencionados(mensagem, texto_extra=texto_bruto)
        return {
            "mensagem": mensagem,
            "botoes": botoes,
            "texto_bruto": texto_bruto,
            "texto_opcoes_staff": texto_opcoes_staff,
            "mapa_usuarios": mapa_usuarios,
            "mapa_cargos": mapa_cargos,
        }

    def renderizar_bloco_conteudo(dados: dict, autor_eh_bot: bool) -> list[str]:
        """
        Renderiza o miolo de uma mensagem (v2 ou content-message + anexos).
        No modelo, cada mensagem do bot vira um .message-container-v2.
        """
        saida: list[str] = []
        mensagem = dados["mensagem"]
        botoes = dados["botoes"]
        texto_bruto = dados["texto_bruto"]
        texto_opcoes_staff = dados["texto_opcoes_staff"]
        mapa_usuarios = dados["mapa_usuarios"]
        mapa_cargos = dados["mapa_cargos"]

        usar_bloco_v2 = bool(botoes) or (bool(texto_bruto) and autor_eh_bot)

        if usar_bloco_v2:
            saida.append("<div class='message-container-v2'>")
            if texto_opcoes_staff:
                saida.append(
                    "<div class='container-text-block'>"
                    f"<span class='small-text'>{esc(texto_opcoes_staff)}</span>"
                    "</div>"
                    "<hr class='container-divider' "
                    "style='margin: 2rem 0;'>"
                )
            elif texto_bruto:
                saida.append(
                    "<div class='container-text-block'>"
                    f"{markdown_simples_para_html(texto_bruto, mapa_usuarios, mapa_cargos)}"
                    "</div>"
                )
                if botoes:
                    saida.append("<hr class='container-divider'>")

            if botoes:
                saida.append("<div class='container-row'>")
                for indice, botao in enumerate(botoes):
                    if indice > 0 and indice % 4 == 0:
                        saida.append("</div><div class='container-row'>")
                    classes_btn = f"container-button button-{botao['style']}"
                    if botao["disabled"]:
                        classes_btn += " disabled"
                    saida.append(
                        f"<div class='{classes_btn}'>"
                        f"{botao.get('emoji_html') or ''}"
                        f"{esc(botao['label'])}"
                        f"</div>"
                    )
                saida.append("</div>")
            saida.append("</div>")
        elif texto_bruto:
            saida.append(
                f"<div class='content-message'>"
                f"{markdown_simples_para_html(texto_bruto, mapa_usuarios, mapa_cargos)}"
                f"</div>"
            )

        for embed in mensagem.embeds[:6]:
            titulo = esc(embed.title or "")
            desc_raw = embed.description or ""
            desc = (
                markdown_simples_para_html(desc_raw, mapa_usuarios, mapa_cargos)
                if desc_raw
                else ""
            )
            if not titulo and not desc:
                continue
            saida.append("<div class='embed'>")
            if titulo:
                saida.append(f"<div class='embed-title'>{titulo}</div>")
            if desc:
                saida.append(f"<div class='embed-description'>{desc}</div>")
            saida.append("</div>")

        if mensagem.attachments:
            saida.append("<div class='attachments'>")
            for anexo in mensagem.attachments:
                nome_arq = esc(anexo.filename)
                url_anexo = esc(anexo.url)
                tipo = (anexo.content_type or "").lower()
                eh_imagem = tipo.startswith("image/") or nome_arq.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp")
                )
                if eh_imagem:
                    saida.append(
                        f"<a href='{url_anexo}' target='_blank' rel='noopener'>"
                        f"<img src='{url_anexo}' alt='{nome_arq}' loading='lazy'></a>"
                    )
                else:
                    saida.append(
                        f"<a class='file-link' href='{url_anexo}' "
                        f"target='_blank' rel='noopener'>📎 {nome_arq}</a>"
                    )
            saida.append("</div>")

        if (
            not texto_bruto
            and not texto_opcoes_staff
            and not mensagem.attachments
            and not any(e.title or e.description for e in mensagem.embeds)
            and not botoes
        ):
            saida.append(
                "<div class='content-message'>"
                "<em>(mensagem sem texto legível)</em></div>"
            )
        return saida

    # Como no HTML modelo: mensagens do bot no início (antes de qualquer
    # humano) ficam no mesmo card, com vários message-container-v2.
    grupos: list[list[discord.Message]] = []
    indice_msg = 0
    ja_viu_humano = False
    while indice_msg < len(mensagens):
        mensagem_atual = mensagens[indice_msg]
        autor_atual = mensagem_atual.author
        if autor_atual.bot and not ja_viu_humano:
            grupo_bot: list[discord.Message] = [mensagem_atual]
            indice_msg += 1
            while indice_msg < len(mensagens) and mensagens[indice_msg].author.bot:
                grupo_bot.append(mensagens[indice_msg])
                indice_msg += 1
            grupos.append(grupo_bot)
            continue
        if not autor_atual.bot:
            ja_viu_humano = True
        grupos.append([mensagem_atual])
        indice_msg += 1

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

    for grupo in grupos:
        mensagem_cabeca = grupo[0]
        autor = mensagem_cabeca.author
        nome = esc(getattr(autor, "display_name", None) or autor.name)
        avatar = esc(url_avatar(autor))
        quando = formatar_horario(mensagem_cabeca.created_at)

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

        partes.append("<div class='message-container'>")
        partes.append(f"<img class='avatar' src='{avatar}' alt='' loading='lazy'>")
        partes.append("<div class='message-content'>")
        partes.append(f"<div class='{' '.join(classes_mensagem)}'>")
        partes.append("<div class='author-timestamp'>")
        partes.append(f"<span class='author'>{nome}</span>")
        partes.append(f"<span class='timestamp'>{quando}</span>")
        partes.extend(tags_html)
        partes.append("</div>")

        for mensagem_do_grupo in grupo:
            dados = preparar_conteudo_mensagem(mensagem_do_grupo)
            partes.extend(
                renderizar_bloco_conteudo(dados, autor_eh_bot=bool(autor.bot))
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
