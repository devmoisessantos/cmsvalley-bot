"""
Lógica de tickets: criar canal, assumir, finalizar e preparar transcript.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    CARGOS,
    CARGOS_TICKET_REVOGAR_EXO,
    CARGOS_TICKET_STAFF,
    TICKETS_CATEGORIAS,
)
from src.database.conexao import async_session
from src.database.models import (
    Ticket,
    agora,
)
from src.utils.error_handling import ignorar_falha_cosmetica

registrador = logging.getLogger(__name__)

# Uma trava por autor: evita clique duplo criar dois canais ao mesmo tempo.
_travas_abertura_por_autor: dict[int, asyncio.Lock] = {}
_trava_mapa_travas = asyncio.Lock()


async def _obter_trava_abertura(autor_discord_id: int) -> asyncio.Lock:
    """Devolve (e cria se precisar) a trava de abertura deste membro."""
    async with _trava_mapa_travas:
        trava = _travas_abertura_por_autor.get(autor_discord_id)
        if trava is None:
            trava = asyncio.Lock()
            _travas_abertura_por_autor[autor_discord_id] = trava
        return trava


def _nomes_cargos_staff_da_categoria(categoria_chave: str | None) -> list[str]:
    """
    Quais cargos entram no canal e nas ações de staff desta categoria.

    Revogar Exoneração é restrito à equipe de diretoria geral.
    As demais categorias usam a lista completa de staff de ticket.
    """
    if categoria_chave == "revogar_exo":
        return list(CARGOS_TICKET_REVOGAR_EXO)
    return list(CARGOS_TICKET_STAFF)


def _cargos_staff_na_guilda(
    guilda: discord.Guild,
    categoria_chave: str | None = None,
) -> list[discord.Role]:
    """
    Resolve os cargos de staff do ticket que existem na guilda.

    Usa Role real (não discord.Object): overwrite com Object sem
    tipo de alvo pode ser ignorada pela API e o canal fica só com
    autor, bot e quem tem Administrator.
    """
    cargos: list[discord.Role] = []
    ids_ja_incluidos: set[int] = set()
    for nome_cargo in _nomes_cargos_staff_da_categoria(categoria_chave):
        cargo_id = CARGOS.get(nome_cargo)
        if not cargo_id:
            registrador.warning(
                "Cargo de ticket '%s' não está no CARGOS do config.",
                nome_cargo,
            )
            continue
        id_numerico = int(cargo_id)
        if id_numerico in ids_ja_incluidos:
            continue
        ids_ja_incluidos.add(id_numerico)

        cargo = guilda.get_role(id_numerico)
        if cargo is None:
            registrador.warning(
                "Cargo de ticket '%s' (id=%s) não existe nesta guilda.",
                nome_cargo,
                id_numerico,
            )
            continue
        cargos.append(cargo)

    return cargos


def _overwrite_staff_texto() -> discord.PermissionOverwrite:
    """Permissão padrão de staff em canal de texto do ticket."""
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        attach_files=True,
        embed_links=True,
        read_message_history=True,
    )


def _overwrite_staff_voz() -> discord.PermissionOverwrite:
    """Permissão padrão de staff em call de atendimento."""
    return discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=True,
    )


def _membro_tem_algum_cargo_por_chave(
    membro: discord.Member,
    chaves_dos_cargos: list[str] | set[str] | tuple[str, ...],
) -> bool:
    """
    True se o membro tem algum cargo da lista, batendo por ID do config
    ou pelo nome do cargo no Discord.
    """
    ids_permitidos: set[int] = set()
    nomes_permitidos: set[str] = set()
    for chave in chaves_dos_cargos:
        nomes_permitidos.add(chave)
        cargo_id = CARGOS.get(chave)
        if cargo_id:
            ids_permitidos.add(int(cargo_id))

    for cargo_do_membro in membro.roles:
        if cargo_do_membro.id in ids_permitidos:
            return True
        if cargo_do_membro.name in nomes_permitidos:
            return True
    return False


def _eh_no_separador(objeto) -> bool:
    """True se o nó é um Separator (Components V2, type 14)."""
    if isinstance(objeto, dict):
        return objeto.get("type") == 14
    tipo = getattr(objeto, "type", None)
    return tipo is not None and getattr(tipo, "value", tipo) == 14


def _url_de_media_thumbnail(no) -> str | None:
    """Extrai URL de um Thumbnail Components V2 (type 11) ou objeto media."""
    if no is None:
        return None

    if isinstance(no, dict):
        # type 11 = Thumbnail; media pode vir no próprio nó ou em "media"
        media = no.get("media") if isinstance(no.get("media"), dict) else no
        for chave in ("url", "proxy_url", "proxyUrl"):
            valor = media.get(chave) if isinstance(media, dict) else None
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
        # accessory direto com url
        for chave in ("url", "proxy_url", "proxyUrl"):
            valor = no.get(chave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
        return None

    media = getattr(no, "media", None)
    if media is not None:
        for atributo in ("url", "proxy_url", "proxyUrl"):
            valor = getattr(media, atributo, None)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
            if isinstance(media, dict) and isinstance(media.get(atributo), str):
                return media[atributo].strip()

    for atributo in ("url", "proxy_url", "proxyUrl"):
        valor = getattr(no, atributo, None)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return None


def _extrair_urls_thumbnail_componente(
    mensagem: discord.Message,
) -> list[str]:
    """
    URLs das miniaturas (Thumbnail accessory) em Sections Components V2.

    No Discord, Section (type 9) pode ter accessory=Thumbnail (type 11) —
    é o avatar do autor no card "Ticket Criado com Sucesso".
    """
    urls: list[str] = []
    vistos: set[str] = set()

    def registrar(url: str | None) -> None:
        if not url or url in vistos:
            return
        vistos.add(url)
        urls.append(url)

    def percorrer(no, profundidade: int = 0) -> None:
        if no is None or profundidade > 14:
            return

        if isinstance(no, dict):
            tipo = no.get("type")
            # Thumbnail direto
            if tipo == 11:
                registrar(_url_de_media_thumbnail(no))
            # Section / qualquer nó com accessory
            accessory = no.get("accessory")
            if accessory is not None:
                registrar(_url_de_media_thumbnail(accessory))
                if isinstance(accessory, dict) and accessory.get("type") == 11:
                    registrar(_url_de_media_thumbnail(accessory))
            for chave in ("components", "children", "items"):
                filhos = no.get(chave)
                if isinstance(filhos, list):
                    for filho in filhos:
                        percorrer(filho, profundidade + 1)
                elif isinstance(filhos, dict):
                    percorrer(filhos, profundidade + 1)
            return

        # Objetos tipados discord.py
        tipo = getattr(no, "type", None)
        tipo_val = getattr(tipo, "value", tipo)
        nome = type(no).__name__.lower()
        if tipo_val == 11 or "thumbnail" in nome:
            registrar(_url_de_media_thumbnail(no))

        accessory = getattr(no, "accessory", None)
        if accessory is not None:
            registrar(_url_de_media_thumbnail(accessory))

        for atributo in ("children", "components", "items"):
            filhos = getattr(no, atributo, None)
            if not filhos:
                continue
            try:
                lista = list(filhos)
            except TypeError:
                continue
            for filho in lista:
                percorrer(filho, profundidade + 1)

        dados = getattr(no, "_data", None)
        if isinstance(dados, dict):
            percorrer(dados, profundidade + 1)

    dados = getattr(mensagem, "_data", None)
    if isinstance(dados, dict):
        for comp in dados.get("components") or []:
            percorrer(comp)

    for comp in getattr(mensagem, "components", None) or []:
        percorrer(comp)

    return urls


def membro_eh_staff_ticket(
    membro: discord.Member,
    categoria_chave: str | None = None,
) -> bool:
    """
    True se o membro pode atuar como staff neste ticket.

    Com categoria_chave=None usa a lista geral. Em revogar_exo
    só passa quem está em CARGOS_TICKET_REVOGAR_EXO (ou admin,
    tratado em quem chama quando necessário).
    """
    nomes = _nomes_cargos_staff_da_categoria(categoria_chave)
    return _membro_tem_algum_cargo_por_chave(membro, nomes)


def membro_eh_equipe_ticket(membro: discord.Member) -> bool:
    """
    True se o membro tem o cargo ⚠️ EQUIPE • TICKET.

    Usado na transferência de atendimento: só quem tem esse cargo
    pode receber o ticket.
    """
    id_equipe = CARGOS.get("⚠️ EQUIPE • TICKET")
    if not id_equipe:
        return False
    for cargo_do_membro in membro.roles:
        if cargo_do_membro.id == int(id_equipe):
            return True
    return False


def membro_pode_gerenciar_ticket(
    membro: discord.Member,
    ticket: Ticket | None = None,
) -> bool:
    """
    Diz se o membro pode usar os comandos de gerenciamento do ticket.

    Libera para:
    - administrador do Discord ou cargo de administração
    - staff da categoria do ticket (quando o ticket é informado)
    - Responsavel HP / Responsável Geral
    - quem assumiu o ticket (quando o ticket é informado)
    """
    from src.utils.permissions import membro_e_administrador

    if membro_e_administrador(membro):
        return True

    categoria_chave = None
    if ticket is not None:
        categoria_chave = ticket.categoria_chave

    if membro_eh_staff_ticket(membro, categoria_chave):
        return True

    nomes_liberados = {
        "Responsavel HP",
        "👑 | RESPONSÁVEL GERAL",
    }
    if _membro_tem_algum_cargo_por_chave(membro, nomes_liberados):
        return True

    if ticket is not None and ticket.staff_assumiu_id is not None:
        if int(ticket.staff_assumiu_id) == int(membro.id):
            return True

    return False


async def listar_tickets_do_autor(
    autor_discord_id: int,
    limite: int = 25,
) -> list[Ticket]:
    """
    Lista os tickets do autor, do mais recente para o mais antigo.

    Usado pelo comando ver-transcript para o staff escolher qual
    atendimento consultar.
    """
    async with async_session() as sessao:
        consulta = (
            select(Ticket)
            .where(Ticket.autor_discord_id == autor_discord_id)
            .order_by(Ticket.aberto_em.desc())
            .limit(limite)
        )
        resultado = await sessao.execute(consulta)
        return list(resultado.scalars().all())


async def listar_tickets_abertos(limite: int = 100) -> list[Ticket]:
    """Lista tickets ainda abertos ou assumidos (canal ainda ativo)."""
    async with async_session() as sessao:
        consulta = (
            select(Ticket)
            .where(Ticket.status.in_(["aberto", "assumido"]))
            .order_by(Ticket.aberto_em.desc())
            .limit(limite)
        )
        resultado = await sessao.execute(consulta)
        return list(resultado.scalars().all())


def gerar_senha_transcript() -> str:
    """
    Gera senha para visualização do transcript.

    token_urlsafe(8) ≈ 11 caracteres URL-safe (~66 bits de entropia),
    bem mais resistente a brute-force do que o antigo token_hex(3).
    """
    return secrets.token_urlsafe(8)


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
    """Obtém o ticket ligado ao canal para evitar tratar canais comuns como suporte."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Ticket).where(Ticket.canal_id == canal_id)
        )
        return resultado.scalar_one_or_none()


async def buscar_ticket_aberto_do_autor(
    autor_discord_id: int,
    categoria_chave: str | None = None,
) -> Ticket | None:
    """
    Retorna ticket ainda aberto/assumido do autor.

    Por padrão (categoria_chave=None) busca em QUALQUER categoria —
    só pode existir 1 ticket aberto por membro no servidor.

    Usa limit(1) ordenado pelo mais recente: se já houver duplicata no
    banco, não estoura MultipleResultsFound e ainda bloqueia nova abertura.
    """
    async with async_session() as sessao:
        consulta = (
            select(Ticket)
            .where(
                Ticket.autor_discord_id == autor_discord_id,
                Ticket.status.in_(["aberto", "assumido"]),
            )
            .order_by(Ticket.aberto_em.desc())
            .limit(1)
        )
        if categoria_chave:
            consulta = consulta.where(Ticket.categoria_chave == categoria_chave)
        resultado = await sessao.execute(consulta)
        return resultado.scalars().first()


def membro_tem_cargo_obrigatorio_ticket(
    membro: discord.Member,
    definicao: dict,
) -> bool:
    """
    Verifica se o membro tem ao menos um dos cargos exigidos pela categoria.

    Lista vazia / ausente = liberado para qualquer um.
    """
    from src.config import CARGOS_PUNICOES

    nomes_obrigatorios = definicao.get("cargos_obrigatorios") or []
    if not nomes_obrigatorios:
        return True

    ids_permitidos: set[int] = set()
    for nome_cargo in nomes_obrigatorios:
        cargo_id = CARGOS_PUNICOES.get(nome_cargo)
        if cargo_id:
            ids_permitidos.add(int(cargo_id))
        for cargo_membro in membro.roles:
            if cargo_membro.name == nome_cargo:
                ids_permitidos.add(cargo_membro.id)

    if not ids_permitidos:
        return True

    ids_do_membro = {cargo.id for cargo in membro.roles}
    return bool(ids_do_membro & ids_permitidos)


async def criar_ticket(
    guilda: discord.Guild,
    autor: discord.Member,
    categoria_chave: str,
) -> tuple[Ticket, discord.TextChannel] | None:
    """
    Cria o canal privado do ticket e registra no banco.

    Retorna (ticket, canal) ou None se a categoria for inválida.

    Usa trava por autor + rechecagem imediata antes de criar o canal,
    para clique duplo não gerar dois tickets abertos.
    """
    definicao = TICKETS_CATEGORIAS.get(categoria_chave)
    if definicao is None:
        return None

    if not membro_tem_cargo_obrigatorio_ticket(autor, definicao):
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

    trava = await _obter_trava_abertura(autor.id)
    async with trava:
        # Segunda barreira: outro clique pode ter passado no painel
        # enquanto o primeiro ainda criava o canal.
        ja_aberto = await buscar_ticket_aberto_do_autor(
            autor_discord_id=autor.id,
            categoria_chave=None,
        )
        if ja_aberto is not None:
            registrador.info(
                "Abertura bloqueada: autor %s já tem ticket #%s (%s)",
                autor.id,
                ja_aberto.id,
                ja_aberto.categoria_chave,
            )
            return None

        username = nome_usuario_discord(autor)
        nome_canal = sanitizar_nome_canal(f"{definicao['prefixo_canal']}-{username}")
        cargos_staff = _cargos_staff_na_guilda(guilda, categoria_chave)
        overwrite_staff = _overwrite_staff_texto()

        overwrites: dict[
            discord.Role | discord.Member,
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

        for cargo_staff in cargos_staff:
            overwrites[cargo_staff] = overwrite_staff

        registrador.info(
            "Criando ticket categoria=%s com %s cargos de staff: %s",
            categoria_chave,
            len(cargos_staff),
            ", ".join(f"{cargo.name}({cargo.id})" for cargo in cargos_staff)
            or "(nenhum)",
        )

        # overwrites já vão no create — evita N set_permissions (rate limit).
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


async def sincronizar_permissoes_do_canal_ticket(
    canal: discord.TextChannel,
    ticket: Ticket,
) -> dict[str, int | list[str]]:
    """
    Garante que os cargos corretos veem e falam no canal do ticket.

    Aplica tudo em um único channel.edit(overwrites=...) para evitar
    rate limit de vários set_permissions em sequência.

    Usado ao assumir e no comando manual de sincronizar.
    Retorna contagem e nomes aplicados para feedback ao staff.
    """
    guilda = canal.guild
    resultado: dict[str, int | list[str]] = {
        "cargos_ok": 0,
        "cargos_falha": 0,
        "nomes_ok": [],
        "nomes_falha": [],
    }
    if guilda is None:
        return resultado

    overwrite_staff = _overwrite_staff_texto()
    cargos_staff = _cargos_staff_na_guilda(guilda, ticket.categoria_chave)

    overwrites: dict[
        discord.Role | discord.Member,
        discord.PermissionOverwrite,
    ] = {
        guilda.default_role: discord.PermissionOverwrite(view_channel=False),
    }

    autor = guilda.get_member(int(ticket.autor_discord_id))
    if autor is not None:
        overwrites[autor] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            read_message_history=True,
        )

    if guilda.me is not None:
        overwrites[guilda.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            attach_files=True,
            embed_links=True,
            read_message_history=True,
        )

    nomes_ok: list[str] = []
    for cargo_staff in cargos_staff:
        overwrites[cargo_staff] = overwrite_staff
        nomes_ok.append(f"{cargo_staff.name} ({cargo_staff.id})")

    try:
        await canal.edit(
            overwrites=overwrites,
            reason=(
                f"Sincronizar permissões do ticket "
                f"(categoria={ticket.categoria_chave})"
            ),
        )
        resultado["cargos_ok"] = len(nomes_ok)
        resultado["nomes_ok"] = nomes_ok
    except discord.HTTPException as erro:
        resultado["cargos_falha"] = len(nomes_ok)
        resultado["nomes_falha"] = nomes_ok
        registrador.warning(
            "Falha ao sincronizar overwrites do ticket #%s canal=%s: %s",
            ticket.id,
            canal.id,
            erro,
        )
        return resultado

    registrador.info(
        "Permissões sincronizadas no ticket #%s canal=%s ok=%s falha=%s",
        ticket.id,
        canal.id,
        len(nomes_ok),
        0,
    )
    return resultado


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
        registrador.warning(f"⚠️ Falha ao renomear canal do ticket: {erro}")

    try:
        await sincronizar_permissoes_do_canal_ticket(canal, ticket)
    except Exception as erro_sync:
        registrador.warning(
            "Falha ao sincronizar permissões ao assumir ticket #%s: %s",
            ticket.id,
            erro_sync,
        )

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
    """Obtém um ticket pela chave primária sem lançar erro quando não existe."""
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
        except discord.HTTPException as erro_em_apagar_call_do_ticket:
            # Enfeite que falhou: apagar a call de voz do ticket.
            # A acao principal ja tinha dado certo, entao so registro.
            ignorar_falha_cosmetica(
                erro_em_apagar_call_do_ticket,
                o_que_falhou="apagar a call de voz do ticket",
            )
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

    overwrite_voz = _overwrite_staff_voz()
    overwrites: dict[
        discord.Role | discord.Member,
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

    for cargo_staff in _cargos_staff_na_guilda(guilda, ticket.categoria_chave):
        overwrites[cargo_staff] = overwrite_voz

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


async def montar_html_transcript(
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
        """Neutraliza caracteres HTML para que mensagens não alterem o transcript."""
        return modulo_html.escape(texto or "")

    async def _persistir_bytes_midia(
        dados: bytes,
        mime: str,
        filename: str,
    ) -> str | None:
        """
        Envia bytes para a API (S3/R2). URL HTTPS permanente.
        None se não configurado ou se o upload falhar.
        """
        from src.tickets.tickets_transcript_api import enviar_asset_para_api

        try:
            return await enviar_asset_para_api(
                int(ticket.id),
                dados,
                content_type=mime,
                filename=filename,
            )
        except Exception as erro_upload:
            logging.warning(
                "Upload asset ticket #%s falhou (%s): %s",
                ticket.id,
                filename,
                erro_upload,
            )
            return None

    def _mime_por_nome_ou_url(nome_ou_url: str, mime: str) -> str:
        mime = (mime or "").split(";")[0].strip()
        if mime and mime != "application/octet-stream":
            return mime
        lower = (nome_ou_url or "").lower().split("?")[0]
        if lower.endswith(".png"):
            return "image/png"
        if lower.endswith(".jpg") or lower.endswith(".jpeg"):
            return "image/jpeg"
        if lower.endswith(".gif"):
            return "image/gif"
        if lower.endswith(".webp"):
            return "image/webp"
        if lower.endswith(".mp4"):
            return "video/mp4"
        if lower.endswith(".webm"):
            return "video/webm"
        if lower.endswith(".mp3"):
            return "audio/mpeg"
        if lower.endswith(".ogg"):
            return "audio/ogg"
        if lower.endswith(".pdf"):
            return "application/pdf"
        return mime or "application/octet-stream"

    async def midia_estavel(
        url: str, content_type: str | None = None, limite_bytes: int = 25_000_000
    ) -> str:
        """
        Baixa a mídia e grava no S3/API (URL permanente).

        Fallback: data-URI base64 (só se o upload falhar e o arquivo for
        pequeno o bastante). data: quebra o botão "Abrir" em muitos browsers —
        por isso o S3 é o caminho preferido.
        """
        import base64

        import aiohttp

        if not url:
            return ""
        if url.startswith("data:"):
            return url
        if url.startswith("http") and "discord" not in url and "/transcripts/" in url:
            # Já é URL da nossa API/CDN
            return url
        try:
            timeout = aiohttp.ClientTimeout(total=40)
            headers = {"User-Agent": "CMSValley-Transcript/1.0"}
            async with aiohttp.ClientSession(
                timeout=timeout, headers=headers
            ) as sessao:
                async with sessao.get(url) as resp:
                    if resp.status != 200:
                        return url
                    dados = await resp.read()
                    if not dados:
                        return url
                    mime = _mime_por_nome_ou_url(
                        url,
                        content_type
                        or resp.headers.get("Content-Type")
                        or "application/octet-stream",
                    )
                    nome = url.split("?")[0].rstrip("/").split("/")[-1] or "midia.bin"
                    if len(dados) <= limite_bytes:
                        url_cdn = await _persistir_bytes_midia(dados, mime, nome)
                        if url_cdn:
                            return url_cdn
                    # Fallback data-URI só para arquivos menores (lightbox/Abrir
                    # com data: é frágil; preferimos CDN).
                    if len(dados) <= 6_000_000:
                        b64 = base64.b64encode(dados).decode("ascii")
                        return f"data:{mime};base64,{b64}"
                    return url
        except Exception as erro_midia:
            logging.warning("Falha ao processar mídia %s: %s", url[:80], erro_midia)
            return url

    async def anexo_estavel(anexo: discord.Attachment) -> str:
        """Lê o anexo via Discord e grava no S3/API; fallback data-URI/HTTP."""
        import base64

        limite = 25_000_000
        try:
            dados = await anexo.read()
            if dados and len(dados) <= limite:
                mime = _mime_por_nome_ou_url(
                    anexo.filename or "",
                    anexo.content_type or "application/octet-stream",
                )
                nome = anexo.filename or "anexo.bin"
                url_cdn = await _persistir_bytes_midia(dados, mime, nome)
                if url_cdn:
                    return url_cdn
                if len(dados) <= 6_000_000:
                    return (
                        f"data:{mime};base64,"
                        f"{base64.b64encode(dados).decode('ascii')}"
                    )
        except Exception as erro_anexo:
            logging.warning(
                "Attachment.read falhou (%s): %s",
                getattr(anexo, "filename", "?"),
                erro_anexo,
            )
        return await midia_estavel(
            getattr(anexo, "url", "") or "", getattr(anexo, "content_type", None)
        )

    def url_avatar(usuario: discord.abc.User) -> str:
        """Obtém uma imagem de avatar com alternativa segura para falhas da API."""
        try:
            return str(usuario.display_avatar.replace(size=64).url)
        except Exception:
            return f"https://cdn.discordapp.com/embed/avatars/{int(usuario.id) % 6}.png"

    def url_icone_guilda() -> str:
        """Fornece o ícone da guilda ou uma imagem padrão para o cabeçalho HTML."""
        if guilda is not None and guilda.icon is not None:
            return str(guilda.icon.replace(size=128).url)
        return "https://cdn.discordapp.com/embed/avatars/0.png"

    def formatar_horario(data_e_hora) -> str:
        """Converte horários ao fuso local sem impedir o transcript se isso falhar."""
        if data_e_hora is None:
            return "—"
        local = data_e_hora
        try:
            from src.utils.formatacao import para_horario_brasilia

            convertido = para_horario_brasilia(data_e_hora)
            if convertido is not None:
                local = convertido
        except Exception as erro_ao_converter_fuso:
            # Se a conversao de fuso falha, mostro o horario como veio (UTC)
            # em vez de derrubar o transcript inteiro por causa de um horario.
            logging.warning(
                "Nao consegui converter %s para o fuso de Brasilia: %s",
                data_e_hora,
                erro_ao_converter_fuso,
            )
        return local.strftime("%d/%m/%Y %H:%M")

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
        except AttributeError as erro_sem_autor:
            # Mensagem de sistema pode nao ter autor utilizavel. O transcript
            # continua valido sem esse nome no mapa.
            logging.debug(
                "Mensagem %s sem autor utilizavel para o transcript: %s",
                getattr(mensagem, "id", "?"),
                erro_sem_autor,
            )
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
            """Inclui no mapa cargos disponíveis para resolver menções do HTML."""
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
            """Protege menções de usuário até a etapa de renderização HTML."""
            return f"{{{{U:{match.group(1)}}}}}"

        def trocar_cargo(match) -> str:
            """Protege menções de cargo até a etapa de renderização HTML."""
            return f"{{{{R:{match.group(1)}}}}}"

        def trocar_emoji(match) -> str:
            """Troca emoji personalizado por marcador com animação e identidade."""
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
        Converte markdown Discord para HTML (estrutura da tela-chat).

        Suporta:
        - # / ## / ### (títulos) e -# (subtexto)
        - **negrito** / __sublinhado__ / *itálico* / _itálico_
        - ~~riscado~~ / ||spoiler||
        - `código` e ```bloco``` (com linguagem opcional)
        - > quote e >>> quote multilinha
        - listas - e 1.
        - menções user/cargo, canais, timestamps, emoji custom
        - links http(s)
        """
        if not texto:
            return ""

        preparado = preparar_texto_com_mencoes(texto, mapa_usuarios, mapa_cargos)
        seguro = esc(preparado)
        seguro = seguro.replace("\x00HR\x00", '<hr class="container-divider">')

        # --- Extrair blocos de código ANTES de qualquer outra coisa ---
        blocos_codigo: list[str] = []

        def _empilhar_codigo(lang: str, codigo: str) -> str:
            lang = (lang or "").strip()
            codigo = codigo or ""
            if codigo.startswith("\n"):
                codigo = codigo[1:]
            if codigo.endswith("\n"):
                codigo = codigo[:-1]
            indice = len(blocos_codigo)
            lang_attr = f' data-lang="{esc(lang)}"' if lang else ""
            blocos_codigo.append(
                f'<pre class="code-block"{lang_attr}><code>{codigo}</code></pre>'
            )
            return f"@@CODE{indice}@@"

        # ```lang\n...```
        seguro = modulo_re.sub(
            r"```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```",
            lambda m: _empilhar_codigo(m.group(1), m.group(2)),
            seguro,
        )
        # ```...``` sem linguagem / fence simples
        seguro = modulo_re.sub(
            r"```([\s\S]*?)```",
            lambda m: _empilhar_codigo("", m.group(1)),
            seguro,
        )

        # Código inline (não atravessa linha)
        seguro = modulo_re.sub(
            r"`([^`\n]+)`",
            r"<code>\1</code>",
            seguro,
        )

        # Links http(s) — evita engolir src de <img> futuros
        seguro = modulo_re.sub(
            r'(?<!["\'=])(https?://[^\s<>"\']+)',
            lambda m: (
                f'<a href="{m.group(1)}" target="_blank" rel="noopener" '
                f'class="msg-link">{m.group(1)}</a>'
            ),
            seguro,
        )

        # Emoji custom {{E:a|s:nome:id}}
        def html_emoji(match) -> str:
            animado = match.group(1) == "a"
            nome = match.group(2)
            emoji_id = match.group(3)
            extensao = "gif" if animado else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extensao}"
            return (
                f'<img class="discord-emoji" data-emoji="" src="{esc(url)}" '
                f'alt=":{esc(nome)}:" loading="lazy">'
            )

        seguro = modulo_re.sub(
            r"\{\{E:(a|s):([A-Za-z0-9_]+):(\d+)\}\}",
            html_emoji,
            seguro,
        )

        # Canais <#id> já escapados
        def html_canal(match) -> str:
            id_canal = match.group(1)
            canal_obj = guilda.get_channel(int(id_canal)) if guilda else None
            nome_canal = f"#{canal_obj.name}" if canal_obj else "#canal"
            return f'<span class="mention">{esc(nome_canal)}</span>'

        seguro = modulo_re.sub(r"&lt;#(\d+)&gt;", html_canal, seguro)

        # Slash </cmd:id>
        seguro = modulo_re.sub(
            r"&lt;/([a-zA-Z0-9_ -]+):(\d+)&gt;",
            lambda m: f'<span class="mention">/{esc(m.group(1))}</span>',
            seguro,
        )

        # Timestamps <t:UNIX:f>
        def html_timestamp(match) -> str:
            unix = int(match.group(1))
            from datetime import datetime, timezone

            data_e_hora = datetime.fromtimestamp(unix, tz=timezone.utc)
            return f'<span class="timestamp">{data_e_hora.strftime("%d/%m/%Y %H:%M")}</span>'

        seguro = modulo_re.sub(r"&lt;t:(\d+)(?::[a-zA-Z])?&gt;", html_timestamp, seguro)

        # Usuários {{U:id}}
        def html_usuario(match) -> str:
            id_usuario = match.group(1)
            nome = mapa_usuarios.get(id_usuario) or "usuário"
            return f'<span class="mention">@{esc(nome)}</span>'

        seguro = modulo_re.sub(r"\{\{U:(\d+)\}\}", html_usuario, seguro)

        # Cargos {{R:id}}
        def html_cargo(match) -> str:
            id_cargo = match.group(1)
            dados_cargo = mapa_cargos.get(id_cargo)
            if dados_cargo:
                nome_cargo, cor_hex = dados_cargo
                return (
                    f'<span class="mention" style="color:{esc(cor_hex)};'
                    f'background-color:{esc(cor_hex)}22">@{esc(nome_cargo)}</span>'
                )
            return '<span class="mention">@cargo</span>'

        seguro = modulo_re.sub(r"\{\{R:(\d+)\}\}", html_cargo, seguro)

        # --- Blockquotes ---
        # >>> multilinha: do >>> até o fim do bloco (linhas seguintes)
        def guardar_quote_multi(match) -> str:
            corpo = match.group(1).strip("\n")
            # linhas internas podem ter &gt; residual no começo
            linhas = []
            for ln in corpo.split("\n"):
                ln = modulo_re.sub(r"^&gt;\s?", "", ln)
                linhas.append(ln)
            inner = "<br>".join(linhas)
            return f"<blockquote>{inner}</blockquote>"

        seguro = modulo_re.sub(
            r"(?m)^&gt;&gt;&gt;\s?([\s\S]*?)(?=\n(?!&gt;)|$)",
            guardar_quote_multi,
            seguro,
        )

        # > linha única (ou sequências de > linhas → um blockquote)
        def guardar_quote_linhas(match) -> str:
            bloco = match.group(0)
            linhas = []
            for ln in bloco.split("\n"):
                ln = modulo_re.sub(r"^&gt;\s?", "", ln)
                linhas.append(ln)
            return f"<blockquote>{'<br>'.join(linhas)}</blockquote>"

        seguro = modulo_re.sub(
            r"(?m)^(?:&gt;\s?.+\n?)+",
            guardar_quote_linhas,
            seguro,
        )

        # Títulos / subtexto
        seguro = modulo_re.sub(
            r"(?m)^-#\s+(.+)$",
            r'<p class="small muted">\1</p>',
            seguro,
        )
        seguro = modulo_re.sub(
            r"(?m)^###\s+(.+)$",
            r'<h3 class="message-heading">\1</h3>',
            seguro,
        )
        seguro = modulo_re.sub(
            r"(?m)^##\s+(.+)$",
            r'<h2 class="message-heading">\1</h2>',
            seguro,
        )
        seguro = modulo_re.sub(
            r"(?m)^#\s+(.+)$",
            r'<h1 class="message-heading">\1</h1>',
            seguro,
        )

        # Listas — mesmo padrão do tela-chat: <p class="message-list-item">
        # (o CSS ::before desenha o bullet; ul/li nativo + ::before = bullet duplo)
        def wrap_lista(match) -> str:
            itens = match.group(0).strip().split("\n")
            partes = []
            for it in itens:
                it = modulo_re.sub(r"^-\s+", "", it)
                it = modulo_re.sub(r"^\*\s+", "", it)
                it = modulo_re.sub(r"^\d+\.\s+", "", it)
                it = modulo_re.sub(r"^[•·●]\s+", "", it)
                if it.strip():
                    partes.append(f'<p class="message-list-item">{it}</p>')
            return "".join(partes)

        seguro = modulo_re.sub(
            r"(?m)^(?:\d+\.\s+.+|\-\s+.+|\*\s+.+|[•·●]\s+.+)\n?"
            r"(?:(?:\d+\.\s+.+|\-\s+.+|\*\s+.+|[•·●]\s+.+)\n?)*",
            wrap_lista,
            seguro,
        )

        # Formatação inline
        seguro = modulo_re.sub(
            r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", seguro
        )
        seguro = modulo_re.sub(
            r"\*\*__(.+?)__\*\*", r"<strong><u>\1</u></strong>", seguro
        )
        seguro = modulo_re.sub(
            r"__\*\*(.+?)\*\*__", r"<u><strong>\1</strong></u>", seguro
        )
        seguro = modulo_re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", seguro)
        seguro = modulo_re.sub(r"__(.+?)__", r"<u>\1</u>", seguro)
        seguro = modulo_re.sub(r"~~(.+?)~~", r"<s>\1</s>", seguro)
        seguro = modulo_re.sub(
            r"\|\|(.+?)\|\|",
            r'<span class="spoiler-text" aria-expanded="false">\1</span>',
            seguro,
        )
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
            seguro = seguro.replace(f"@@CODE{indice}@@", bloco)

        # Quebras de linha → <br>, sem poluir blocos de bloco
        # Protege tags de bloco antes de fatiar linhas
        placeholders_bloco: list[str] = []

        def guardar_html_bloco(match) -> str:
            placeholders_bloco.append(match.group(0))
            return f"@@BLK{len(placeholders_bloco) - 1}@@"

        seguro = modulo_re.sub(
            r"<(?:pre|blockquote|ul|h[1-3]|hr)\b[\s\S]*?</(?:pre|blockquote|ul|h[1-3])>|<hr\b[^>]*>",
            guardar_html_bloco,
            seguro,
        )

        partes_out: list[str] = []
        for linha in seguro.split("\n"):
            if linha.strip() == "":
                continue
            if linha.startswith("@@BLK"):
                partes_out.append(linha)
            else:
                partes_out.append(f"<p>{linha}</p>")
        seguro = "".join(partes_out)

        for i, bloco in enumerate(placeholders_bloco):
            seguro = seguro.replace(f"@@BLK{i}@@", bloco)

        # Limpa <br> órfãos ao redor de blocos
        seguro = modulo_re.sub(r"(?:<br>\s*){3,}", "<br><br>", seguro)

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
            """Normaliza um botão encontrado, descartando duplicatas e ações internas.

            Converte estilo e emoji para a representação HTML e impede que
            marcadores técnicos de ticket apareçam no transcript destinado à
            leitura humana.
            """
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
            """Visita estruturas serializadas para encontrar botões em cada nível."""
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
            """Visita componentes de objetos sem recursão infinita ou dados privados.

            Aceita objetos e dicionários porque o Discord pode expor a mesma
            interface em ambos os formatos. O limite de profundidade protege a
            exportação contra estruturas inesperadamente circulares ou extensas.
            """
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
        rotulos_botoes = {botao["label"].strip().lower() for botao in botoes}
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
        urls_thumbnail = _extrair_urls_thumbnail_componente(mensagem)
        return {
            "mensagem": mensagem,
            "botoes": botoes,
            "texto_bruto": texto_bruto,
            "texto_opcoes_staff": texto_opcoes_staff,
            "mapa_usuarios": mapa_usuarios,
            "mapa_cargos": mapa_cargos,
            "urls_thumbnail": urls_thumbnail,
        }

    async def renderizar_bloco_conteudo(dados: dict, autor_eh_bot: bool) -> list[str]:
        """
        Renderiza o miolo de uma mensagem (v2 ou message-text + anexos).
        No modelo, cada mensagem do bot vira um .message-container-v2.
        """
        saida: list[str] = []
        mensagem = dados["mensagem"]
        botoes = dados["botoes"]
        texto_bruto = dados["texto_bruto"]
        texto_opcoes_staff = dados["texto_opcoes_staff"]
        mapa_usuarios = dados["mapa_usuarios"]
        mapa_cargos = dados["mapa_cargos"]
        urls_thumbnail = dados.get("urls_thumbnail") or []

        # Painéis do bot / com botões → form-response + form-fields (tela-chat)
        usar_form = bool(botoes) or (bool(texto_bruto) and autor_eh_bot)

        async def html_thumbnail(url: str) -> str:
            """Miniatura lateral estilo tela-chat (Section accessory)."""
            url_estavel = await midia_estavel(url, "image/*")
            return (
                f'<img class="component-thumbnail" data-evidence="" '
                f'src="{esc(url_estavel)}" alt="Miniatura do painel" '
                f'loading="lazy">'
            )

        def quebrar_por_separador(texto: str) -> tuple[str, str]:
            """
            Separa título (antes do HR) do corpo da Section (depois).
            Espelha o layout do card de abertura: título + hr + section.
            """
            if "\x00HR\x00" not in texto:
                return "", texto
            partes = texto.split("\x00HR\x00", 1)
            return partes[0].strip(), partes[1].strip()

        if usar_form:
            saida.append('<div class="message-text"></div>')
            saida.append('<div class="form-response"><div class="form-fields">')
            if texto_opcoes_staff:
                saida.append(
                    '<div class="message-text">'
                    f'<p class="small muted">{esc(texto_opcoes_staff)}</p>'
                    "</div>"
                    '<hr class="container-divider">'
                )
            if texto_bruto:
                titulo_bruto, corpo_bruto = quebrar_por_separador(texto_bruto)
                url_thumb = urls_thumbnail[0] if urls_thumbnail else None

                if titulo_bruto:
                    html_titulo = markdown_simples_para_html(
                        titulo_bruto, mapa_usuarios, mapa_cargos
                    )
                    if html_titulo and not html_titulo.lstrip().startswith("<"):
                        html_titulo = f"<p>{html_titulo}</p>"
                    saida.append(f'<div class="message-text">{html_titulo}</div>')
                    saida.append('<hr class="container-divider">')
                    texto_para_corpo = corpo_bruto
                else:
                    texto_para_corpo = texto_bruto

                html_txt = markdown_simples_para_html(
                    texto_para_corpo, mapa_usuarios, mapa_cargos
                )
                if html_txt and not html_txt.lstrip().startswith("<"):
                    html_txt = f"<p>{html_txt}</p>"

                if url_thumb and html_txt:
                    # Layout tela-chat: texto à esquerda, avatar do autor à direita
                    saida.append('<div class="transcript-section">')
                    saida.append(f'<div><div class="message-text">{html_txt}</div></div>')
                    saida.append(await html_thumbnail(url_thumb))
                    saida.append("</div>")
                elif html_txt:
                    saida.append(f'<div class="message-text">{html_txt}</div>')
                elif url_thumb:
                    # Só thumbnail (raro) — ainda mostra a miniatura
                    saida.append('<div class="transcript-section">')
                    saida.append("<div></div>")
                    saida.append(await html_thumbnail(url_thumb))
                    saida.append("</div>")
            elif urls_thumbnail:
                saida.append('<div class="transcript-section">')
                saida.append("<div></div>")
                saida.append(await html_thumbnail(urls_thumbnail[0]))
                saida.append("</div>")
            if botoes:
                saida.append('<div class="archived-action-row">')
                for botao in botoes:
                    disabled = " disabled" if botao["disabled"] else ""
                    saida.append(
                        f'<button class="secondary-button archived-button" type="button"'
                        f'{disabled} title="Botão disponível somente no Discord">'
                        f"{botao.get('emoji_html') or ''}"
                        f"{esc(botao['label'])}"
                        f"</button>"
                    )
                saida.append("</div>")
            saida.append("</div></div>")  # form-fields + form-response
        elif texto_bruto:
            html_txt = markdown_simples_para_html(
                texto_bruto, mapa_usuarios, mapa_cargos
            )
            if html_txt and not html_txt.lstrip().startswith("<"):
                html_txt = f"<p>{html_txt}</p>"
            url_thumb = urls_thumbnail[0] if urls_thumbnail else None
            if url_thumb and html_txt:
                saida.append('<div class="transcript-section">')
                saida.append(f'<div><div class="message-text">{html_txt}</div></div>')
                saida.append(await html_thumbnail(url_thumb))
                saida.append("</div>")
            else:
                saida.append(f'<div class="message-text">{html_txt}</div>')
        else:
            saida.append('<div class="message-text"></div>')

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
            url_autor_icon = ""
            if embed.author and embed.author.name:
                autor_do_embed = esc(embed.author.name)
                icon_url = getattr(embed.author, "icon_url", None) or getattr(
                    embed.author, "icon", None
                )
                if icon_url:
                    url_autor_icon = str(icon_url)

            url_embed = ""
            if getattr(embed, "url", None):
                url_embed = esc(str(embed.url))

            provedor = ""
            if getattr(embed, "provider", None) and embed.provider.name:
                provedor = esc(embed.provider.name)

            tem_campos = bool(getattr(embed, "fields", None))
            tem_rodape = bool(embed.footer and getattr(embed.footer, "text", None))
            url_imagem = ""
            if embed.image and embed.image.url:
                url_imagem = str(embed.image.url)
            url_thumb = ""
            if embed.thumbnail and embed.thumbnail.url:
                url_thumb = str(embed.thumbnail.url)
            url_video = ""
            if embed.video and embed.video.url:
                url_video = str(embed.video.url)

            # Imagem principal do preview (image > thumbnail > autor icon)
            url_preview_img = url_imagem or url_thumb or url_autor_icon
            tem_conteudo = any(
                [
                    titulo_do_embed,
                    descricao_do_embed,
                    autor_do_embed,
                    tem_campos,
                    tem_rodape,
                    url_preview_img,
                    url_video,
                    url_embed,
                    provedor,
                ]
            )
            if not tem_conteudo:
                continue

            cor_barra = ""
            try:
                cor_val = getattr(
                    getattr(embed, "colour", None) or getattr(embed, "color", None),
                    "value",
                    None,
                )
                if cor_val:
                    cor_barra = f"border-left-color:#{int(cor_val):06x};"
            except Exception:
                cor_barra = ""

            # Domínio para o rodapé do card (medal.tv, youtube.com, …)
            dominio = ""
            try:
                from urllib.parse import urlparse

                bruto_url = str(
                    getattr(embed, "url", None)
                    or (url_embed.replace("&amp;", "&") if url_embed else "")
                    or ""
                )
                if bruto_url:
                    host = (urlparse(bruto_url).hostname or "").lower()
                    if host.startswith("www."):
                        host = host[4:]
                    dominio = esc(host)
            except Exception:
                dominio = ""
            if not dominio and provedor:
                dominio = provedor

            # Card de preview de link (estilo Discord / medal.tv)
            estilo_attr = f' style="{cor_barra}"' if cor_barra else ""
            if url_embed:
                saida.append(
                    f'<a class="link-preview embed" href="{url_embed}" '
                    f'target="_blank" rel="noopener noreferrer"{estilo_attr}>'
                )
                tag_fecha = "a"
            else:
                saida.append(f'<div class="link-preview embed"{estilo_attr}>')
                tag_fecha = "div"

            if url_preview_img:
                url_img_est = await midia_estavel(url_preview_img, "image/*")
                saida.append(
                    '<div class="link-preview-media">'
                    f'<img class="link-preview-image" src="{esc(url_img_est)}" '
                    f'alt="{titulo_do_embed or dominio or "preview"}" '
                    f'loading="lazy" decoding="async">'
                    "</div>"
                )
            elif url_video:
                url_vid_est = await midia_estavel(url_video, "video/*")
                saida.append(
                    '<div class="link-preview-media">'
                    f'<video class="link-preview-video" controls preload="metadata" '
                    f'src="{esc(url_vid_est)}"></video>'
                    "</div>"
                )

            saida.append('<div class="link-preview-body">')
            if dominio:
                saida.append(
                    f'<div class="link-preview-provider">{dominio}</div>'
                )
            elif provedor:
                saida.append(
                    f'<div class="link-preview-provider">{provedor}</div>'
                )
            if autor_do_embed and autor_do_embed != dominio:
                saida.append(
                    f'<div class="link-preview-author">{autor_do_embed}</div>'
                )
            if titulo_do_embed:
                saida.append(
                    f'<div class="link-preview-title">{titulo_do_embed}</div>'
                )
            elif url_embed and not titulo_do_embed:
                saida.append(
                    f'<div class="link-preview-title">{url_embed}</div>'
                )
            if descricao_do_embed:
                saida.append(
                    f'<div class="link-preview-desc">{descricao_do_embed}</div>'
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
                    f'<div class="link-preview-footer">{esc(embed.footer.text)}</div>'
                )
            saida.append("</div>")  # .link-preview-body
            saida.append(f"</{tag_fecha}>")

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
            saida.append('<div class="media-gallery">')
            for anexo in mensagem.attachments:
                nome_arq = esc(anexo.filename or "arquivo")
                tipo = (anexo.content_type or "").lower()
                url_estavel = await anexo_estavel(anexo)
                url_esc = esc(url_estavel)
                is_img = tipo.startswith("image/") or (
                    anexo.filename or ""
                ).lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
                is_vid = tipo.startswith("video/") or (
                    anexo.filename or ""
                ).lower().endswith((".mp4", ".webm", ".mov"))
                is_aud = tipo.startswith("audio/")
                if is_img:
                    saida.append(
                        '<div class="evidence">'
                        f'<button type="button" class="evidence-button" data-image-src="{url_esc}" '
                        f'data-image-name="{nome_arq}" aria-label="Ampliar imagem {nome_arq}">'
                        f'<img class="evidence-image" data-evidence="" src="{url_esc}" '
                        f'alt="{nome_arq}" loading="lazy" decoding="async">'
                        '<span class="enlarge-label">'
                        '<svg class="icon" aria-hidden="true"><use href="#i-expand"></use></svg>'
                        "Ampliar imagem</span></button>"
                        '<div class="attachment-info">'
                        '<svg class="icon" aria-hidden="true"><use href="#i-image"></use></svg>'
                        f'<div><p class="attachment-name">{nome_arq}</p>'
                        '<p class="attachment-details">Arquivo do atendimento</p></div>'
                        f'<a class="attachment-download" href="{url_esc}" target="_blank" '
                        f'rel="noopener noreferrer" download aria-label="Abrir arquivo {nome_arq}">Abrir</a>'
                        "</div></div>"
                    )
                elif is_vid:
                    saida.append(
                        '<div class="evidence">'
                        f'<video class="evidence-video" controls preload="metadata" src="{url_esc}"></video>'
                        '<div class="attachment-info">'
                        '<svg class="icon" aria-hidden="true"><use href="#i-file"></use></svg>'
                        f'<div><p class="attachment-name">{nome_arq}</p>'
                        '<p class="attachment-details">Vídeo do atendimento</p></div></div></div>'
                    )
                elif is_aud:
                    saida.append(
                        '<div class="evidence">'
                        f'<audio class="evidence-audio" controls src="{url_esc}"></audio>'
                        '<div class="attachment-info">'
                        f'<div><p class="attachment-name">{nome_arq}</p>'
                        '<p class="attachment-details">Áudio do atendimento</p></div></div></div>'
                    )
                else:
                    saida.append(
                        '<div class="evidence">'
                        '<div class="attachment-info">'
                        '<svg class="icon" aria-hidden="true"><use href="#i-file"></use></svg>'
                        f'<div><p class="attachment-name">{nome_arq}</p>'
                        '<p class="attachment-details">Arquivo do atendimento</p></div>'
                        f'<a class="attachment-download" href="{url_esc}" target="_blank" '
                        f'rel="noopener noreferrer" download>Abrir</a></div></div>'
                    )
            saida.append("</div>")

        if (
            not texto_bruto
            and not texto_opcoes_staff
            and not mensagem.attachments
            and not any(
                (getattr(emb, "title", None) or getattr(emb, "description", None))
                for emb in (mensagem.embeds or [])
            )
            and not botoes
            and not getattr(mensagem, "stickers", None)
        ):
            saida.append(
                '<div class="message-text"><em>(mensagem sem texto legível)</em></div>'
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
    montar_html_transcript._ultimo_dia = None  # type: ignore[attr-defined]
    for grupo in grupos:
        mensagem_cabeca = grupo[0]
        autor = mensagem_cabeca.author
        nome = esc(getattr(autor, "display_name", None) or autor.name)
        avatar = esc(url_avatar(autor))
        quando = formatar_horario(mensagem_cabeca.created_at)

        # article.message-row — estrutura da tela-chat
        avatar_classes = ["avatar"]
        badges_html: list[str] = []
        if autor.bot:
            avatar_classes.append("bot")
            badges_html.append('<span class="author-badge bot">BOT</span>')
        eh_staff = autor.id in ids_staff
        if not eh_staff and isinstance(autor, discord.Member):
            eh_staff = membro_eh_staff_ticket(autor)
        if eh_staff and not autor.bot:
            avatar_classes.append("staff")
            badges_html.append('<span class="author-badge staff">STAFF</span>')
        if autor.id == ticket.autor_discord_id and not autor.bot:
            badges_html.append('<span class="author-badge autor">AUTOR</span>')

        criado = mensagem_cabeca.created_at
        iso_dt = ""
        try:
            iso_dt = criado.isoformat().replace("+00:00", "Z") if criado else ""
        except Exception:
            iso_dt = ""

        # Separador de data (tela-chat)
        try:
            from src.utils.formatacao import para_horario_brasilia

            local_dt = para_horario_brasilia(criado) if criado else None
        except Exception:
            local_dt = criado
        if local_dt is not None:
            chave_dia = local_dt.strftime("%Y-%m-%d")
            rotulo_dia = local_dt.strftime("%d de %B de %Y")
            # meses em pt-BR simples
            meses = {
                "January": "janeiro",
                "February": "fevereiro",
                "March": "março",
                "April": "abril",
                "May": "maio",
                "June": "junho",
                "July": "julho",
                "August": "agosto",
                "September": "setembro",
                "October": "outubro",
                "November": "novembro",
                "December": "dezembro",
            }
            for en, pt in meses.items():
                rotulo_dia = rotulo_dia.replace(en, pt)
            if not hasattr(montar_html_transcript, "_ultimo_dia"):
                montar_html_transcript._ultimo_dia = None  # type: ignore[attr-defined]
            if montar_html_transcript._ultimo_dia != chave_dia:  # type: ignore[attr-defined]
                partes_mensagens.append(
                    f'<div class="date-divider">{esc(rotulo_dia)}</div>'
                )
                montar_html_transcript._ultimo_dia = chave_dia  # type: ignore[attr-defined]

        partes_mensagens.append('<article class="message-row" data-message="">')
        partes_mensagens.append(
            f'<img class="{" ".join(avatar_classes)}" data-avatar="" '
            f'src="{avatar}" alt="Avatar de {nome}" loading="lazy" '
            f'decoding="async" referrerpolicy="no-referrer">'
        )
        partes_mensagens.append('<div class="message-body">')
        partes_mensagens.append('<div class="message-meta">')
        partes_mensagens.append(f'<span class="author-name">{nome}</span>')
        partes_mensagens.extend(badges_html)
        if iso_dt:
            partes_mensagens.append(
                f'<time class="message-time" datetime="{esc(iso_dt)}">{quando}</time>'
            )
        else:
            partes_mensagens.append(f'<time class="message-time">{quando}</time>')
        partes_mensagens.append("</div>")  # .message-meta

        for mensagem_do_grupo in grupo:
            dados = preparar_conteudo_mensagem(mensagem_do_grupo)
            partes_mensagens.extend(
                await renderizar_bloco_conteudo(dados, autor_eh_bot=bool(autor.bot))
            )

        partes_mensagens.append("</div>")  # .message-body
        partes_mensagens.append("</article>")  # .message-row

    html_mensagens = "\n".join(partes_mensagens)

    titulo_pagina = f"Transcript #{ticket.id} — {ticket.categoria_rotulo or 'Ticket'}"
    info_canal = f"Transcript · {ticket.categoria_rotulo or 'Ticket'} · #{ticket.id}"
    try:
        total_msgs = str(sum(len(g) for g in grupos))
    except Exception:
        total_msgs = "0"
    categoria = ticket.categoria_rotulo or "Ticket"
    autor_nome = ticket.autor_nome or "—"
    staff_nome = ticket.staff_finalizou_nome or "—"
    autor_id = getattr(ticket, "autor_discord_id", None) or ""
    staff_id = (
        getattr(ticket, "staff_finalizou_id", None)
        or getattr(ticket, "staff_assumiu_id", None)
        or ""
    )
    autor_avatar = "https://cdn.discordapp.com/embed/avatars/0.png"
    staff_avatar = "https://cdn.discordapp.com/embed/avatars/1.png"
    try:
        if guilda is not None and autor_id:
            membro = guilda.get_member(int(autor_id))
            if membro is not None:
                autor_avatar = str(membro.display_avatar.replace(size=128).url)
        if guilda is not None and staff_id:
            membro_s = guilda.get_member(int(staff_id))
            if membro_s is not None:
                staff_avatar = str(membro_s.display_avatar.replace(size=128).url)
    except Exception:
        pass
    # Datas brutas (datetime) para cálculo de duração; strings formatadas só para HTML.
    aberto_em_dt = getattr(ticket, "criado_em", None) or getattr(
        ticket, "aberto_em", None
    )
    finalizado_em_dt = getattr(ticket, "finalizado_em", None)
    aberto_em = formatar_horario(aberto_em_dt)
    finalizado_em = formatar_horario(finalizado_em_dt)
    anexos_count = "0"
    try:
        anexos_count = str(
            sum(len(getattr(m, "attachments", None) or []) for g in grupos for m in g)
        )
    except Exception:
        pass

    html_final = template_html
    html_final = html_final.replace("{{TITULO_PAGINA}}", esc(titulo_pagina))
    html_final = html_final.replace("{{NOME_GUILDA}}", nome_guilda)
    html_final = html_final.replace("{{ICONE_GUILDA}}", icone)
    html_final = html_final.replace("{{INFO_CANAL}}", esc(info_canal))
    html_final = html_final.replace("{{MENSAGENS}}", html_mensagens)
    html_final = html_final.replace("{{TOTAL_MSGS}}", esc(total_msgs))
    html_final = html_final.replace("{{CATEGORIA}}", esc(categoria))
    html_final = html_final.replace("{{TICKET_ID}}", str(int(ticket.id)))
    html_final = html_final.replace("{{AUTOR_NOME}}", esc(autor_nome))
    html_final = html_final.replace("{{STAFF_NOME}}", esc(staff_nome))
    # Nome do canal do ticket (ex: 🙋・imguxta), não o rótulo da categoria
    titulo_canal = ""
    try:
        canal_obj = (
            guilda.get_channel(int(ticket.canal_id))
            if guilda and ticket.canal_id
            else None
        )
        if canal_obj is not None and getattr(canal_obj, "name", None):
            titulo_canal = str(canal_obj.name)
    except Exception:
        titulo_canal = ""
    if not titulo_canal:
        # fallback: nome do canal nas mensagens coletadas
        for msg in mensagens:
            ch = getattr(msg, "channel", None)
            if ch is not None and getattr(ch, "name", None):
                titulo_canal = str(ch.name)
                break
    if not titulo_canal:
        titulo_canal = f"Ticket #{ticket.id}"

    tempo_atendimento = formatar_tempo_atendimento(aberto_em_dt, finalizado_em_dt)

    html_final = html_final.replace("{{TITULO_CANAL}}", esc(titulo_canal))
    html_final = html_final.replace("{{ANEXOS_COUNT}}", esc(anexos_count))
    html_final = html_final.replace("{{AUTOR_AVATAR}}", esc(autor_avatar))
    html_final = html_final.replace("{{STAFF_AVATAR}}", esc(staff_avatar))
    html_final = html_final.replace("{{AUTOR_ID}}", esc(str(autor_id or "—")))
    html_final = html_final.replace("{{STAFF_ID}}", esc(str(staff_id or "—")))
    html_final = html_final.replace("{{ABERTO_EM}}", esc(aberto_em))
    html_final = html_final.replace("{{FINALIZADO_EM}}", esc(finalizado_em))
    html_final = html_final.replace("{{TEMPO_ATENDIMENTO}}", esc(tempo_atendimento))
    return html_final


def formatar_tempo_atendimento(aberto_em, finalizado_em):
    """Calcula duração legível entre abertura e finalização do ticket.

    Espera objetos datetime (ou None). Strings formatadas não são aceitas.
    """
    from datetime import datetime

    if not aberto_em or not finalizado_em:
        return "—"

    if not isinstance(aberto_em, datetime) or not isinstance(finalizado_em, datetime):
        return "—"

    try:
        delta = finalizado_em - aberto_em
    except TypeError:
        # Ex.: um aware e outro naive
        return "—"

    total_minutos = int(delta.total_seconds() // 60)

    # Finalizado antes de aberto (clock skew / dados inconsistentes)
    if total_minutos < 0:
        return "—"

    dias = total_minutos // (24 * 60)
    horas = (total_minutos % (24 * 60)) // 60
    minutos = total_minutos % 60

    partes = []
    if dias:
        partes.append(f"{dias}d")
    if horas:
        partes.append(f"{horas}h")
    if minutos or not partes:
        partes.append(f"{minutos}min")

    return " ".join(partes)

