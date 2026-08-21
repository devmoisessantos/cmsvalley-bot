"""
Ouvintes de auditoria do Discord (domínio eventos).

Cada tipo de evento publica no canal configurado em CANAIS via
``publicar_log_auditoria`` (helper único em utils/logger.py).

LOG_HORAS fica no plantão (plantao_logger). Aqui só LOG_VOZ para calls.

Se um canal de log estiver com ID 0, aquele tipo fica desligado.
"""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)

import discord
from discord.ext import commands

from src.config import (
    CANAIS,
    GUILD_ID,
)
from src.eventos.eventos_mensagens_views import (
    montar_view_mensagem_apagada,
    montar_view_mensagem_editada,
)
from src.utils.formatacao import formatar_hms
from src.utils.logger import (
    COR_AVISO,
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    buscar_executor_no_audit_log,
    cargo_ja_foi_logado_pelo_bot,
    log_mudanca_cargo,
    obter_id_do_canal_de_log,
    publicar_log_auditoria,
)

registrador = logging.getLogger(__name__)

# Preenchido no setup — o helper usa para fetch_channel se o cache estiver frio
_bot_global: commands.Bot | None = None


async def _publicar(
    guilda: discord.Guild,
    chave: str,
    *,
    titulo: str,
    linhas: list[str] | str,
    cor=None,
    url_do_avatar: str | None = None,
    arquivos=None,
    abrir_topico_para_anexos: bool = False,
    nome_do_topico: str | None = None,
    url_do_link: str | None = None,
    rotulo_do_link: str = "Abrir mensagem",
    blocos_extra: list[str] | None = None,
):
    """Atalho: sempre envia o cliente do bot e captura erro do listener."""
    kwargs = {
        "titulo": titulo,
        "linhas": linhas,
        "url_do_avatar": url_do_avatar,
        "cliente": _bot_global,
        "arquivos": arquivos,
        "abrir_topico_para_anexos": abrir_topico_para_anexos,
        "nome_do_topico": nome_do_topico,
        "url_do_link": url_do_link,
        "rotulo_do_link": rotulo_do_link,
        "blocos_extra": blocos_extra,
    }
    if cor is not None:
        kwargs["cor"] = cor
    try:
        return await publicar_log_auditoria(guilda, chave, **kwargs)
    except Exception:
        registrador.exception("Falha no listener ao publicar %s (%s)", chave, titulo)
        return None


def _servidor_correto(guilda: discord.Guild | None) -> bool:
    """
    Só processa a guilda principal.

    Se GUILD_ID não estiver configurado (0), aceita qualquer guilda para
    não silenciar todos os logs por configuração incompleta.
    """
    if guilda is None:
        return False
    try:
        id_configurado = int(GUILD_ID or 0)
    except (TypeError, ValueError):
        id_configurado = 0
    if id_configurado <= 0:
        return True
    return int(guilda.id) == id_configurado


def _mencao(usuario: discord.abc.User | None) -> str:
    if usuario is None:
        return "_desconhecido_"
    return f"{usuario.mention}"


def _ts(data: datetime | None, estilo: str = "F") -> str:
    if data is None:
        return "—"
    if data.tzinfo is None:
        data = data.replace(tzinfo=timezone.utc)
    return f"<t:{int(data.timestamp())}:{estilo}>"


async def _fid(discord_id: int) -> str:
    """ID FiveM quando existir; senão traço."""
    try:
        from src.recrutamento.recrutamento_service import resolver_id_fivem

        valor = await resolver_id_fivem(int(discord_id))
        if valor:
            return str(valor)
    except Exception:
        pass
    return "—"


def _cargo_principal(membro: discord.Member) -> str:
    """Menção do cargo mais alto (exceto @everyone)."""
    cargos = [c for c in membro.roles if not c.is_default()]
    if not cargos:
        return "—"
    cargo = max(cargos, key=lambda c: c.position)
    return cargo.mention


class EventosAuditoriaCog(commands.Cog):
    """
    Centraliza os listeners de auditoria do servidor.

    - Canais, mensagens, voz, apelidos, avatares, cargos, moderação
    - Horas em call (servidor inteiro, independente de plantão)
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ids_de_edicao_recentes: set[int] = set()
        # Sessão de voz para calcular tempo conectado no LOG_VOZ
        # discord_id -> {"canal_id": int, "entrou_em": datetime}
        self._sessoes_de_voz: dict[int, dict] = {}
        # Total de segundos em voz nesta execução do bot
        self._total_segundos_em_voz: dict[int, int] = {}

    # ── Canais ───────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, canal: discord.abc.GuildChannel):
        """Canal criado."""
        if not _servidor_correto(canal.guild):
            return
        executor = await buscar_executor_no_audit_log(
            canal.guild,
            discord.AuditLogAction.channel_create,
            alvo_id=canal.id,
        )
        tipo = (
            "Categoria"
            if isinstance(canal, discord.CategoryChannel)
            else (
                "Canal de voz"
                if isinstance(canal, discord.VoiceChannel)
                else "Canal de texto"
            )
        )
        mencao = getattr(canal, "mention", f"`{canal.name}`")
        await _publicar(
            canal.guild,
            "LOG_CANAIS",
            titulo="📁 Canal criado",
            linhas=[
                f"-  `#️⃣` **Canal:** {mencao} (`{canal.id}`)",
                f"-  `🏷️` **Nome:** `{canal.name}`",
                f"-  `📁` **Tipo:** {tipo}",
                f"-  `📂` **Categoria:** `{canal.category.name if canal.category else '—'}`",
                f"-  `👤` **Criado por:** {_mencao(executor)}",
            ],
            cor=COR_SUCESSO,
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, canal: discord.abc.GuildChannel):
        """Canal excluído."""
        if not _servidor_correto(canal.guild):
            return
        executor = await buscar_executor_no_audit_log(
            canal.guild,
            discord.AuditLogAction.channel_delete,
            alvo_id=canal.id,
        )
        tipo = (
            "Categoria"
            if isinstance(canal, discord.CategoryChannel)
            else (
                "Canal de voz"
                if isinstance(canal, discord.VoiceChannel)
                else "Canal de texto"
            )
        )
        categoria = canal.category
        if categoria is not None:
            linha_cat = (
                f"-  `📂` **Categoria:** {categoria.name} **ID:** (`{categoria.id}`)"
            )
        else:
            linha_cat = "-  `📂` **Categoria:** —"
        await _publicar(
            canal.guild,
            "LOG_CANAIS",
            titulo="🗑️ Canal excluído",
            linhas=[
                f"-  `🏷️` **Nome:** {canal.name} (`{canal.id}`)",
                f"-  `📁` **Tipo:** {tipo}",
                linha_cat,
                f"-  `👤` **Excluído por:** {_mencao(executor)}",
            ],
            cor=COR_ERRO,
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        antes: discord.abc.GuildChannel,
        depois: discord.abc.GuildChannel,
    ):
        """Renomear, mover, tópico, slowmode, NSFW, permissões."""
        if not _servidor_correto(depois.guild):
            return

        alteracoes: list[tuple[str, list[str]]] = []

        if antes.name != depois.name:
            alteracoes.append(
                (
                    "Canal renomeado",
                    [
                        f"-  `⬅️` **Antes:** `{antes.name}`",
                        f"-  `➡️` **Depois:** `{depois.name}`",
                    ],
                )
            )

        cat_antes = antes.category.id if antes.category else None
        cat_depois = depois.category.id if depois.category else None
        if cat_antes != cat_depois:
            nome_antes = antes.category.name if antes.category else "—"
            nome_depois = depois.category.name if depois.category else "—"
            alteracoes.append(
                (
                    "Canal movido de categoria",
                    [
                        f"-  `⬅️` **Antes:** `{nome_antes}`",
                        f"-  `➡️` **Depois:** `{nome_depois}`",
                    ],
                )
            )

        if isinstance(antes, discord.TextChannel) and isinstance(
            depois, discord.TextChannel
        ):
            if (antes.topic or "") != (depois.topic or ""):
                alteracoes.append(
                    (
                        "Tópico/descrição alterado",
                        [
                            f"**Antes:** {(antes.topic or '—')[:300]}",
                            f"**Depois:** {(depois.topic or '—')[:300]}",
                        ],
                    )
                )
            if antes.slowmode_delay != depois.slowmode_delay:
                alteracoes.append(
                    (
                        "Slowmode alterado",
                        [
                            f"**Antes:** `{antes.slowmode_delay}s`",
                            f"**Depois:** `{depois.slowmode_delay}s`",
                        ],
                    )
                )
            if antes.nsfw != depois.nsfw:
                estado = "ativado" if depois.nsfw else "desativado"
                alteracoes.append(
                    ("NSFW ativado/desativado", [f"**Estado:** {estado}"])
                )

        if antes.overwrites != depois.overwrites:
            alteracoes.append(
                (
                    "Permissões do canal alteradas",
                    ["**Detalhe:** overwrites modificados"],
                )
            )

        if not alteracoes:
            return

        executor = await buscar_executor_no_audit_log(
            depois.guild,
            discord.AuditLogAction.channel_update,
            alvo_id=depois.id,
        )
        fid_executor = "—"
        if executor is not None:
            fid_executor = await _fid(executor.id)

        for titulo_alt, detalhe in alteracoes:
            linhas = [
                f"-  `#️⃣` **Canal:** {getattr(depois, 'mention', depois.name)} "
                f"(`{depois.id}`)",
                f"-  `🔄` **Alteração:** {titulo_alt}",
                *detalhe,
                f"-  `👤` **Responsável pela alteração:** {_mencao(executor)} "
                f"**FID:** `{fid_executor}`",
            ]
            await _publicar(
                depois.guild,
                "LOG_CANAIS",
                titulo="✏️ Canal atualizado",
                linhas=linhas,
                cor=COR_AVISO,
            )

    # ── Mensagens ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_delete(self, mensagem: discord.Message):
        if mensagem.guild is None or not _servidor_correto(mensagem.guild):
            return
        if mensagem.author and mensagem.author.bot:
            return

        executor = await buscar_executor_no_audit_log(
            mensagem.guild,
            discord.AuditLogAction.message_delete,
            alvo_id=mensagem.author.id if mensagem.author else None,
        )
        conteudo = (mensagem.content or "").strip() or "_sem texto_"
        if len(conteudo) > 1800:
            conteudo = conteudo[:1800] + "…"

        view = montar_view_mensagem_apagada(
            guilda=mensagem.guild,
            autor=mensagem.author,
            canal=mensagem.channel,
            conteudo=conteudo,
            enviada_em=mensagem.created_at,
            apagada_em=datetime.now(timezone.utc),
            id_da_mensagem=mensagem.id,
            quem_apagou=executor,
        )

        # Anexos da mensagem original vão em tópico (arquivado após 2s)
        arquivos: list[discord.File] = []
        for anexo in mensagem.attachments[:5]:
            try:
                arquivos.append(await anexo.to_file())
            except (discord.HTTPException, discord.NotFound):
                pass

        import asyncio

        from src.utils.logger import resolver_canal_de_log

        canal_log = await resolver_canal_de_log(
            mensagem.guild,
            "LOG_MENSAGENS_DELETADAS",
            cliente=self.bot,
        )
        if canal_log is None:
            return
        try:
            msg_log = await canal_log.send(view=view)
            registrador.info(
                "Log publicado em LOG_MENSAGENS_DELETADAS (canal %s)",
                getattr(canal_log, "id", "?"),
            )
            if arquivos:
                try:
                    topico = await msg_log.create_thread(
                        name=f"anexos-{mensagem.id}"[:100]
                    )
                    await topico.send(files=arquivos)
                    await asyncio.sleep(2)
                    try:
                        await topico.edit(
                            archived=True,
                            locked=True,
                            reason="Arquivar anexos da mensagem apagada",
                        )
                    except discord.HTTPException:
                        await topico.edit(archived=True)
                except (discord.Forbidden, discord.HTTPException) as erro:
                    registrador.warning(
                        "Tópico de anexos (msg apagada) falhou: %s", erro
                    )
        except Exception:
            registrador.exception("Falha ao publicar LOG_MENSAGENS_DELETADAS")

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, mensagens: list[discord.Message]):
        if not mensagens:
            return
        guilda = mensagens[0].guild
        if guilda is None or not _servidor_correto(guilda):
            return
        await _publicar(
            guilda,
            "LOG_MENSAGENS_DELETADAS",
            titulo="🔍 🗑️ Mensagens apagadas em massa",
            linhas=[
                f"**Canal:** {mensagens[0].channel.mention}",
                f"**Quantidade:** **{len(mensagens)}**",
                f"**Quando:** {_ts(datetime.now(timezone.utc))}",
            ],
            cor=COR_ERRO,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, antes: discord.Message, depois: discord.Message):
        if depois.guild is None or not _servidor_correto(depois.guild):
            return
        if depois.author and depois.author.bot:
            return
        if (antes.content or "") == (depois.content or ""):
            return
        if depois.id in self._ids_de_edicao_recentes:
            return
        self._ids_de_edicao_recentes.add(depois.id)
        if len(self._ids_de_edicao_recentes) > 300:
            self._ids_de_edicao_recentes.clear()

        view = montar_view_mensagem_editada(
            guilda=depois.guild,
            autor=depois.author,
            canal=depois.channel,
            conteudo_anterior=antes.content or "",
            conteudo_novo=depois.content or "",
            id_da_mensagem=depois.id,
            url_da_mensagem=depois.jump_url,
        )

        from src.utils.logger import resolver_canal_de_log

        canal_log = await resolver_canal_de_log(
            depois.guild,
            "LOG_MENSAGENS_EDITADAS",
            cliente=self.bot,
        )
        if canal_log is None:
            return
        try:
            await canal_log.send(view=view)
            registrador.info(
                "Log publicado em LOG_MENSAGENS_EDITADAS (canal %s)",
                getattr(canal_log, "id", "?"),
            )
        except Exception:
            registrador.exception("Falha ao publicar LOG_MENSAGENS_EDITADAS")

    # ── Voz (LOG_VOZ) ───────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        membro: discord.Member,
        antes: discord.VoiceState,
        depois: discord.VoiceState,
    ):
        if not _servidor_correto(membro.guild):
            return
        if membro.bot:
            return

        canal_antes = antes.channel
        canal_depois = depois.channel
        agora = datetime.now(timezone.utc)

        # Entrada em call
        if canal_antes is None and canal_depois is not None:
            self._sessoes_de_voz[membro.id] = {
                "canal_id": canal_depois.id,
                "entrou_em": agora,
            }
            await _publicar(
                membro.guild,
                "LOG_VOZ",
                titulo="🔊 Entrada em canal de voz",
                linhas=[
                    f"-  `👤` **Membro:** {_mencao(membro)}",
                    f"-  `#️⃣` **Canal:** {canal_depois.mention} "
                    f"**ID:** (`{canal_depois.id}`)",
                    f"-  `➡️` **Entrada:** {_ts(agora)}",
                ],
                cor=COR_SUCESSO,
                url_do_avatar=membro.display_avatar.url,
            )
            return

        # Saída da call
        if canal_antes is not None and canal_depois is None:
            sessao = self._sessoes_de_voz.pop(membro.id, None)
            entrou_em = sessao["entrou_em"] if sessao else None
            if entrou_em is None and antes.channel:
                # Sem sessão em memória (restart): ainda loga saída sem duração
                entrou_em = None
            duracao = 0
            if entrou_em is not None:
                if entrou_em.tzinfo is None:
                    entrou_em = entrou_em.replace(tzinfo=timezone.utc)
                duracao = max(0, int((agora - entrou_em).total_seconds()))
                self._total_segundos_em_voz[membro.id] = (
                    int(self._total_segundos_em_voz.get(membro.id, 0)) + duracao
                )

            await _publicar(
                membro.guild,
                "LOG_VOZ",
                titulo="🚪 Saída do canal de voz",
                linhas=[
                    f"-  `👤` **Membro:** {_mencao(membro)}",
                    f"-  `#️⃣` **Canal:** {canal_antes.mention} "
                    f"**ID:** (`{canal_antes.id}`)",
                    f"-  `➡️` **Entrou:** {_ts(entrou_em, 't') if entrou_em else '—'}",
                    f"-  `⬅️` **Saiu:** {_ts(agora, 't')}",
                    f"-  `⏱️` **Tempo conectado:** "
                    f"`{formatar_hms(duracao) if duracao else '—'}`",
                ],
                cor=COR_AVISO,
                url_do_avatar=membro.display_avatar.url,
            )
            return

        # Mudou de canal
        if (
            canal_antes is not None
            and canal_depois is not None
            and canal_antes.id != canal_depois.id
        ):
            # Atualiza sessão de horas para o novo canal (mantém início original)
            if membro.id in self._sessoes_de_voz:
                self._sessoes_de_voz[membro.id]["canal_id"] = canal_depois.id
            else:
                self._sessoes_de_voz[membro.id] = {
                    "canal_id": canal_depois.id,
                    "entrou_em": agora,
                }

            executor = await buscar_executor_no_audit_log(
                membro.guild,
                discord.AuditLogAction.member_move,
                alvo_id=membro.id,
            )
            if executor is not None:
                titulo = "🔁 Foi movido de call"
                extra = [f"-  `👤` **Movido por:** {_mencao(executor)}"]
            else:
                titulo = "🔁 Se moveu de call"
                extra = []

            await _publicar(
                membro.guild,
                "LOG_VOZ",
                titulo=titulo,
                linhas=[
                    f"-  `👤` **Membro:** {_mencao(membro)}",
                    f"-  `⬅️` **De:** {canal_antes.mention} --> "
                    f"`➡️` **Para:** {canal_depois.mention}",
                    *extra,
                    f"-  `📅` **Data:** {_ts(agora)}",
                ],
                cor=COR_INFO,
                url_do_avatar=membro.display_avatar.url,
            )

        # Mute / surdez de servidor / stream
        if antes.mute != depois.mute:
            responsavel = await buscar_executor_no_audit_log(
                membro.guild,
                discord.AuditLogAction.member_update,
                alvo_id=membro.id,
            )
            estado = "mutado" if depois.mute else "desmutado"
            titulo_mute = (
                "🔇 Silenciar voz do servidor"
                if depois.mute
                else "🔊 Reativar voz do servidor"
            )
            await _publicar(
                membro.guild,
                "LOG_VOZ",
                titulo=titulo_mute,
                linhas=[
                    f"-  `👤` **Membro:** {_mencao(membro)}",
                    f"-  `🔇` **Mute servidor:** {estado}",
                    f"-  `👤` **Mutado por:** {_mencao(responsavel)}",
                ],
                cor=COR_AVISO,
                url_do_avatar=membro.display_avatar.url,
            )

        if antes.deaf != depois.deaf:
            responsavel = await buscar_executor_no_audit_log(
                membro.guild,
                discord.AuditLogAction.member_update,
                alvo_id=membro.id,
            )
            estado = "ensurdecido" if depois.deaf else "desensurdecido"
            titulo_surdez = (
                "🙉 Desativar áudio do servidor"
                if depois.deaf
                else "👂 Reativar áudio do servidor"
            )
            await _publicar(
                membro.guild,
                "LOG_VOZ",
                titulo=titulo_surdez,
                linhas=[
                    f"-  `👤` **Membro:** {_mencao(membro)}",
                    f"-  `🙉` **Surdez servidor:** {estado}",
                    f"-  `👤` **Alterado por:** {_mencao(responsavel)}",
                ],
                cor=COR_AVISO,
                url_do_avatar=membro.display_avatar.url,
            )

        if antes.self_stream != depois.self_stream:
            estado = "começou" if depois.self_stream else "parou"
            await _publicar(
                membro.guild,
                "LOG_VOZ",
                titulo="📺 Transmissão",
                linhas=[
                    f"-  `👤` **Membro:** {_mencao(membro)}",
                    f"-  `📺` **Transmissão:** {estado}",
                ],
                cor=COR_INFO,
                url_do_avatar=membro.display_avatar.url,
            )

    # ── Apelidos, avatares, cargos ───────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, antes: discord.Member, depois: discord.Member):
        if not _servidor_correto(depois.guild):
            return

        if antes.nick != depois.nick:
            executor = await buscar_executor_no_audit_log(
                depois.guild,
                discord.AuditLogAction.member_update,
                alvo_id=depois.id,
            )
            if executor is None or executor.id == depois.id:
                texto_executor = "Próprio usuário"
            else:
                texto_executor = _mencao(executor)
            fid = await _fid(depois.id)
            await _publicar(
                depois.guild,
                "LOG_APELIDOS",
                titulo="🪪 Apelido alterado",
                linhas=[
                    f"-  `👤` **Membro:** {_mencao(depois)} **FID:** (`{fid}`)",
                    f"-  `⬅️` **Antes:** `{antes.nick or '—'}`",
                    f"-  `➡️` **Depois:** `{depois.nick or '—'}`",
                    f"-  `👤` **Alterado por:** {texto_executor}",
                ],
                cor=COR_INFO,
                url_do_avatar=depois.display_avatar.url,
            )

        url_guild_antes = antes.guild_avatar.url if antes.guild_avatar else None
        url_guild_depois = depois.guild_avatar.url if depois.guild_avatar else None
        if url_guild_antes != url_guild_depois:
            bytes_antigos = await self._ler_bytes_do_asset(
                antes.guild_avatar,
                rotulo="avatar do servidor",
                user_id=depois.id,
            )
            await self._log_avatar(
                depois,
                tipo="Avatar do servidor",
                bytes_antigos=bytes_antigos,
                url_antiga=url_guild_antes,
                url_nova=url_guild_depois or depois.display_avatar.url,
            )

        ids_antes = {c.id for c in antes.roles}
        ids_depois = {c.id for c in depois.roles}
        if ids_antes != ids_depois:
            if not cargo_ja_foi_logado_pelo_bot(depois.id):
                adicionados = [
                    c.mention
                    for c in depois.roles
                    if c.id in (ids_depois - ids_antes) and not c.is_default()
                ]
                removidos = [
                    c.name
                    for c in antes.roles
                    if c.id in (ids_antes - ids_depois) and not c.is_default()
                ]
                if adicionados or removidos:
                    executor = await buscar_executor_no_audit_log(
                        depois.guild,
                        discord.AuditLogAction.member_role_update,
                        alvo_id=depois.id,
                    )
                    if executor is None:
                        executor = self.bot.user
                    await log_mudanca_cargo(
                        depois.guild,
                        candidato=depois,
                        executor=executor,
                        cargos_adicionados=adicionados or None,
                        cargos_removidos=removidos or None,
                    )

    @commands.Cog.listener()
    async def on_user_update(self, antes: discord.User, depois: discord.User):
        if antes.avatar == depois.avatar:
            return

        # Lê os bytes ANTES de qualquer outro await — o CDN do avatar
        # antigo pode deixar de responder se demorarmos demais.
        # Preferir antes.avatar (hash antigo real). display_avatar pode
        # apontar para decoração/default e atrapalhar GIF animado.
        asset_antigo = (
            antes.avatar if antes.avatar is not None else antes.display_avatar
        )
        url_antiga = None
        try:
            url_antiga = str(asset_antigo.url) if asset_antigo else None
        except Exception:
            url_antiga = None
        bytes_antigos = await self._ler_bytes_do_asset(
            asset_antigo,
            rotulo="avatar global",
            user_id=antes.id,
        )

        try:
            id_guild = int(GUILD_ID or 0)
        except (TypeError, ValueError):
            id_guild = 0
        guilda = self.bot.get_guild(id_guild) if id_guild else None
        if guilda is None and self.bot.guilds:
            guilda = self.bot.guilds[0]
        if guilda is None:
            return
        membro = guilda.get_member(depois.id)
        if membro is None:
            return
        await self._log_avatar(
            membro,
            tipo="Avatar global",
            bytes_antigos=bytes_antigos,
            url_antiga=url_antiga,
            url_nova=depois.display_avatar.url,
        )

    async def _ler_bytes_do_asset(
        self,
        asset: discord.Asset | None,
        *,
        rotulo: str = "avatar",
        user_id: int | None = None,
    ) -> bytes | None:
        """
        Baixa bytes de avatar (incluindo GIF animado).

        Avatares animados usam hash ``a_...`` e o CDN pode recusar
        ``?size=1024`` em alguns casos. Tentamos várias formas, na ordem:

        1. asset.to_file() / asset.read() (sem forçar size)
        2. read com sizes 256, 512, 1024
        3. get_from_cdn em URLs alternativas (.gif / sem query)
        4. aiohttp com User-Agent em cada URL candidata
        """
        if asset is None:
            return None

        urls_candidatas: list[str] = []

        def _adicionar_url(url: str | None) -> None:
            if not url:
                return
            if url not in urls_candidatas:
                urls_candidatas.append(url)

        # URL natural do asset
        try:
            _adicionar_url(str(asset.url))
        except Exception:
            pass

        # Variantes sem query e com sizes comuns
        try:
            base = str(asset.url).split("?")[0]
            _adicionar_url(base)
            for size in (256, 512, 1024, 128):
                _adicionar_url(f"{base}?size={size}")
        except Exception:
            pass

        # Montagem manual (GIF animado) a partir do hash
        try:
            hash_avatar = getattr(asset, "key", None)
            if hash_avatar and user_id:
                animado = bool(getattr(asset, "is_animated", lambda: False)())
                # key pode vir sem a_ ; is_animated é a fonte da verdade
                ext = "gif" if animado or str(hash_avatar).startswith("a_") else "png"
                base_manual = (
                    f"https://cdn.discordapp.com/avatars/{user_id}/{hash_avatar}.{ext}"
                )
                _adicionar_url(base_manual)
                for size in (256, 512, 1024):
                    _adicionar_url(f"{base_manual}?size={size}")
                # media.discordapp.net às vezes responde quando cdn falha
                base_media = base_manual.replace(
                    "cdn.discordapp.com", "media.discordapp.net"
                )
                _adicionar_url(base_media)
                for size in (256, 512):
                    _adicionar_url(f"{base_media}?size={size}")
        except Exception:
            pass

        # 1) API oficial do Asset — primeiro SEM with_size (melhor p/ GIF)
        for tentativa in (
            "read_simples",
            "to_file",
            "size_256",
            "size_512",
            "size_1024",
        ):
            try:
                if tentativa == "read_simples":
                    dados = await asset.read()
                elif tentativa == "to_file":
                    if not hasattr(asset, "to_file"):
                        continue
                    arquivo = await asset.to_file()
                    dados = arquivo.fp.read()
                    arquivo.fp.seek(0)
                elif tentativa == "size_256":
                    dados = await asset.with_size(256).read()
                elif tentativa == "size_512":
                    dados = await asset.with_size(512).read()
                else:
                    dados = await asset.with_size(1024).read()
                if dados:
                    registrador.info(
                        "Avatar %s lido via %s (%s bytes)",
                        rotulo,
                        tentativa,
                        len(dados),
                    )
                    return dados
            except Exception as erro:
                registrador.warning(
                    "Avatar %s — %s falhou: %s",
                    rotulo,
                    tentativa,
                    erro,
                )

        # 2) get_from_cdn do bot em cada URL
        if hasattr(self.bot, "http"):
            for url in urls_candidatas:
                try:
                    dados = await self.bot.http.get_from_cdn(url)
                    if dados and not (
                        len(dados) < 200 and b"Invalid resource" in dados
                    ):
                        registrador.info(
                            "Avatar %s via get_from_cdn (%s bytes) url=%s",
                            rotulo,
                            len(dados),
                            url[:80],
                        )
                        return dados
                except Exception as erro:
                    registrador.debug("get_from_cdn falhou %s: %s", url[:60], erro)

        # 3) aiohttp em cada URL
        try:
            import aiohttp

            cabecalhos = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "image/gif,image/png,image/webp,image/*,*/*;q=0.8",
            }
            async with aiohttp.ClientSession(headers=cabecalhos) as sessao:
                for url in urls_candidatas:
                    try:
                        async with sessao.get(
                            url,
                            timeout=aiohttp.ClientTimeout(total=20),
                            allow_redirects=True,
                        ) as resposta:
                            if resposta.status != 200:
                                continue
                            dados = await resposta.read()
                            if not dados:
                                continue
                            if b"Invalid resource" in dados[:200]:
                                continue
                            # JSON de erro do Discord não é imagem
                            if dados[:1] == b"{":
                                continue
                            registrador.info(
                                "Avatar %s via aiohttp (%s bytes) url=%s",
                                rotulo,
                                len(dados),
                                url[:80],
                            )
                            return dados
                    except Exception:
                        continue
        except Exception as erro:
            registrador.warning("aiohttp geral falhou (%s): %s", rotulo, erro)

        registrador.error(
            "Não foi possível obter bytes do %s | urls=%s",
            rotulo,
            urls_candidatas[:5],
        )
        return None

    def _arquivo_de_bytes(
        self,
        dados: bytes,
        nome_base: str,
    ) -> discord.File:
        """Monta discord.File a partir dos bytes brutos (png/gif/jpg)."""
        from io import BytesIO

        if dados[:6] in (b"GIF87a", b"GIF89a"):
            nome = f"{nome_base}.gif"
        elif dados[:8] == b"\x89PNG\r\n\x1a\n":
            nome = f"{nome_base}.png"
        elif dados[:2] == b"\xff\xd8":
            nome = f"{nome_base}.jpg"
        else:
            nome = f"{nome_base}.png"
        return discord.File(BytesIO(dados), filename=nome)

    async def _log_avatar(
        self,
        membro: discord.Member,
        *,
        tipo: str,
        bytes_antigos: bytes | None = None,
        url_antiga: str | None = None,
        url_nova: str | None = None,
    ) -> None:
        """
        Card no canal + tópico com a imagem ANEXADA.

        No tópico:
        - só o arquivo (Discord mostra o preview da imagem)
        - botão link opcional "Abrir no navegador" (sem URL solta no texto,
          para não gerar preview duplicado / embed quebrado)
        """
        import asyncio

        from src.utils.log_container import LogContainerView
        from src.utils.logger import resolver_canal_de_log

        linhas = "\n".join(
            [
                f"-  `👤` **Membro:** {_mencao(membro)}",
                f"-  `📁` **Tipo:** {tipo}",
            ]
        )
        view = LogContainerView(
            titulo="🖼️ Avatar alterado",
            linhas=linhas,
            guild=membro.guild,
            cor=COR_INFO,
            avatar_url=url_nova,
            link_url=url_nova,
            link_label="Abrir avatar atual",
        )

        canal_log = await resolver_canal_de_log(
            membro.guild, "LOG_AVATARES", cliente=self.bot
        )
        if canal_log is None:
            return

        arquivo_antigo = None
        if bytes_antigos:
            arquivo_antigo = self._arquivo_de_bytes(
                bytes_antigos,
                f"avatar_antigo_{membro.id}",
            )

        try:
            msg_log = await canal_log.send(view=view)
            registrador.info(
                "Log publicado em LOG_AVATARES (canal %s) arquivo=%s",
                getattr(canal_log, "id", "?"),
                "sim" if arquivo_antigo else "nao",
            )

            if arquivo_antigo is None and not url_antiga:
                return

            try:
                topico = await msg_log.create_thread(
                    name=f"avatar-antigo-{membro.id}"[:100]
                )

                # Botão link (opcional) — sem colar URL no texto
                view_do_topico = None
                if url_antiga:
                    view_do_topico = discord.ui.View(timeout=None)
                    view_do_topico.add_item(
                        discord.ui.Button(
                            style=discord.ButtonStyle.link,
                            label="Abrir no navegador",
                            url=url_antiga,
                        )
                    )

                if arquivo_antigo is not None:
                    await topico.send(
                        content="-  `🖼️` **Avatar anterior:**",
                        file=arquivo_antigo,
                        view=view_do_topico,
                    )
                else:
                    registrador.error(
                        "Avatar antigo sem bytes para %s — não anexa imagem",
                        membro.id,
                    )
                    await topico.send(
                        content=(
                            "⚠️ Não foi possível anexar a imagem do avatar "
                            "anterior (download falhou)."
                        ),
                        view=view_do_topico,
                    )

                await asyncio.sleep(2)
                try:
                    await topico.edit(
                        archived=True,
                        locked=True,
                        reason="Arquivar avatar antigo",
                    )
                except discord.HTTPException:
                    try:
                        await topico.edit(archived=True)
                    except discord.HTTPException:
                        pass
            except (discord.Forbidden, discord.HTTPException) as erro:
                registrador.warning("Tópico de avatar antigo falhou: %s", erro)
        except Exception:
            registrador.exception("Falha ao publicar LOG_AVATARES")

    # ── Entrada / saída de membros ───────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, membro: discord.Member):
        """LOG_MEMBROS: entrou no servidor."""
        if not _servidor_correto(membro.guild):
            return
        fid = await _fid(membro.id)
        await _publicar(
            membro.guild,
            "LOG_MEMBROS",
            titulo="📥 Membro entrou",
            linhas=[
                f"**Membro:** {_mencao(membro)} (`{membro.id}`)",
                f"**FID:** (`{fid}`)",
                f"**Conta criada:** {_ts(membro.created_at)}",
                f"**Entrou em:** {_ts(datetime.now(timezone.utc))}",
            ],
            cor=COR_SUCESSO,
            url_do_avatar=membro.display_avatar.url,
        )

    # ── Moderação ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_ban(self, guilda: discord.Guild, usuario: discord.User):
        if not _servidor_correto(guilda):
            return
        executor = await buscar_executor_no_audit_log(
            guilda, discord.AuditLogAction.ban, alvo_id=usuario.id
        )
        await _publicar(
            guilda,
            "LOG_MODERACAO",
            titulo="🔨 Ban",
            linhas=[
                f"**Membro:** {_mencao(usuario)}",
                f"**Aplicado por:** {_mencao(executor)}",
            ],
            cor=COR_ERRO,
            url_do_avatar=usuario.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guilda: discord.Guild, usuario: discord.User):
        if not _servidor_correto(guilda):
            return
        executor = await buscar_executor_no_audit_log(
            guilda, discord.AuditLogAction.unban, alvo_id=usuario.id
        )
        await _publicar(
            guilda,
            "LOG_MODERACAO",
            titulo="♻️ Unban",
            linhas=[
                f"**Membro:** {_mencao(usuario)}",
                f"**Removido por:** {_mencao(executor)}",
            ],
            cor=COR_SUCESSO,
            url_do_avatar=usuario.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, membro: discord.Member):
        if not _servidor_correto(membro.guild):
            return
        if membro.id in self._sessoes_de_voz:
            self._sessoes_de_voz.pop(membro.id, None)

        fid = await _fid(membro.id)
        await _publicar(
            membro.guild,
            "LOG_MEMBROS",
            titulo="📤 Membro saiu",
            linhas=[
                f"**Membro:** {_mencao(membro)} (`{membro.id}`)",
                f"**FID:** (`{fid}`)",
                f"**Saiu em:** {_ts(datetime.now(timezone.utc))}",
            ],
            cor=COR_AVISO,
            url_do_avatar=membro.display_avatar.url,
        )

        executor = await buscar_executor_no_audit_log(
            membro.guild,
            discord.AuditLogAction.kick,
            alvo_id=membro.id,
            segundos_de_tolerancia=15,
        )
        if executor is None:
            return
        await _publicar(
            membro.guild,
            "LOG_MODERACAO",
            titulo="👢 Kick",
            linhas=[
                f"**Membro:** {_mencao(membro)}",
                f"**Aplicado por:** {_mencao(executor)}",
            ],
            cor=COR_AVISO,
            url_do_avatar=membro.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entrada: discord.AuditLogEntry):
        guilda = entrada.guild
        if guilda is None or not _servidor_correto(guilda):
            return
        acao = entrada.action
        alvo = entrada.target
        executor = entrada.user

        if acao == discord.AuditLogAction.member_update and entrada.changes:
            for mudanca in entrada.changes:
                if getattr(mudanca, "key", None) == "communication_disabled_until":
                    antes_v = mudanca.old
                    depois_v = mudanca.new
                    if depois_v and not antes_v:
                        await _publicar(
                            guilda,
                            "LOG_MODERACAO",
                            titulo="⏳ Timeout aplicado",
                            linhas=[
                                f"**Membro:** {_mencao(alvo)}",
                                f"**Até:** {_ts(depois_v)}",
                                f"**Aplicado por:** {_mencao(executor)}",
                                f"**Motivo:** {entrada.reason or '—'}",
                            ],
                            cor=COR_AVISO,
                        )
                    elif antes_v and not depois_v:
                        await _publicar(
                            guilda,
                            "LOG_MODERACAO",
                            titulo="✅ Timeout removido",
                            linhas=[
                                f"**Membro:** {_mencao(alvo)}",
                                f"**Removido por:** {_mencao(executor)}",
                            ],
                            cor=COR_SUCESSO,
                        )

        if acao in (
            discord.AuditLogAction.role_update,
            discord.AuditLogAction.role_create,
            discord.AuditLogAction.role_delete,
        ):
            mapa = {
                discord.AuditLogAction.role_create: ("🔍 🆕 Cargo criado", COR_SUCESSO),
                discord.AuditLogAction.role_delete: ("🔍 🗑️ Cargo excluído", COR_ERRO),
                discord.AuditLogAction.role_update: (
                    "🔍 ✏️ Cargo / permissões alterados",
                    COR_AVISO,
                ),
            }
            titulo, cor = mapa[acao]
            nome = getattr(alvo, "name", None) or getattr(alvo, "id", "—")
            await _publicar(
                guilda,
                "LOG_MODERACAO",
                titulo=titulo,
                linhas=[
                    f"**Cargo:** `{nome}`",
                    f"**Alterado por:** {_mencao(executor)}",
                    f"**Motivo:** {entrada.reason or '—'}",
                ],
                cor=cor,
            )

    @commands.Cog.listener()
    async def on_ready(self):
        """Confere se os canais de log são alcançáveis após o bot conectar."""
        from src.utils.logger import resolver_canal_de_log

        guilda = None
        try:
            gid = int(GUILD_ID or 0)
            if gid:
                guilda = self.bot.get_guild(gid)
        except (TypeError, ValueError):
            guilda = None
        if guilda is None and self.bot.guilds:
            guilda = self.bot.guilds[0]
        if guilda is None:
            registrador.error("Auditoria: nenhuma guilda disponível no on_ready")
            return

        chaves = [
            "LOG_CANAIS",
            "LOG_MENSAGENS_DELETADAS",
            "LOG_MENSAGENS_EDITADAS",
            "LOG_VOZ",
            "LOG_APELIDOS",
            "LOG_MEMBROS",
            "LOG_AVATARES",
            "LOG_MODERACAO",
            "LOG_HORAS",
            "LOG_CARGOS",
        ]
        for chave in chaves:
            canal = await resolver_canal_de_log(guilda, chave, cliente=self.bot)
            if canal is not None:
                registrador.info(
                    "Auditoria OK %s → #%s (%s)",
                    chave,
                    getattr(canal, "name", "?"),
                    getattr(canal, "id", "?"),
                )
            else:
                registrador.error(
                    "Auditoria FALHOU %s — canal inacessível ou ID 0",
                    chave,
                )


async def setup(bot: commands.Bot) -> None:
    """Registra os ouvintes e informa quais canais de log estão ativos."""
    global _bot_global
    _bot_global = bot
    await bot.add_cog(EventosAuditoriaCog(bot))
    chaves = [
        "LOG_CANAIS",
        "LOG_MENSAGENS_DELETADAS",
        "LOG_MENSAGENS_EDITADAS",
        "LOG_VOZ",
        "LOG_APELIDOS",
        "LOG_MEMBROS",
        "LOG_AVATARES",
        "LOG_MODERACAO",
        "LOG_HORAS",
        "LOG_CARGOS",
    ]
    ativos = []
    desligados = []
    for chave in chaves:
        if obter_id_do_canal_de_log(chave) > 0:
            ativos.append(f"{chave}={CANAIS.get(chave)}")
        else:
            desligados.append(chave)
    registrador.info(
        "EventosAuditoriaCog registrado — ativos: %s | desligados (ID 0): %s",
        ativos or ["nenhum"],
        desligados or ["nenhum"],
    )
