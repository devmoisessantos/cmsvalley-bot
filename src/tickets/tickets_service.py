"""
Lógica de tickets: criar canal, assumir, finalizar e preparar transcript.
"""

from __future__ import annotations

import secrets
from pathlib import Path

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


def _eh_no_separador(objeto) -> bool:
    """True se o nó é um Separator (Components V2, type 14)."""
    if isinstance(objeto, dict):
        return objeto.get("type") == 14
    tipo = getattr(objeto, "type", None)
    return tipo is not None and getattr(tipo, "value", tipo) == 14


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

    if _eh_no_separador(objeto):
        return ["\x00HR\x00"]

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


CAMINHO_TEMPLATE_TRANSCRIPT = (
    Path(__file__).resolve().parent / "templates" / "transcript_base.html"
)


def carregar_template_transcript() -> str:
    """
    Lê o HTML/CSS editável do transcript.

    Edite src/tickets/templates/transcript_base.html para mudar visual,
    classes e estrutura da página. Placeholders:
    {{TITULO_PAGINA}} {{NOME_GUILDA}} {{ICONE_GUILDA}}
    {{INFO_CANAL}} {{MENSAGENS}}
    """
    return CAMINHO_TEMPLATE_TRANSCRIPT.read_text(encoding="utf-8")


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
        seguro = seguro.replace("\x00HR\x00", "<hr class='container-divider'>")

        # Links http(s) — ANTES de montar <img> de emoji/GIF
        # (se rodar depois, o regex engole o src e quebra o GIF)
        seguro = modulo_re.sub(
            r"(?<![\"'=])(https?://[^\s<>\"']+)",
            lambda m: (
                f'<a href="{m.group(1)}" target="_blank" '
                f'rel="noopener" class="msg-link">{m.group(1)}</a>'
            ),
            seguro,
        )

        # Emoji custom Discord → <img>
        def html_emoji(match) -> str:
            animado = match.group(1) == "a"
            nome = match.group(2)
            emoji_id = match.group(3)
            extensao = "gif" if animado else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extensao}"
            return (
                f'<img class="emoji" src="{esc(url)}" '
                f'alt=":{esc(nome)}:" loading="lazy">'
            )

        seguro = modulo_re.sub(
            r"\{\{E:(a|s):([A-Za-z0-9_]+):(\d+)\}\}",
            html_emoji,
            seguro,
        )

        # Canais <#id>
        def html_canal(match) -> str:
            id_canal = match.group(1)
            canal_obj = guilda.get_channel(int(id_canal)) if guilda else None
            nome_canal = f"#{canal_obj.name}" if canal_obj else "canal-desconhecido"
            return f"<span class='channel'>{esc(nome_canal)}</span>"

        seguro = modulo_re.sub(r"&lt;#(\d+)&gt;", html_canal, seguro)

        # Slash command mentions </comando:id>
        seguro = modulo_re.sub(
            r"&lt;/([a-zA-Z0-9_ -]+):(\d+)&gt;",
            lambda m: f"<span class='channel'>/{esc(m.group(1))}</span>",
            seguro,
        )

        # Timestamps <t:UNIX:formato>
        def html_timestamp(match) -> str:
            unix = int(match.group(1))
            from datetime import (
                datetime,
                timezone,
            )

            dt = datetime.fromtimestamp(unix, tz=timezone.utc)
            return f"<span class='timestamp' style='display:inline'>{dt.strftime('%d/%m/%Y %H:%M')}</span>"

        seguro = modulo_re.sub(r"&lt;t:(\d+)(?::[a-zA-Z])?&gt;", html_timestamp, seguro)

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

        # Blockquote >>> (multiline) — verifica primeiro
        seguro = modulo_re.sub(
            r"(?ms)^&gt;&gt;&gt;\s?(.+)",
            r"<blockquote class='quote'>\1</blockquote>",
            seguro,
        )
        # Blockquote > (linha única)
        seguro = modulo_re.sub(
            r"(?m)^&gt;\s+(.+)$",
            r"<blockquote class='quote'>\1</blockquote>",
            seguro,
        )
        # Lista numerada
        seguro = modulo_re.sub(
            r"(?m)^\d+\.\s+(.+)$",
            r"<li class='list-item'>\1</li>",
            seguro,
        )

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
            r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", seguro
        )

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

    template_html = carregar_template_transcript()

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
            saida.append('<div class="message-container-v2">')
            if texto_opcoes_staff:
                saida.append(
                    '<div class="container-text-block">'
                    f'<span class="small-text">{esc(texto_opcoes_staff)}</span>'
                    "</div>"
                    '<hr class="container-divider" style="margin: 2rem 0;">'
                )
            elif texto_bruto:
                saida.append(
                    '<div class="container-text-block">'
                    f"{markdown_simples_para_html(texto_bruto, mapa_usuarios, mapa_cargos)}"
                    "</div>"
                )

            if botoes:
                saida.append('<div class="container-row">')
                for indice, botao in enumerate(botoes):
                    if indice > 0 and indice % 4 == 0:
                        saida.append('</div><div class="container-row">')
                    classes_btn = f"container-button button-{botao['style']}"
                    if botao["disabled"]:
                        classes_btn += " disabled"
                    saida.append(
                        f'<div class="{classes_btn}">'
                        f"{botao.get('emoji_html') or ''}"
                        f"{esc(botao['label'])}"
                        f"</div>"
                    )
                saida.append("</div>")
            saida.append("</div>")
        elif texto_bruto:
            saida.append(
                f'<div class="content-message">'
                f"{markdown_simples_para_html(texto_bruto, mapa_usuarios, mapa_cargos)}"
                f"</div>"
            )

        for embed in mensagem.embeds[:6]:
            titulo_do_embed = esc(getattr(embed, "title", None) or "")
            descricao_bruta = getattr(embed, "description", None) or ""
            if descricao_bruta:
                descricao_do_embed = markdown_simples_para_html(
                    descricao_bruta, mapa_usuarios, mapa_cargos
                )
            else:
                descricao_do_embed = ""

            autor_do_embed = ""
            if embed.author and embed.author.name:
                autor_do_embed = esc(embed.author.name)

            tem_campos = bool(getattr(embed, "fields", None))
            tem_rodape = bool(embed.footer and getattr(embed.footer, "text", None))
            url_imagem = ""
            if embed.image and embed.image.url:
                url_imagem = esc(embed.image.url)
            url_video = ""
            if embed.video and embed.video.url:
                url_video = esc(embed.video.url)

            tem_conteudo = any(
                [
                    titulo_do_embed,
                    descricao_do_embed,
                    autor_do_embed,
                    tem_campos,
                    tem_rodape,
                    url_imagem,
                    url_video,
                ]
            )
            if not tem_conteudo:
                continue

            saida.append('<div class="embed">')
            if autor_do_embed:
                saida.append(f'<div class="embed-author">{autor_do_embed}</div>')
            if titulo_do_embed:
                saida.append(f'<div class="embed-title">{titulo_do_embed}</div>')
            if descricao_do_embed:
                saida.append(
                    f'<div class="embed-description">{descricao_do_embed}</div>'
                )
            for campo in (embed.fields or [])[:25]:
                nome_campo = esc(campo.name or "")
                valor_campo = markdown_simples_para_html(
                    campo.value or "", mapa_usuarios, mapa_cargos
                )
                saida.append(
                    '<div class="embed-field">'
                    f'<div class="embed-field-name">{nome_campo}</div>'
                    f'<div class="embed-field-value">{valor_campo}</div>'
                    "</div>"
                )
            if tem_rodape:
                saida.append(
                    f'<div class="embed-footer">{esc(embed.footer.text)}</div>'
                )
            if url_imagem:
                saida.append(
                    f'<img class="embed-image" src="{url_imagem}" loading="lazy">'
                )
            if url_video:
                saida.append(
                    f'<video class="video-embed" controls preload="metadata" '
                    f'src="{url_video}"></video>'
                )
            saida.append("</div>")

        if getattr(mensagem, "stickers", None):
            saida.append('<div class="attachments">')
            for sticker in mensagem.stickers:
                url_sticker = esc(str(sticker.url))
                saida.append(
                    f'<img class="sticker" src="{url_sticker}" '
                    f'alt="{esc(sticker.name)}" loading="lazy">'
                )
            saida.append("</div>")

        if mensagem.attachments:
            saida.append('<div class="attachments">')
            for anexo in mensagem.attachments:
                nome_arq = esc(anexo.filename)
                url_anexo = esc(anexo.url)
                tipo = (anexo.content_type or "").lower()
                eh_imagem = tipo.startswith("image/") or nome_arq.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp")
                )
                eh_video = tipo.startswith("video/") or nome_arq.lower().endswith(
                    (".mp4", ".webm", ".mov")
                )
                eh_audio = tipo.startswith("audio/") or nome_arq.lower().endswith(
                    (".mp3", ".ogg", ".wav")
                )
                if eh_imagem:
                    saida.append(
                        f'<a href="{url_anexo}" target="_blank" rel="noopener">'
                        f'<img src="{url_anexo}" alt="{nome_arq}" loading="lazy"></a>'
                    )
                elif eh_video:
                    saida.append(
                        f'<video class="video-embed" controls preload="metadata" '
                        f'src="{url_anexo}"></video>'
                    )
                elif eh_audio:
                    saida.append(f'<audio controls src="{url_anexo}"></audio>')
                else:
                    saida.append(
                        f'<a class="file-link" href="{url_anexo}" '
                        f'target="_blank" rel="noopener">📎 {nome_arq}</a>'
                    )
            saida.append("</div>")

        if (
            not texto_bruto
            and not texto_opcoes_staff
            and not mensagem.attachments
            and not any(e.title or e.description for e in mensagem.embeds)
            and not botoes
            and not getattr(mensagem, "stickers", None)
        ):
            saida.append(
                '<div class="content-message">'
                "<em>(mensagem sem texto legível)</em></div>"
            )
        return saida

    # Mensagens do bot no início (antes do 1º humano) no mesmo card,
    # como no HTML modelo: vários message-container-v2 juntos.
    grupos: list[list[discord.Message]] = []
    indice_msg = 0
    mensagens_bots = 0  # Conta quantos bots já foram processados

    while indice_msg < len(mensagens):
        mensagem_atual = mensagens[indice_msg]
        autor_atual = mensagem_atual.author

        # Agrupa APENAS se for bot E ainda não tiver 3 bots no grupo
        if autor_atual.bot and mensagens_bots < 3:
            grupo_bot = [mensagem_atual]
            indice_msg += 1
            mensagens_bots += 1

            # Pega bots enquanto ainda não tiver 3 no total
            while (
                indice_msg < len(mensagens)
                and mensagens[indice_msg].author.bot
                and mensagens_bots < 3
            ):
                grupo_bot.append(mensagens[indice_msg])
                indice_msg += 1
                mensagens_bots += 1

            grupos.append(grupo_bot)
            continue

        # Qualquer mensagem após os 3 bots fica isolada
        grupos.append([mensagem_atual])
        indice_msg += 1

    partes_mensagens: list[str] = []
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
            tags_html.append('<span class="bot-tag">BOT</span>')
        if autor.id == ticket.autor_discord_id:
            classes_mensagem.append("autor")
            tags_html.append("<span class='autor-tag'>Autor</span>")
        eh_staff = autor.id in ids_staff
        if not eh_staff and isinstance(autor, discord.Member):
            eh_staff = membro_eh_staff_ticket(autor)
        if eh_staff:
            classes_mensagem.append("staff")
            tags_html.append('<span class="staff-tag">STAFF</span>')
        if not autor.bot and autor.id != ticket.autor_discord_id and not eh_staff:
            classes_mensagem.append("membro")
            tags_html.append('<span class="staff-tag">Membro</span>')

        partes_mensagens.append('<div class="message-container">')
        partes_mensagens.append(
            f'<img class="avatar" src="{avatar}" alt="" loading="lazy">'
        )
        partes_mensagens.append('<div class="message-content">')
        partes_mensagens.append(f'<div class="{" ".join(classes_mensagem)}">')
        partes_mensagens.append('<div class="author-timestamp">')
        partes_mensagens.append(f'<span class="author">{nome}</span>')
        partes_mensagens.append(f'<span class="timestamp">{quando}</span>')
        partes_mensagens.extend(tags_html)
        partes_mensagens.append("</div>")

        for mensagem_do_grupo in grupo:
            dados = preparar_conteudo_mensagem(mensagem_do_grupo)
            partes_mensagens.extend(
                renderizar_bloco_conteudo(dados, autor_eh_bot=bool(autor.bot))
            )

        partes_mensagens.append("</div>")  # .message
        partes_mensagens.append("</div>")  # .message-content
        partes_mensagens.append("</div>")  # .message-container

    html_mensagens = "\n".join(partes_mensagens)

    titulo_pagina = f"Transcript #{ticket.id} — {ticket.categoria_rotulo or 'Ticket'}"
    info_canal = f"Transcript · {ticket.categoria_rotulo or 'Ticket'} · #{ticket.id}"

    html_final = template_html
    html_final = html_final.replace("{{TITULO_PAGINA}}", esc(titulo_pagina))
    html_final = html_final.replace("{{NOME_GUILDA}}", nome_guilda)
    html_final = html_final.replace("{{ICONE_GUILDA}}", icone)
    html_final = html_final.replace("{{INFO_CANAL}}", esc(info_canal))
    html_final = html_final.replace("{{MENSAGENS}}", html_mensagens)
    return html_final
