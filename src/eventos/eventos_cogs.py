"""
Ouvintes de auditoria do Discord (domínio eventos).

Cada tipo de evento publica no canal configurado em CANAIS via
``publicar_log_auditoria`` (helper único em utils/logger.py).

LOG_HORAS neste domínio = tempo em call de voz do servidor inteiro
(não é plantão). Plantão continua só em LOG_PLANTAO.

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
from src.utils.formatacao import formatar_hms
from src.utils.logger import (
    COR_AVISO,
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    baixar_arquivo_de_url,
    buscar_executor_no_audit_log,
    cargo_ja_foi_logado_pelo_bot,
    log_mudanca_cargo,
    obter_id_do_canal_de_log,
    publicar_log_auditoria,
)

registrador = logging.getLogger(__name__)


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
        # Controle de sessão de voz para LOG_HORAS (servidor inteiro)
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
        await publicar_log_auditoria(
            canal.guild,
            "LOG_CANAIS",
            titulo="🔍 📁 Canal criado",
            linhas=[
                f"**Canal:** {mencao} (`{canal.id}`)",
                f"**Nome:** `{canal.name}`",
                f"**Tipo:** {tipo}",
                f"**Categoria:** `{canal.category.name if canal.category else '—'}`",
                "",
                f"**Criado por:** {_mencao(executor)}",
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
        linhas = [
            f"**Nome:** {canal.name} (`{canal.id}`)",
            f"**Tipo:** {tipo}",
        ]
        if categoria is not None:
            linhas.append(f"**Categoria:** {categoria.name} **ID:** (`{categoria.id}`)")
        else:
            linhas.append("**Categoria:** —")
        linhas.extend(["", f"**Excluído por:** {_mencao(executor)}"])
        await publicar_log_auditoria(
            canal.guild,
            "LOG_CANAIS",
            titulo="🔍 🗑️ Canal excluído",
            linhas=linhas,
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
                        f"**Antes:** `{antes.name}`",
                        f"**Depois:** `{depois.name}`",
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
                        f"**Antes:** `{nome_antes}`",
                        f"**Depois:** `{nome_depois}`",
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
                f"**Canal:** {getattr(depois, 'mention', depois.name)} (`{depois.id}`)",
                f"**Alteração:** {titulo_alt}",
                *detalhe,
                "",
                f"**Responsável pela alteração:** {_mencao(executor)} **FID:** `{fid_executor}`",
            ]
            await publicar_log_auditoria(
                depois.guild,
                "LOG_CANAIS",
                titulo="🔍 📝 Canal atualizado",
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
        if len(conteudo) > 1500:
            conteudo = conteudo[:1500] + "…"

        linhas = [
            f"**Autor:** {_mencao(mensagem.author)}",
            f"**Canal:** {mensagem.channel.mention}",
            f"**Conteúdo:**\n>>> {conteudo}",
            f"**Enviada em:** {_ts(mensagem.created_at)}",
            f"**Apagada em:** {_ts(datetime.now(timezone.utc))}",
            f"**ID da mensagem:** `{mensagem.id}`",
            f"**Quem apagou:** {_mencao(executor)}",
        ]
        arquivos: list[discord.File] = []
        for anexo in mensagem.attachments[:5]:
            try:
                arquivos.append(await anexo.to_file())
            except (discord.HTTPException, discord.NotFound):
                linhas.append(f"**Anexo (URL):** {anexo.url}")

        await publicar_log_auditoria(
            mensagem.guild,
            "LOG_MENSAGENS_DELETADAS",
            titulo="🔍 🗑️ Mensagem apagada",
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
        if not mensagens:
            return
        guilda = mensagens[0].guild
        if guilda is None or not _servidor_correto(guilda):
            return
        await publicar_log_auditoria(
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

        texto_antes = (antes.content or "").strip() or "_vazio_"
        texto_depois = (depois.content or "").strip() or "_vazio_"
        if len(texto_antes) > 900:
            texto_antes = texto_antes[:900] + "…"
        if len(texto_depois) > 900:
            texto_depois = texto_depois[:900] + "…"

        await publicar_log_auditoria(
            depois.guild,
            "LOG_MENSAGENS_EDITADAS",
            titulo="🔍 ✏️ Mensagem editada",
            linhas=[
                f"**Autor:** {_mencao(depois.author)}",
                f"**Canal:** {depois.channel.mention}",
                f"**Conteúdo anterior:**\n>>> {texto_antes}",
                f"**Conteúdo novo:**\n>>> {texto_depois}",
                f"**ID da mensagem:** `{depois.id}`",
                f"**Link:** [abrir mensagem]({depois.jump_url})",
            ],
            cor=COR_AVISO,
            url_do_avatar=(depois.author.display_avatar.url if depois.author else None),
        )

    # ── Voz + LOG_HORAS (servidor inteiro, não plantão) ───────────────────

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
            await publicar_log_auditoria(
                membro.guild,
                "LOG_VOZ",
                titulo="🔍 🔊 Entrada em canal de voz",
                linhas=[
                    f"**Membro:** {_mencao(membro)}",
                    f"**Canal:** {canal_depois.mention} **ID:** (`{canal_depois.id}`)",
                    f"**Entrada:** {_ts(agora)}",
                ],
                cor=COR_SUCESSO,
                url_do_avatar=membro.display_avatar.url,
            )
            # LOG_HORAS: início de contagem (servidor inteiro)
            fid = await _fid(membro.id)
            await publicar_log_auditoria(
                membro.guild,
                "LOG_HORAS",
                titulo="🔍 🟢 Contagem de horas iniciada",
                linhas=[
                    f"**Membro:** {_mencao(membro)} **FID:** (`{fid}`)",
                    f"**Cargo:** {_cargo_principal(membro)}",
                    f"**Canal:** {canal_depois.mention}",
                    "",
                    f"**Início:** {_ts(agora)}",
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

            await publicar_log_auditoria(
                membro.guild,
                "LOG_VOZ",
                titulo="🔍 🔇 Saída do canal de voz",
                linhas=[
                    f"**Membro:** {_mencao(membro)}",
                    f"**Canal:** {canal_antes.mention} **ID:** (`{canal_antes.id}`)",
                    "",
                    f"**Entrou:** {_ts(entrou_em, 't') if entrou_em else '—'}",
                    f"**Saiu:** {_ts(agora, 't')}",
                    f"**Tempo conectado:** `{formatar_hms(duracao) if duracao else '—'}`",
                ],
                cor=COR_AVISO,
                url_do_avatar=membro.display_avatar.url,
            )

            fid = await _fid(membro.id)
            total = int(self._total_segundos_em_voz.get(membro.id, 0))
            await publicar_log_auditoria(
                membro.guild,
                "LOG_HORAS",
                titulo="🔍 🔴 Horas em call encerrada",
                linhas=[
                    f"**Membro:** {_mencao(membro)} **FID:** (`{fid}`)",
                    "",
                    f"**Início:** {_ts(entrou_em, 't') if entrou_em else '—'}",
                    f"**Fim:** {_ts(agora, 't')}",
                    f"**Duração:** `{formatar_hms(duracao) if duracao else '—'}`",
                    "",
                    f"**Total acumulado (nesta sessão do bot):** `{formatar_hms(total)}`",
                ],
                cor=COR_ERRO,
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
                titulo = "🔍 🔁 Foi movido de call"
                extra = [f"**Movido por:** {_mencao(executor)}"]
            else:
                titulo = "🔍 🔄 Mudança de canal de voz"
                extra = []

            await publicar_log_auditoria(
                membro.guild,
                "LOG_VOZ",
                titulo=titulo,
                linhas=[
                    f"**Membro:** {_mencao(membro)}",
                    f"**De:** {canal_antes.mention} --> **Para:** {canal_depois.mention}",
                    *extra,
                    "",
                    f"**Data:** {_ts(agora)}",
                ],
                cor=COR_INFO,
                url_do_avatar=membro.display_avatar.url,
            )

        # Mute / surdez de servidor / stream
        extras: list[str] = []
        titulo_extra = None
        if antes.mute != depois.mute:
            extras.append(
                f"**Mute servidor:** {'mutado' if depois.mute else 'desmutado'}"
            )
            titulo_extra = "🔍 🔇 Mute de servidor"
        if antes.deaf != depois.deaf:
            extras.append(
                f"**Surdez servidor:** "
                f"{'ensurdecido' if depois.deaf else 'desensurdecido'}"
            )
            titulo_extra = "🔍 🔈 Surdez de servidor"
        if antes.self_stream != depois.self_stream:
            extras.append(
                f"**Transmissão:** {'começou' if depois.self_stream else 'parou'}"
            )
            titulo_extra = "🔍 📺 Transmissão"

        if titulo_extra and extras:
            await publicar_log_auditoria(
                membro.guild,
                "LOG_VOZ",
                titulo=titulo_extra,
                linhas=[f"**Membro:** {_mencao(membro)}", *extras],
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
            await publicar_log_auditoria(
                depois.guild,
                "LOG_APELIDOS",
                titulo="🔍 🪪 Apelido alterado",
                linhas=[
                    f"**Membro:** {_mencao(depois)} **FID:** (`{fid}`)",
                    "",
                    f"**Antes:** `{antes.nick or '—'}`",
                    f"**Depois:** `{depois.nick or '—'}`",
                    "",
                    f"**Alterado por:** {texto_executor}",
                ],
                cor=COR_INFO,
                url_do_avatar=depois.display_avatar.url,
            )

        url_guild_antes = antes.guild_avatar.url if antes.guild_avatar else None
        url_guild_depois = depois.guild_avatar.url if depois.guild_avatar else None
        if url_guild_antes != url_guild_depois:
            await self._log_avatar(
                depois,
                tipo="Avatar do servidor",
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
            url_antiga=antes.display_avatar.url,
            url_nova=depois.display_avatar.url,
        )

    async def _log_avatar(
        self,
        membro: discord.Member,
        *,
        tipo: str,
        url_antiga: str | None,
        url_nova: str | None,
    ) -> None:
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
            titulo="🔍 🖼️ Avatar alterado",
            linhas=[
                f"**Membro:** {_mencao(membro)}",
                f"**Tipo:** {tipo}",
                f"**Avatar atual:** [abrir]({url_nova})"
                if url_nova
                else "**Avatar atual:** —",
            ],
            cor=COR_INFO,
            url_do_avatar=url_nova,
            arquivos=arquivos or None,
            abrir_topico_para_anexos=bool(arquivos),
            nome_do_topico=f"avatar-antigo-{membro.id}"[:100],
        )

    # ── Moderação ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_ban(self, guilda: discord.Guild, usuario: discord.User):
        if not _servidor_correto(guilda):
            return
        executor = await buscar_executor_no_audit_log(
            guilda, discord.AuditLogAction.ban, alvo_id=usuario.id
        )
        await publicar_log_auditoria(
            guilda,
            "LOG_MODERACAO",
            titulo="🔍 🔨 Ban",
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
        await publicar_log_auditoria(
            guilda,
            "LOG_MODERACAO",
            titulo="🔍 ♻️ Unban",
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
        # Encerra sessão de voz se ainda estiver marcada
        if membro.id in self._sessoes_de_voz:
            self._sessoes_de_voz.pop(membro.id, None)
        executor = await buscar_executor_no_audit_log(
            membro.guild,
            discord.AuditLogAction.kick,
            alvo_id=membro.id,
            segundos_de_tolerancia=15,
        )
        if executor is None:
            return
        await publicar_log_auditoria(
            membro.guild,
            "LOG_MODERACAO",
            titulo="🔍 👢 Kick",
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
                        await publicar_log_auditoria(
                            guilda,
                            "LOG_MODERACAO",
                            titulo="🔍 ⏳ Timeout aplicado",
                            linhas=[
                                f"**Membro:** {_mencao(alvo)}",
                                f"**Até:** {_ts(depois_v)}",
                                f"**Aplicado por:** {_mencao(executor)}",
                                f"**Motivo:** {entrada.reason or '—'}",
                            ],
                            cor=COR_AVISO,
                        )
                    elif antes_v and not depois_v:
                        await publicar_log_auditoria(
                            guilda,
                            "LOG_MODERACAO",
                            titulo="🔍 ✅ Timeout removido",
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
            await publicar_log_auditoria(
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


async def setup(bot: commands.Bot) -> None:
    """Registra os ouvintes e informa quais canais de log estão ativos."""
    await bot.add_cog(EventosAuditoriaCog(bot))
    chaves = [
        "LOG_CANAIS",
        "LOG_MENSAGENS_DELETADAS",
        "LOG_MENSAGENS_EDITADAS",
        "LOG_VOZ",
        "LOG_APELIDOS",
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
