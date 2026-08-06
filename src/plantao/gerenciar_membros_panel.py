"""Painel #gerenciar-membros — Diretoria++: consulta completa no banco + ações admin.

Sem modo coordenação / chamada. Ficha alimentada por:
usuarios, recrutamentos, estado_plantao, log_plantao, faltas_chamada, chamadas
+ dados da API do Discord (cargos, joined_at, avatar).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from sqlalchemy import func, select

from src.config import NOMES_CANAIS_PLANTAO, VALOR_MOEDA_INGAME
from src.database.connection import async_session
from src.database.models import (
    Chamada,
    EstadoPlantao,
    FaltaChamada,
    LogPlantao,
    Recrutamento,
    Usuario,
)
from src.plantao.auditoria import registrar_auditoria_admin
from src.plantao.permissoes import e_diretoria, mensagem_sem_permissao
from src.plantao.plantao_service import desligar_servico, garantir_aware
from src.utils.error_handling import LoggingViewMixin
from src.utils.formatacao import formatar_dinheiro, formatar_hms

# ── Consultas ────────────────────────────────────────────────────────────


async def _resolver_id_fivem(discord_id: int) -> str | None:
    """Prioridade: EstadoPlantao → Usuario → último Recrutamento APROVADO."""
    async with async_session() as session:
        r = await session.execute(
            select(EstadoPlantao.id_fivem).where(
                EstadoPlantao.discord_id == discord_id,
                EstadoPlantao.id_fivem.is_not(None),
            )
        )
        v = r.scalar_one_or_none()
        if v:
            return str(v)

        r = await session.execute(
            select(Usuario.id_fivem).where(
                Usuario.discord_id == discord_id,
                Usuario.id_fivem.is_not(None),
            )
        )
        v = r.scalar_one_or_none()
        if v:
            return str(v)

        r = await session.execute(
            select(Recrutamento.id_fivem)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.status == "APROVADO",
                Recrutamento.id_fivem.is_not(None),
            )
            .order_by(Recrutamento.data_fim.desc().nullslast(), Recrutamento.id.desc())
            .limit(1)
        )
        v = r.scalar_one_or_none()
        return str(v) if v else None


async def _buscar_estado(discord_id: int) -> EstadoPlantao | None:
    async with async_session() as session:
        r = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        return r.scalar_one_or_none()


async def _buscar_usuario(discord_id: int) -> Usuario | None:
    async with async_session() as session:
        r = await session.execute(
            select(Usuario).where(Usuario.discord_id == discord_id)
        )
        return r.scalar_one_or_none()


async def _recrutamento_como_candidato(discord_id: int) -> Recrutamento | None:
    async with async_session() as session:
        r = await session.execute(
            select(Recrutamento)
            .where(Recrutamento.discord_id_candidato == discord_id)
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        return r.scalar_one_or_none()


async def _stats_como_recrutador(
    discord_id: int,
) -> tuple[int, int, list[Recrutamento]]:
    """Retorna (total APROVADO, total última semana, últimos 5 APROVADO)."""
    agora = datetime.now(timezone.utc)
    semana = agora - timedelta(days=7)
    async with async_session() as session:
        total = await session.execute(
            select(func.count())
            .select_from(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
            )
        )
        total_n = int(total.scalar_one() or 0)

        sem = await session.execute(
            select(func.count())
            .select_from(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
                Recrutamento.data_fim.is_not(None),
                Recrutamento.data_fim >= semana,
            )
        )
        sem_n = int(sem.scalar_one() or 0)

        ultimos = await session.execute(
            select(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
            )
            .order_by(Recrutamento.data_fim.desc().nullslast(), Recrutamento.id.desc())
            .limit(5)
        )
        lista = list(ultimos.scalars().all())
    return total_n, sem_n, lista


async def _stats_chamadas(discord_id: int) -> tuple[int, int, int]:
    """(faltas, chamadas_como_doutor, presentes_aproximado via falta invertida não)."""
    async with async_session() as session:
        faltas = await session.execute(
            select(func.count())
            .select_from(FaltaChamada)
            .where(FaltaChamada.discord_id == discord_id)
        )
        faltas_n = int(faltas.scalar_one() or 0)

        como_doutor = await session.execute(
            select(func.count())
            .select_from(Chamada)
            .where(Chamada.doutor_id == discord_id)
        )
        doutor_n = int(como_doutor.scalar_one() or 0)

    return faltas_n, doutor_n


async def _tempo_total_segundos(discord_id: int) -> int:
    async with async_session() as session:
        r = await session.execute(
            select(func.coalesce(func.sum(LogPlantao.duracao_segundos), 0)).where(
                LogPlantao.discord_id == discord_id,
                LogPlantao.duracao_segundos.is_not(None),
            )
        )
        return int(r.scalar_one() or 0)


async def _ultimos_logs(discord_id: int, limite: int = 6) -> list[LogPlantao]:
    async with async_session() as session:
        r = await session.execute(
            select(LogPlantao)
            .where(LogPlantao.discord_id == discord_id)
            .order_by(LogPlantao.criado_em.desc())
            .limit(limite)
        )
        return list(r.scalars().all())


# ── Formatação ───────────────────────────────────────────────────────────


def _formatar_cargos(membro: discord.Member) -> str:
    roles = [
        r
        for r in sorted(membro.roles, key=lambda x: x.position, reverse=True)
        if r.name != "@everyone"
    ]
    if not roles:
        return "_Nenhum cargo._"
    mencoes = [r.mention for r in roles]
    linhas = []
    for i in range(0, len(mencoes), 3):
        linhas.append(" · ".join(mencoes[i : i + 3]))
    return "\n".join(linhas)


def _ts(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:d>"


async def montar_blocos_ficha(
    membro: discord.Member,
    estado: EstadoPlantao | None,
) -> list[str]:
    """Retorna lista de blocos de texto (seções) para montar com Separators."""
    usuario = await _buscar_usuario(membro.id)
    id_fivem = await _resolver_id_fivem(membro.id)
    id_fivem_txt = f"`{id_fivem}`" if id_fivem else "`—`"

    status_db = usuario.status if usuario else "—"
    nick_db = (
        usuario.nickname_atual if usuario and usuario.nickname_atual else None
    ) or membro.display_name
    ja_aprovado = "sim" if (usuario and usuario.ja_foi_aprovado) else "não"

    entrou = f"<t:{int(membro.joined_at.timestamp())}:f>" if membro.joined_at else "—"

    # ── Identidade ──
    bloco_id = (
        f"# 👤 {membro.display_name}\n"
        f"{membro.mention} · `{membro.id}`\n\n"
        f"## Identidade\n"
        f"**Nick (Discord):** {membro.display_name}\n"
        f"**Nick (DB):** {nick_db}\n"
        f"**ID FiveM:** {id_fivem_txt}\n"
        f"**Status (DB):** `{status_db}` · **Já aprovado:** `{ja_aprovado}`\n"
        f"**Entrou no servidor:** {entrou}\n"
        f"**Cargos:**\n{_formatar_cargos(membro)}"
    )

    # ── Como candidato ──
    rec_cand = await _recrutamento_como_candidato(membro.id)
    if rec_cand:
        bloco_cand = (
            f"## Recrutamento (como candidato)\n"
            f"**Status:** `{rec_cand.status}` · **Cargo final:** `{rec_cand.cargo_final or '—'}`\n"
            f"**Recrutador:** <@{rec_cand.discord_id_recrutador}>\n"
            f"**Início:** {_ts(rec_cand.data_inicio)} · **Fim:** {_ts(rec_cand.data_fim)}\n"
            f"**Nota:** `{rec_cand.nota_percentual if rec_cand.nota_percentual is not None else '—'}` "
            f"· **Acertos:** `{rec_cand.acertos if rec_cand.acertos is not None else '—'}`\n"
            f"**ID FiveM (rec):** `{rec_cand.id_fivem or '—'}`"
        )
    else:
        bloco_cand = "## Recrutamento (como candidato)\n_Nenhum registro._"

    # ── Como recrutador ──
    total_rec, sem_rec, ultimos = await _stats_como_recrutador(membro.id)
    if total_rec == 0:
        bloco_rec_feitos = (
            "## Recrutamentos feitos\n"
            "**Total de Recrutamentos:** `0`\n"
            "**Total na última semana:** `0`"
        )
    else:
        linhas_ult = []
        for r in ultimos:
            fid = r.id_fivem or "?"
            linhas_ult.append(f"`{fid}` — <@{r.discord_id_candidato}>")
        resto = total_rec - len(ultimos)
        extra = f"\n... e mais **{resto}**." if resto > 0 else ""
        bloco_rec_feitos = (
            "## Recrutamentos feitos\n"
            f"**Total de Recrutamentos:** `{total_rec}`\n"
            f"**Total na última semana:** `{sem_rec}`\n\n"
            + "\n".join(linhas_ult)
            + extra
        )

    # ── Plantão ──
    online = bool(estado and estado.toggle_ligado)
    saldo = estado.saldo_moedas if estado else 0
    segs = estado.segundos_acumulados if estado else 0
    tempo_total = await _tempo_total_segundos(membro.id)

    if online and estado and estado.em_call_valida and estado.call_entrada_em:
        entrada = garantir_aware(estado.call_entrada_em)
        ciclo = int((datetime.now(timezone.utc) - entrada).total_seconds())
        nome_call = NOMES_CANAIS_PLANTAO.get(estado.canal_atual_id, "Desconhecida")
        linha_st = (
            f"🟢 **Em serviço** · nesta call `{formatar_hms(ciclo)}` · **{nome_call}**"
        )
    elif online:
        linha_st = "🟢 **Em serviço** · aguardando call"
    else:
        linha_st = "🔴 **Fora de serviço**"

    bloco_plantao = (
        f"## Plantão\n"
        f"{linha_st}\n"
        f"**Saldo moedas:** `{saldo}` ({formatar_dinheiro(saldo * VALOR_MOEDA_INGAME)})\n"
        f"**Segundos do ciclo:** `{segs}`\n"
        f"**Tempo total (logs):** `{formatar_hms(tempo_total)}`\n"
        f"**ID FiveM (plantão):** `{(estado.id_fivem if estado and estado.id_fivem else '—')}`"
    )

    # ── Chamadas ──
    faltas_n, doutor_n = await _stats_chamadas(membro.id)
    bloco_chamadas = (
        f"## Chamadas\n"
        f"**Faltas registradas:** `{faltas_n}`\n"
        f"**Chamadas realizadas (como doutor):** `{doutor_n}`"
    )

    # ── Histórico ──
    logs = await _ultimos_logs(membro.id)
    if logs:
        linhas_log = []
        for log in logs:
            ts = f"<t:{int(log.criado_em.timestamp())}:R>" if log.criado_em else ""
            linhas_log.append(f"`{log.evento}` {ts}")
        bloco_hist = "## Histórico recente (plantão)\n" + "\n".join(linhas_log)
    else:
        bloco_hist = "## Histórico recente (plantão)\n_Sem eventos._"

    return [
        bloco_id,
        bloco_cand,
        bloco_rec_feitos,
        bloco_plantao,
        bloco_chamadas,
        bloco_hist,
    ]


# ── Painel persistente ───────────────────────────────────────────────────


class PainelGerenciarMembrosLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild
        icon_url = guild.icon.url if guild.icon else None

        row = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Gerenciar Membros",
            style=discord.ButtonStyle.primary,
            custom_id="admin:gerenciar_membros",
            emoji="🛡️",
        )
        botao.callback = self._callback_abrir
        row.add_item(botao)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# 🛡️ Gerenciar Membros"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.Section(
                "> **Somente Diretoria++**",
                (
                    "Consulte a ficha completa de um membro no banco de dados "
                    "e execute ações administrativas.\n\n"
                    "Todas as ações são registradas em auditoria."
                ),
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row,
            accent_color=discord.Color.dark_gold(),
        )
        self.add_item(self.container)

    async def _callback_abrir(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Use em um servidor.", ephemeral=True
            )
            return
        if not e_diretoria(interaction.user):
            await interaction.response.send_message(
                mensagem_sem_permissao("acessar o gerenciamento de membros"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            view=SeletorMembroAdminView(),
            ephemeral=True,
        )


class SeletorMembroAdminView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=300)

        row_user = discord.ui.ActionRow()
        select = discord.ui.UserSelect(
            placeholder="Selecione o membro…",
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        row_user.add_item(select)

        row_id = discord.ui.ActionRow()
        botao_id = discord.ui.Button(
            label="Buscar membro por Discord ID",
            style=discord.ButtonStyle.secondary,
            emoji="🔍",
        )
        botao_id.callback = self._on_buscar_id
        row_id.add_item(botao_id)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay(
                "# 🔍 Buscar membro\nSelecione no menu **ou** busque pelo Discord ID."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row_user,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            row_id,
            accent_color=discord.Color.dark_gold(),
        )
        self.add_item(self.container)

    async def _on_select(self, interaction: discord.Interaction):
        if not e_diretoria(interaction.user):
            await interaction.response.send_message(
                mensagem_sem_permissao("ver fichas"), ephemeral=True
            )
            return
        user_ids = interaction.data.get("values", [])
        if not user_ids:
            await interaction.response.send_message("❌ Nenhum membro.", ephemeral=True)
            return
        membro = interaction.guild.get_member(int(user_ids[0]))
        if membro is None:
            await interaction.response.send_message(
                "❌ Membro não está no servidor (use busca por ID).", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await _abrir_ficha(interaction, membro)

    async def _on_buscar_id(self, interaction: discord.Interaction):
        if not e_diretoria(interaction.user):
            await interaction.response.send_message(
                mensagem_sem_permissao("buscar membros"), ephemeral=True
            )
            return
        await interaction.response.send_modal(ModalBuscarDiscordId())


class ModalBuscarDiscordId(discord.ui.Modal, title="Buscar por Discord ID"):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID",
        placeholder="Ex: 547220861896097822",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not e_diretoria(interaction.user):
            await interaction.response.send_message(
                mensagem_sem_permissao("buscar membros"), ephemeral=True
            )
            return
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message(
                "❌ Discord ID inválido (somente números).", ephemeral=True
            )
            return
        discord_id = int(raw)
        await interaction.response.defer(ephemeral=True)

        membro = interaction.guild.get_member(discord_id) if interaction.guild else None
        if membro is None:
            # Ainda assim tenta montar ficha só com banco (sem Member completo)
            try:
                user = await interaction.client.fetch_user(discord_id)
            except discord.NotFound:
                await interaction.followup.send(
                    f"❌ Nenhum usuário com ID `{discord_id}`.", ephemeral=True
                )
                return
            # Member fake limitado — cargos só se estiver no guild
            await interaction.followup.send(
                f"⚠️ `<@{discord_id}>` não está no servidor. "
                "Mostrando dados do banco + usuário Discord.",
                ephemeral=True,
            )

            # Usa um proxy: se não for Member, cargos vazios
            class _Proxy:
                def __init__(self, u):
                    self.id = u.id
                    self.display_name = u.display_name
                    self.mention = u.mention
                    self.display_avatar = u.display_avatar
                    self.joined_at = None
                    self.roles = []
                    self.guild = interaction.guild

            membro = _Proxy(user)  # type: ignore

        await _abrir_ficha(interaction, membro)


async def _abrir_ficha(interaction: discord.Interaction, membro: discord.Member):
    estado = await _buscar_estado(membro.id)
    blocos = await montar_blocos_ficha(membro, estado)
    view = FichaMembroAdminView(membro, estado, blocos)
    if interaction.response.is_done():
        await interaction.edit_original_response(view=view)
    else:
        await interaction.response.edit_message(view=view)


# ── Ficha + ações ────────────────────────────────────────────────────────


class FichaMembroAdminView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        alvo: discord.Member,
        estado: EstadoPlantao | None,
        blocos: list[str],
    ):
        super().__init__(timeout=360)
        self.alvo = alvo
        self.estado = estado

        avatar = getattr(alvo, "display_avatar", None)
        avatar_url = avatar.url if avatar else None

        componentes: list = []
        # Primeiro bloco com thumbnail
        if avatar_url:
            componentes.append(
                discord.ui.Section(
                    blocos[0], accessory=discord.ui.Thumbnail(avatar_url)
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(blocos[0]))

        for bloco in blocos[1:]:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
            )
            componentes.append(discord.ui.TextDisplay(bloco))

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # 4 botões por linha
        row1 = discord.ui.ActionRow()
        b1 = discord.ui.Button(
            label="Forçar desligar", style=discord.ButtonStyle.danger, emoji="🔴"
        )
        b1.callback = self._forcar_desligar
        row1.add_item(b1)

        b2 = discord.ui.Button(
            label="Zerar ciclo", style=discord.ButtonStyle.secondary, emoji="⏱️"
        )
        b2.callback = self._zerar_ciclo
        row1.add_item(b2)

        b3 = discord.ui.Button(
            label="Ajustar moedas", style=discord.ButtonStyle.primary, emoji="💰"
        )
        b3.callback = self._abrir_modal_moedas
        row1.add_item(b3)

        b4 = discord.ui.Button(
            label="Editar ID FiveM", style=discord.ButtonStyle.primary, emoji="🪪"
        )
        b4.callback = self._abrir_modal_fivem
        row1.add_item(b4)

        row2 = discord.ui.ActionRow()
        b5 = discord.ui.Button(
            label="Editar status DB", style=discord.ButtonStyle.secondary, emoji="📝"
        )
        b5.callback = self._abrir_modal_status
        row2.add_item(b5)

        b6 = discord.ui.Button(
            label="Nova busca", style=discord.ButtonStyle.secondary, emoji="↩️"
        )
        b6.callback = self._voltar
        row2.add_item(b6)

        componentes.append(row1)
        componentes.append(row2)

        self.container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.dark_gold(),
        )
        self.add_item(self.container)

    async def _garantir_perm(self, interaction: discord.Interaction) -> bool:
        if not e_diretoria(interaction.user):
            await interaction.response.send_message(
                mensagem_sem_permissao("ações admin"), ephemeral=True
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction):
        estado = await _buscar_estado(self.alvo.id)
        blocos = await montar_blocos_ficha(self.alvo, estado)
        await interaction.edit_original_response(
            view=FichaMembroAdminView(self.alvo, estado, blocos)
        )

    async def _forcar_desligar(self, interaction: discord.Interaction):
        if not await self._garantir_perm(interaction):
            return
        if not isinstance(self.alvo, discord.Member):
            await interaction.response.send_message(
                "❌ Alvo precisa estar no servidor para forçar desligar.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        resultado = await desligar_servico(self.alvo)
        await registrar_auditoria_admin(
            interaction.guild,
            executor=interaction.user,
            alvo=self.alvo,
            acao="FORCAR_DESLIGAR_SERVICO",
            detalhes=resultado,
            cor=discord.Color.red(),
        )
        await self._refresh(interaction)

    async def _zerar_ciclo(self, interaction: discord.Interaction):
        if not await self._garantir_perm(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        async with async_session() as session:
            r = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == self.alvo.id)
            )
            estado = r.scalar_one_or_none()
            if estado:
                estado.segundos_acumulados = 0
                estado.segmento_iniciado_em = (
                    datetime.now(timezone.utc)
                    if estado.toggle_ligado and estado.em_call_valida
                    else None
                )
                await session.commit()
        await registrar_auditoria_admin(
            interaction.guild,
            executor=interaction.user,
            alvo=self.alvo,
            acao="ZERAR_CICLO",
            detalhes="segundos_acumulados = 0",
        )
        await self._refresh(interaction)

    async def _abrir_modal_moedas(self, interaction: discord.Interaction):
        if not await self._garantir_perm(interaction):
            return
        await interaction.response.send_modal(ModalAjustarMoedas(self.alvo))

    async def _abrir_modal_fivem(self, interaction: discord.Interaction):
        if not await self._garantir_perm(interaction):
            return
        await interaction.response.send_modal(ModalEditarFivem(self.alvo))

    async def _abrir_modal_status(self, interaction: discord.Interaction):
        if not await self._garantir_perm(interaction):
            return
        await interaction.response.send_modal(ModalEditarStatus(self.alvo))

    async def _voltar(self, interaction: discord.Interaction):
        if not await self._garantir_perm(interaction):
            return
        await interaction.response.edit_message(view=SeletorMembroAdminView())


class ModalAjustarMoedas(discord.ui.Modal, title="Ajustar moedas"):
    valor = discord.ui.TextInput(
        label="Novo saldo (inteiro ≥ 0)",
        placeholder="Ex: 12",
        required=True,
        max_length=6,
    )

    def __init__(self, alvo: discord.Member):
        super().__init__()
        self.alvo = alvo

    async def on_submit(self, interaction: discord.Interaction):
        if not e_diretoria(interaction.user):
            await interaction.response.send_message(
                mensagem_sem_permissao("ajustar moedas"), ephemeral=True
            )
            return
        raw = self.valor.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message(
                "❌ Valor inválido.", ephemeral=True
            )
            return
        novo = int(raw)
        await interaction.response.defer(ephemeral=True)
        antigo = 0
        async with async_session() as session:
            r = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == self.alvo.id)
            )
            estado = r.scalar_one_or_none()
            if estado is None:
                estado = EstadoPlantao(discord_id=self.alvo.id)
                session.add(estado)
            antigo = estado.saldo_moedas
            estado.saldo_moedas = novo
            await session.commit()

        await registrar_auditoria_admin(
            interaction.guild,
            executor=interaction.user,
            alvo=self.alvo,
            acao="AJUSTAR_MOEDAS",
            detalhes=f"{antigo} → {novo}",
            cor=discord.Color.green(),
        )
        estado = await _buscar_estado(self.alvo.id)
        blocos = await montar_blocos_ficha(self.alvo, estado)
        await interaction.edit_original_response(
            view=FichaMembroAdminView(self.alvo, estado, blocos)
        )


class ModalEditarFivem(discord.ui.Modal, title="Editar ID FiveM"):
    id_fivem = discord.ui.TextInput(
        label="ID FiveM",
        placeholder="Ex: 54623",
        required=True,
        max_length=6,
    )

    def __init__(self, alvo: discord.Member):
        super().__init__()
        self.alvo = alvo

    async def on_submit(self, interaction: discord.Interaction):
        if not e_diretoria(interaction.user):
            await interaction.response.send_message(
                mensagem_sem_permissao("editar ID FiveM"), ephemeral=True
            )
            return
        valor = self.id_fivem.value.strip()
        if not valor.isdigit():
            await interaction.response.send_message("❌ ID inválido.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        antigo = None
        async with async_session() as session:
            r = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == self.alvo.id)
            )
            estado = r.scalar_one_or_none()
            if estado is None:
                estado = EstadoPlantao(discord_id=self.alvo.id)
                session.add(estado)
            antigo = estado.id_fivem
            estado.id_fivem = valor

            r2 = await session.execute(
                select(Usuario).where(Usuario.discord_id == self.alvo.id)
            )
            usuario = r2.scalar_one_or_none()
            if usuario is None:
                usuario = Usuario(discord_id=self.alvo.id, id_fivem=valor)
                session.add(usuario)
            else:
                usuario.id_fivem = valor
            await session.commit()

        await registrar_auditoria_admin(
            interaction.guild,
            executor=interaction.user,
            alvo=self.alvo,
            acao="EDITAR_ID_FIVEM",
            detalhes=f"{antigo} → {valor}",
        )
        estado = await _buscar_estado(self.alvo.id)
        blocos = await montar_blocos_ficha(self.alvo, estado)
        await interaction.edit_original_response(
            view=FichaMembroAdminView(self.alvo, estado, blocos)
        )


class ModalEditarStatus(discord.ui.Modal, title="Editar status (tabela usuarios)"):
    status_input = discord.ui.TextInput(
        label="Status",
        placeholder="VISITANTE | ESTUDANTE | APROVADO | …",
        required=True,
        max_length=30,
    )
    nick_input = discord.ui.TextInput(
        label="Nickname DB (opcional)",
        required=False,
        max_length=100,
    )

    def __init__(self, alvo: discord.Member):
        super().__init__()
        self.alvo = alvo

    async def on_submit(self, interaction: discord.Interaction):
        if not e_diretoria(interaction.user):
            await interaction.response.send_message(
                mensagem_sem_permissao("editar status"), ephemeral=True
            )
            return
        status = self.status_input.value.strip().upper()
        nick = self.nick_input.value.strip() or None
        await interaction.response.defer(ephemeral=True)

        async with async_session() as session:
            r = await session.execute(
                select(Usuario).where(Usuario.discord_id == self.alvo.id)
            )
            usuario = r.scalar_one_or_none()
            if usuario is None:
                usuario = Usuario(discord_id=self.alvo.id)
                session.add(usuario)
            antigo = usuario.status
            usuario.status = status
            if nick:
                usuario.nickname_atual = nick
            if status == "APROVADO":
                usuario.ja_foi_aprovado = True
            await session.commit()

        await registrar_auditoria_admin(
            interaction.guild,
            executor=interaction.user,
            alvo=self.alvo,
            acao="EDITAR_STATUS_USUARIO",
            detalhes=f"status {antigo} → {status}"
            + (f" · nick={nick}" if nick else ""),
        )
        estado = await _buscar_estado(self.alvo.id)
        blocos = await montar_blocos_ficha(self.alvo, estado)
        await interaction.edit_original_response(
            view=FichaMembroAdminView(self.alvo, estado, blocos)
        )
