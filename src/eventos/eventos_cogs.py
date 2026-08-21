"""
Ouvintes de auditoria do Discord (domínio eventos).

Cada tipo de evento publica no canal configurado em CANAIS via
``publicar_log_auditoria`` (helper único em utils/logger.py).

Intents necessárias (já ligadas no bot): members, message_content, guilds.
Permissão recomendada no cargo do bot: Ver registro de auditoria.
"""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)

import discord
from discord.ext import commands

from src.config import GUILD_ID
from src.utils.logger import (
    COR_AVISO,
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    baixar_arquivo_de_url,
    buscar_executor_no_audit_log,
    cargo_ja_foi_logado_pelo_bot,
    log_mudanca_cargo,
    publicar_log_auditoria,
)

registrador = logging.getLogger(__name__)


def _servidor_correto(guilda: discord.Guild | None) -> bool:
    """Ignora eventos de outros servidores (o bot pode estar em mais de um)."""
    if guilda is None:
        return False
    try:
        return int(guilda.id) == int(GUILD_ID)
    except (TypeError, ValueError):
        return True


def _mencao_usuario(usuario: discord.abc.User | None) -> str:
    if usuario is None:
        return "_desconhecido_"
    return f"{usuario.mention} (`{usuario.id}`)"


def _formato_timestamp(data: datetime | None) -> str:
    if data is None:
        return "—"
    if data.tzinfo is None:
        data = data.replace(tzinfo=timezone.utc)
    return f"<t:{int(data.timestamp())}:F>"


class EventosAuditoriaCog(commands.Cog):
    """
    Centraliza os listeners de auditoria do servidor.

    Cargos: qualquer mudança (manual, bot ou comando) gera log em LOG_CARGOS.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cache simples para não logar a mesma edição duas vezes
        self._ids_de_edicao_recentes: set[int] = set()

    # ── Canais e categorias ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, canal: discord.abc.GuildChannel):
        """1. Canal criado."""
        if not _servidor_correto(canal.guild):
            return
        executor = await buscar_executor_no_audit_log(
            canal.guild,
            discord.AuditLogAction.channel_create,
            alvo_id=canal.id,
        )
        tipo = (
            type(canal).__name__.replace("Channel", "").replace("Category", "Categoria")
        )
        await publicar_log_auditoria(
            canal.guild,
            "LOG_CANAIS",
            titulo="📁 Canal criado",
            linhas=[
                f"- **Canal:** {getattr(canal, 'mention', canal.name)} (`{canal.id}`)",
                f"- **Nome:** `{canal.name}`",
                f"- **Tipo:** `{tipo}`",
                f"- **Categoria:** `{canal.category.name if canal.category else '—'}`",
                f"- **Criado por:** {_mencao_usuario(executor)}",
            ],
            cor=COR_SUCESSO,
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, canal: discord.abc.GuildChannel):
        """2. Canal excluído."""
        if not _servidor_correto(canal.guild):
            return
        executor = await buscar_executor_no_audit_log(
            canal.guild,
            discord.AuditLogAction.channel_delete,
            alvo_id=canal.id,
        )
        await publicar_log_auditoria(
            canal.guild,
            "LOG_CANAIS",
            titulo="🗑️ Canal excluído",
            linhas=[
                f"- **Nome:** `{canal.name}`",
                f"- **ID:** `{canal.id}`",
                f"- **Categoria:** `{canal.category.name if canal.category else '—'}`",
                f"- **Excluído por:** {_mencao_usuario(executor)}",
            ],
            cor=COR_ERRO,
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        antes: discord.abc.GuildChannel,
        depois: discord.abc.GuildChannel,
    ):
        """
        3–9. Renomeado, movido, tópico, slowmode, NSFW, permissões.
        """
        if not _servidor_correto(depois.guild):
            return

        mudancas: list[str] = []

        if antes.name != depois.name:
            mudancas.append(f"- **Nome:** `{antes.name}` → `{depois.name}`")

        categoria_antes = antes.category.id if antes.category else None
        categoria_depois = depois.category.id if depois.category else None
        if categoria_antes != categoria_depois:
            nome_antes = antes.category.name if antes.category else "—"
            nome_depois = depois.category.name if depois.category else "—"
            mudancas.append(f"- **Categoria:** `{nome_antes}` → `{nome_depois}`")

        if isinstance(antes, discord.TextChannel) and isinstance(
            depois, discord.TextChannel
        ):
            if (antes.topic or "") != (depois.topic or ""):
                mudancas.append(
                    f"- **Tópico/descrição:**\n"
                    f"  Antes: {(antes.topic or '—')[:200]}\n"
                    f"  Depois: {(depois.topic or '—')[:200]}"
                )
            if antes.slowmode_delay != depois.slowmode_delay:
                mudancas.append(
                    f"- **Slowmode:** `{antes.slowmode_delay}s` → "
                    f"`{depois.slowmode_delay}s`"
                )
            if antes.nsfw != depois.nsfw:
                estado = "ativado" if depois.nsfw else "desativado"
                mudancas.append(f"- **NSFW:** {estado}")

        # Permissões (overwrites)
        if antes.overwrites != depois.overwrites:
            mudancas.append("- **Permissões do canal:** alteradas")

        if not mudancas:
            return

        executor = await buscar_executor_no_audit_log(
            depois.guild,
            discord.AuditLogAction.channel_update,
            alvo_id=depois.id,
        )
        linhas = [
            f"- **Canal:** {getattr(depois, 'mention', depois.name)} (`{depois.id}`)",
            *mudancas,
            f"- **Alterado por:** {_mencao_usuario(executor)}",
        ]
        await publicar_log_auditoria(
            depois.guild,
            "LOG_CANAIS",
            titulo="✏️ Canal atualizado",
            linhas=linhas,
            cor=COR_AVISO,
        )

    # ── Mensagens deletadas ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_delete(self, mensagem: discord.Message):
        """Log de mensagem apagada (autor, canal, conteúdo, anexos, datas, quem apagou)."""
        if mensagem.guild is None or not _servidor_correto(mensagem.guild):
            return
        if mensagem.author and mensagem.author.bot:
            return

        executor = await buscar_executor_no_audit_log(
            mensagem.guild,
            discord.AuditLogAction.message_delete,
            alvo_id=mensagem.author.id if mensagem.author else None,
        )

        conteudo = (mensagem.content or "").strip()
        if not conteudo:
            conteudo = "_sem texto_"
        if len(conteudo) > 1500:
            conteudo = conteudo[:1500] + "…"

        linhas = [
            f"- **Autor:** {_mencao_usuario(mensagem.author)}",
            f"- **Canal:** {mensagem.channel.mention}",
            f"- **Conteúdo:**\n>>> {conteudo}",
            f"- **Enviada em:** {_formato_timestamp(mensagem.created_at)}",
            f"- **Apagada em:** {_formato_timestamp(datetime.now(timezone.utc))}",
            f"- **ID da mensagem:** `{mensagem.id}`",
            f"- **Quem apagou:** {_mencao_usuario(executor)}",
        ]

        arquivos: list[discord.File] = []
        for anexo in mensagem.attachments[:5]:
            try:
                arquivo = await anexo.to_file()
                arquivos.append(arquivo)
            except (discord.HTTPException, discord.NotFound):
                linhas.append(f"- **Anexo (URL):** {anexo.url}")

        await publicar_log_auditoria(
            mensagem.guild,
            "LOG_MENSAGENS_DELETADAS",
            titulo="🗑️ Mensagem apagada",
            linhas=linhas,
            cor=COR_ERRO,
            url_do_avatar=(
                mensagem.author.display_avatar.url if mensagem.author else None
            ),
            arquivos=arquivos or None,
            abrir_topico_para_anexos=bool(arquivos),
            nome_do_topico=f"anexos-{mensagem.id}"[:100],
        )

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, mensagens: list[discord.Message]):
        """Várias mensagens apagadas de uma vez (purge)."""
        if not mensagens:
            return
        guilda = mensagens[0].guild
        if guilda is None or not _servidor_correto(guilda):
            return
        canal = mensagens[0].channel
        await publicar_log_auditoria(
            guilda,
            "LOG_MENSAGENS_DELETADAS",
            titulo="🗑️ Mensagens apagadas em massa",
            linhas=[
                f"- **Canal:** {canal.mention}",
                f"- **Quantidade:** **{len(mensagens)}**",
                f"- **Quando:** {_formato_timestamp(datetime.now(timezone.utc))}",
            ],
            cor=COR_ERRO,
        )

    # ── Mensagens editadas ───────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_edit(
        self,
        antes: discord.Message,
        depois: discord.Message,
    ):
        """
        Log de edição.

        Ignora:
        - mensagem de bot
        - quando só mudou embed automático (conteúdo igual)
        """
        if depois.guild is None or not _servidor_correto(depois.guild):
            return
        if depois.author and depois.author.bot:
            return

        conteudo_antes = antes.content or ""
        conteudo_depois = depois.content or ""
        if conteudo_antes == conteudo_depois:
            # Só embed/anexo/componente mudou — não loga
            return

        if depois.id in self._ids_de_edicao_recentes:
            return
        self._ids_de_edicao_recentes.add(depois.id)
        if len(self._ids_de_edicao_recentes) > 200:
            self._ids_de_edicao_recentes.clear()

        texto_antes = conteudo_antes.strip() or "_vazio_"
        texto_depois = conteudo_depois.strip() or "_vazio_"
        if len(texto_antes) > 900:
            texto_antes = texto_antes[:900] + "…"
        if len(texto_depois) > 900:
            texto_depois = texto_depois[:900] + "…"

        link = depois.jump_url
        await publicar_log_auditoria(
            depois.guild,
            "LOG_MENSAGENS_EDITADAS",
            titulo="✏️ Mensagem editada",
            linhas=[
                f"- **Autor:** {_mencao_usuario(depois.author)}",
                f"- **Canal:** {depois.channel.mention}",
                f"- **Conteúdo anterior:**\n>>> {texto_antes}",
                f"- **Conteúdo novo:**\n>>> {texto_depois}",
                f"- **ID da mensagem:** `{depois.id}`",
                f"- **Link:** [abrir mensagem]({link})",
            ],
            cor=COR_AVISO,
            url_do_avatar=(depois.author.display_avatar.url if depois.author else None),
        )

    # ── Voz ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        membro: discord.Member,
        antes: discord.VoiceState,
        depois: discord.VoiceState,
    ):
        """Entrada, saída, troca, move por staff, mute/surdez de servidor, stream."""
        if not _servidor_correto(membro.guild):
            return
        if membro.bot:
            return

        linhas_base = [f"- **Membro:** {_mencao_usuario(membro)}"]
        titulo = None
        cor = COR_INFO
        extras: list[str] = []

        canal_antes = antes.channel
        canal_depois = depois.channel

        if canal_antes is None and canal_depois is not None:
            titulo = "🔊 Entrou em call"
            cor = COR_SUCESSO
            extras.append(f"- **Canal:** {canal_depois.mention}")
        elif canal_antes is not None and canal_depois is None:
            titulo = "🔇 Saiu da call"
            cor = COR_AVISO
            extras.append(f"- **Canal:** {canal_antes.mention}")
        elif (
            canal_antes is not None
            and canal_depois is not None
            and canal_antes.id != canal_depois.id
        ):
            # Pode ter sido movido por staff
            executor = await buscar_executor_no_audit_log(
                membro.guild,
                discord.AuditLogAction.member_move,
                alvo_id=membro.id,
            )
            if executor is not None:
                titulo = "🔁 Foi movido de call"
                extras.append(f"- **De:** {canal_antes.mention}")
                extras.append(f"- **Para:** {canal_depois.mention}")
                extras.append(f"- **Movido por:** {_mencao_usuario(executor)}")
            else:
                titulo = "🔄 Mudou de canal de voz"
                extras.append(f"- **De:** {canal_antes.mention}")
                extras.append(f"- **Para:** {canal_depois.mention}")
            cor = COR_INFO

        # Mute / surdez de servidor
        if antes.mute != depois.mute:
            estado = "mutado" if depois.mute else "desmutado"
            extras.append(f"- **Servidor mutou/desmutou:** {estado}")
            if titulo is None:
                titulo = "🔇 Mute de servidor"
        if antes.deaf != depois.deaf:
            estado = "ensurdecido" if depois.deaf else "desensurdecido"
            extras.append(f"- **Servidor ensurdeceu/desensurdeceu:** {estado}")
            if titulo is None:
                titulo = "🔈 Surdez de servidor"

        # Transmissão (stream)
        if antes.self_stream != depois.self_stream:
            estado = "começou" if depois.self_stream else "parou"
            extras.append(f"- **Transmissão:** {estado}")
            if titulo is None:
                titulo = "📺 Transmissão"
                cor = COR_INFO

        if titulo is None:
            return

        await publicar_log_auditoria(
            membro.guild,
            "LOG_VOZ",
            titulo=titulo,
            linhas=linhas_base + extras,
            cor=cor,
            url_do_avatar=membro.display_avatar.url,
        )

    # ── Apelidos, avatares e cargos ──────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(
        self,
        antes: discord.Member,
        depois: discord.Member,
    ):
        """Apelido, avatar do servidor e cargos (qualquer origem)."""
        if not _servidor_correto(depois.guild):
            return

        # Apelido
        if antes.nick != depois.nick:
            await self._log_apelido(antes, depois)

        # Avatar do servidor (display_avatar pode mudar com guild avatar)
        avatar_guild_antes = antes.guild_avatar
        avatar_guild_depois = depois.guild_avatar
        url_antes = avatar_guild_antes.url if avatar_guild_antes else None
        url_depois = avatar_guild_depois.url if avatar_guild_depois else None
        if url_antes != url_depois:
            await self._log_avatar(
                depois,
                tipo="Avatar do servidor",
                url_antiga=url_antes,
                url_nova=url_depois or depois.display_avatar.url,
            )

        # Cargos — expande para TODO o servidor (manual, bot ou comando)
        ids_antes = {cargo.id for cargo in antes.roles}
        ids_depois = {cargo.id for cargo in depois.roles}
        if ids_antes != ids_depois:
            await self._log_cargos(antes, depois, ids_antes, ids_depois)

    @commands.Cog.listener()
    async def on_user_update(self, antes: discord.User, depois: discord.User):
        """Avatar global (User)."""
        if antes.avatar == depois.avatar:
            return
        # Propaga para o membro na guild principal, se existir
        guilda = self.bot.get_guild(int(GUILD_ID)) if GUILD_ID else None
        if guilda is None:
            return
        membro = guilda.get_member(depois.id)
        if membro is None:
            return
        url_antiga = antes.display_avatar.url
        url_nova = depois.display_avatar.url
        await self._log_avatar(
            membro,
            tipo="Avatar global",
            url_antiga=url_antiga,
            url_nova=url_nova,
        )

    async def _log_apelido(
        self,
        antes: discord.Member,
        depois: discord.Member,
    ) -> None:
        """Registra mudança de apelido e tenta achar quem alterou."""
        executor = await buscar_executor_no_audit_log(
            depois.guild,
            discord.AuditLogAction.member_update,
            alvo_id=depois.id,
        )
        if executor is None or executor.id == depois.id:
            texto_executor = "Próprio usuário"
        else:
            texto_executor = _mencao_usuario(executor)

        await publicar_log_auditoria(
            depois.guild,
            "LOG_APELIDOS",
            titulo="🏷️ Apelido alterado",
            linhas=[
                f"- **Membro:** {_mencao_usuario(depois)}",
                f"- **Antes:** `{antes.nick or '—'}`",
                f"- **Depois:** `{depois.nick or '—'}`",
                f"- **Alterado por:** {texto_executor}",
            ],
            cor=COR_INFO,
            url_do_avatar=depois.display_avatar.url,
        )

    async def _log_avatar(
        self,
        membro: discord.Member,
        *,
        tipo: str,
        url_antiga: str | None,
        url_nova: str | None,
    ) -> None:
        """
        Mostra avatar atual no card e anexa a imagem antiga em tópico.
        """
        arquivos: list[discord.File] = []
        if url_antiga:
            arquivo = await baixar_arquivo_de_url(
                self.bot,
                url_antiga,
                f"avatar_antigo_{membro.id}.png",
            )
            if arquivo is not None:
                arquivos.append(arquivo)

        await publicar_log_auditoria(
            membro.guild,
            "LOG_AVATARES",
            titulo="🖼️ Avatar alterado",
            linhas=[
                f"- **Membro:** {_mencao_usuario(membro)}",
                f"- **Tipo:** {tipo}",
                f"- **Avatar atual:** [abrir]({url_nova})"
                if url_nova
                else "- **Avatar atual:** —",
            ],
            cor=COR_INFO,
            url_do_avatar=url_nova,
            arquivos=arquivos or None,
            abrir_topico_para_anexos=bool(arquivos),
            nome_do_topico=f"avatar-antigo-{membro.id}"[:100],
        )

    async def _log_cargos(
        self,
        antes: discord.Member,
        depois: discord.Member,
        ids_antes: set[int],
        ids_depois: set[int],
    ) -> None:
        """
        Qualquer alteração de cargo no servidor (manual, bot ou slash).

        Usa o audit log para preencher “Alterado por”.
        Debounce de poucos segundos evita card duplicado quando um serviço
        do bot já chamou ``log_mudanca_cargo`` e o Discord dispara o evento.
        """
        # Se um serviço do bot já publicou o card (log_mudanca_cargo), não repete
        if cargo_ja_foi_logado_pelo_bot(depois.id):
            return

        adicionados_ids = ids_depois - ids_antes
        removidos_ids = ids_antes - ids_depois

        nomes_adicionados = [
            cargo.mention
            for cargo in depois.roles
            if cargo.id in adicionados_ids and not cargo.is_default()
        ]
        nomes_removidos = [
            cargo.name
            for cargo in antes.roles
            if cargo.id in removidos_ids and not cargo.is_default()
        ]

        if not nomes_adicionados and not nomes_removidos:
            return

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
            cargos_adicionados=nomes_adicionados or None,
            cargos_removidos=nomes_removidos or None,
        )

    # ── Moderação (audit log periódico seria pesado; usamos eventos nativos) ─

    @commands.Cog.listener()
    async def on_member_ban(self, guilda: discord.Guild, usuario: discord.User):
        """Ban."""
        if not _servidor_correto(guilda):
            return
        executor = await buscar_executor_no_audit_log(
            guilda,
            discord.AuditLogAction.ban,
            alvo_id=usuario.id,
        )
        await publicar_log_auditoria(
            guilda,
            "LOG_MODERACAO",
            titulo="🔨 Ban",
            linhas=[
                f"- **Membro:** {_mencao_usuario(usuario)}",
                f"- **Aplicado por:** {_mencao_usuario(executor)}",
            ],
            cor=COR_ERRO,
            url_do_avatar=usuario.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guilda: discord.Guild, usuario: discord.User):
        """Unban."""
        if not _servidor_correto(guilda):
            return
        executor = await buscar_executor_no_audit_log(
            guilda,
            discord.AuditLogAction.unban,
            alvo_id=usuario.id,
        )
        await publicar_log_auditoria(
            guilda,
            "LOG_MODERACAO",
            titulo="♻️ Unban",
            linhas=[
                f"- **Membro:** {_mencao_usuario(usuario)}",
                f"- **Removido por:** {_mencao_usuario(executor)}",
            ],
            cor=COR_SUCESSO,
            url_do_avatar=usuario.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, membro: discord.Member):
        """Kick (se houver entrada recente de kick no audit log)."""
        if not _servidor_correto(membro.guild):
            return
        executor = await buscar_executor_no_audit_log(
            membro.guild,
            discord.AuditLogAction.kick,
            alvo_id=membro.id,
            segundos_de_tolerancia=15,
        )
        if executor is None:
            # Saída voluntária — não é moderação
            return
        await publicar_log_auditoria(
            membro.guild,
            "LOG_MODERACAO",
            titulo="👢 Kick",
            linhas=[
                f"- **Membro:** {_mencao_usuario(membro)}",
                f"- **Aplicado por:** {_mencao_usuario(executor)}",
            ],
            cor=COR_AVISO,
            url_do_avatar=membro.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entrada: discord.AuditLogEntry):
        """
        Captura timeout, remoção de timeout e alterações finas de moderação
        quando o Discord emite a entrada de audit log.
        """
        guilda = entrada.guild
        if guilda is None or not _servidor_correto(guilda):
            return

        acao = entrada.action
        alvo = entrada.target
        executor = entrada.user

        # Timeout / remoção de timeout
        if acao == discord.AuditLogAction.member_update and entrada.changes:
            for mudanca in entrada.changes:
                if getattr(mudanca, "key", None) == "communication_disabled_until":
                    antes_v = mudanca.old
                    depois_v = mudanca.new
                    if depois_v and not antes_v:
                        await publicar_log_auditoria(
                            guilda,
                            "LOG_MODERACAO",
                            titulo="⏳ Timeout aplicado",
                            linhas=[
                                f"- **Membro:** {_mencao_usuario(alvo)}",
                                f"- **Até:** {_formato_timestamp(depois_v)}",
                                f"- **Aplicado por:** {_mencao_usuario(executor)}",
                                f"- **Motivo:** {entrada.reason or '—'}",
                            ],
                            cor=COR_AVISO,
                        )
                    elif antes_v and not depois_v:
                        await publicar_log_auditoria(
                            guilda,
                            "LOG_MODERACAO",
                            titulo="✅ Timeout removido",
                            linhas=[
                                f"- **Membro:** {_mencao_usuario(alvo)}",
                                f"- **Removido por:** {_mencao_usuario(executor)}",
                            ],
                            cor=COR_SUCESSO,
                        )

        # Mudança de permissões de cargo no servidor
        if acao in (
            discord.AuditLogAction.role_update,
            discord.AuditLogAction.role_create,
            discord.AuditLogAction.role_delete,
        ):
            mapa = {
                discord.AuditLogAction.role_create: ("🆕 Cargo criado", COR_SUCESSO),
                discord.AuditLogAction.role_delete: ("🗑️ Cargo excluído", COR_ERRO),
                discord.AuditLogAction.role_update: (
                    "✏️ Cargo / permissões alterados",
                    COR_AVISO,
                ),
            }
            titulo, cor = mapa[acao]
            nome = getattr(alvo, "name", None) or getattr(alvo, "id", "—")
            await publicar_log_auditoria(
                guilda,
                "LOG_MODERACAO",
                titulo=titulo,
                linhas=[
                    f"- **Cargo:** `{nome}`",
                    f"- **Alterado por:** {_mencao_usuario(executor)}",
                    f"- **Motivo:** {entrada.reason or '—'}",
                ],
                cor=cor,
            )


async def setup(bot: commands.Bot) -> None:
    """Registra os ouvintes de auditoria do servidor."""
    await bot.add_cog(EventosAuditoriaCog(bot))
    registrador.info("EventosAuditoriaCog registrado (logs de auditoria do servidor)")
