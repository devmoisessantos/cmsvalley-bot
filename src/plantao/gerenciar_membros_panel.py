"""Painel #gerenciar-membros — Diretoria++: ficha do membro + ações admin + coordenação."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from sqlalchemy import func, select
from src.plantao.permissoes import e_diretoria, mensagem_sem_permissao

from src.config import NOMES_CANAIS_PLANTAO, VALOR_MOEDA_INGAME
from src.database.connection import async_session
from src.database.models import (
    EstadoPlantao,
    FaltaChamada,
    LogPlantao,
    Recrutamento,
    Usuario,
)
from src.plantao.auditoria import registrar_auditoria_admin
from src.plantao.chamada.chamada_panel import PainelCoordenacaoView
from src.plantao.plantao_service import desligar_servico, garantir_aware
from src.utils.error_handling import LoggingViewMixin
from src.utils.formatacao import formatar_dinheiro, formatar_hms

# ── Helpers de dados ─────────────────────────────────────────────────────


async def _buscar_estado(discord_id: int) -> EstadoPlantao | None:
    async with async_session() as session:
        r = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        return r.scalar_one_or_none()


async def _tempo_total_segundos(discord_id: int) -> int:
    async with async_session() as session:
        r = await session.execute(
            select(func.coalesce(func.sum(LogPlantao.duracao_segundos), 0)).where(
                LogPlantao.discord_id == discord_id,
                LogPlantao.duracao_segundos.is_not(None),
            )
        )
        return int(r.scalar_one() or 0)


async def _ultimo_recrutamento(discord_id: int) -> Recrutamento | None:
    async with async_session() as session:
        r = await session.execute(
            select(Recrutamento)
            .where(Recrutamento.discord_id_candidato == discord_id)
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        return r.scalar_one_or_none()


async def _contagem_faltas(discord_id: int) -> int:
    async with async_session() as session:
        r = await session.execute(
            select(func.count())
            .select_from(FaltaChamada)
            .where(FaltaChamada.discord_id == discord_id)
        )
        return int(r.scalar_one() or 0)


async def _ultimos_logs(discord_id: int, limite: int = 8) -> list[LogPlantao]:
    async with async_session() as session:
        r = await session.execute(
            select(LogPlantao)
            .where(LogPlantao.discord_id == discord_id)
            .order_by(LogPlantao.criado_em.desc())
            .limit(limite)
        )
        return list(r.scalars().all())


async def montar_texto_ficha(
    membro: discord.Member,
    estado: EstadoPlantao | None,
) -> str:
    online = bool(estado and estado.toggle_ligado)
    saldo = estado.saldo_moedas if estado else 0
    id_fivem = (estado.id_fivem if estado else None) or "—"

    # ciclo atual
    if online and estado and estado.em_call_valida and estado.call_entrada_em:
        entrada = garantir_aware(estado.call_entrada_em)
        ciclo = int((datetime.now(timezone.utc) - entrada).total_seconds())
        nome_call = NOMES_CANAIS_PLANTAO.get(estado.canal_atual_id, "Desconhecida")
        linha_plantao = (
            f"🟢 **Em serviço** · ciclo `{formatar_hms(ciclo)}` · call **{nome_call}**"
        )
    elif online:
        linha_plantao = "🟢 **Em serviço** · aguardando call"
    else:
        linha_plantao = "🔴 **Fora de serviço**"

    tempo_total = await _tempo_total_segundos(membro.id)
    faltas = await _contagem_faltas(membro.id)
    rec = await _ultimo_recrutamento(membro.id)

    cargos = (
        ", ".join(
            r.mention
            for r in sorted(membro.roles, key=lambda x: x.position, reverse=True)
            if r.name != "@everyone"
        )[:400]
        or "—"
    )

    entrou = f"<t:{int(membro.joined_at.timestamp())}:f>" if membro.joined_at else "—"

    if rec:
        data_rec = (
            f"<t:{int(rec.data_fim.timestamp())}:d>"
            if rec.data_fim
            else (
                f"<t:{int(rec.data_inicio.timestamp())}:d>" if rec.data_inicio else "—"
            )
        )
        bloco_rec = (
            f"**Status:** `{rec.status}` · **Cargo final:** `{rec.cargo_final or '—'}`\n"
            f"**Recrutador:** <@{rec.discord_id_recrutador}> · **Data:** {data_rec}\n"
            f"**ID FiveM (rec):** `{rec.id_fivem or '—'}`"
        )
    else:
        bloco_rec = "_Nenhum recrutamento registrado._"

    logs = await _ultimos_logs(membro.id)
    if logs:
        linhas_log = []
        for log in logs:
            ts = f"<t:{int(log.criado_em.timestamp())}:R>" if log.criado_em else ""
            linhas_log.append(f"`{log.evento}` {ts}")
        bloco_log = "\n".join(linhas_log)
    else:
        bloco_log = "_Sem eventos recentes._"

    return (
        f"# 👤 Ficha — {membro.display_name}\n"
        f"{membro.mention} · `{membro.id}`\n\n"
        f"## Identidade\n"
        f"**Nick:** {membro.display_name}\n"
        f"**ID FiveM:** `{id_fivem}`\n"
        f"**Entrou no servidor:** {entrou}\n"
        f"**Cargos:** {cargos}\n\n"
        f"## Recrutamento\n{bloco_rec}\n\n"
        f"## Plantão\n"
        f"{linha_plantao}\n"
        f"**Saldo:** {saldo} moedas ({formatar_dinheiro(saldo * VALOR_MOEDA_INGAME)})\n"
        f"**Tempo total (logs):** `{formatar_hms(tempo_total)}`\n"
        f"**Segundos acumulados (ciclo):** `{estado.segundos_acumulados if estado else 0}`\n\n"
        f"## Chamadas\n"
        f"**Faltas registradas:** `{faltas}`\n\n"
        f"## Histórico recente\n{bloco_log}"
    )


# ── Views ────────────────────────────────────────────────────────────────


class PainelGerenciarMembrosLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente no canal #gerenciar-membros."""

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
                    "Consulte a ficha completa de um membro, force ações de plantão "
                    "e acesse o modo de coordenação de chamadas.\n\n"
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
                mensagem_sem_permissao(
                    "acessar o gerenciamento de membros (Diretoria++)"
                ),
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
        row = discord.ui.ActionRow()
        select = discord.ui.UserSelect(
            placeholder="Selecione o membro…",
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        row.add_item(select)

        row2 = discord.ui.ActionRow()
        botao_coord = discord.ui.Button(
            label="🧭 Modo Coordenação (chamada)",
            style=discord.ButtonStyle.secondary,
        )
        botao_coord.callback = self._on_coordenacao
        row2.add_item(botao_coord)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay(
                "# 🔍 Buscar membro\n"
                "Selecione um membro para abrir a ficha, ou entre no modo coordenação."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            row,
            row2,
            accent_color=discord.Color.dark_gold(),
        )
        self.add_item(self.container)

    async def _on_select(self, interaction: discord.Interaction):
        if not e_diretoria(interaction.user):
            await interaction.response.send_message(
                mensagem_sem_permissao("ver fichas"), ephemeral=True
            )
            return
        values = interaction.data.get("resolved", {}).get("users", {})
        if not values:
            # fallback UserSelect values
            user_ids = interaction.data.get("values", [])
            if not user_ids:
                await interaction.response.send_message(
                    "❌ Nenhum membro.", ephemeral=True
                )
                return
            membro = interaction.guild.get_member(int(user_ids[0]))
        else:
            uid = int(next(iter(values.keys())))
            membro = interaction.guild.get_member(uid)

        if membro is None:
            await interaction.response.send_message(
                "❌ Membro não encontrado no servidor.", ephemeral=True
            )
            return

        estado = await _buscar_estado(membro.id)
        texto = await montar_texto_ficha(membro, estado)
        await interaction.response.edit_message(
            view=FichaMembroAdminView(membro, estado, texto)
        )

    async def _on_coordenacao(self, interaction: discord.Interaction):
        if not e_diretoria(interaction.user):
            await interaction.response.send_message(
                mensagem_sem_permissao("modo coordenação"), ephemeral=True
            )
            return
        view = await PainelCoordenacaoView.construir(interaction.user)
        await interaction.response.edit_message(view=view)


class FichaMembroAdminView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        alvo: discord.Member,
        estado: EstadoPlantao | None,
        texto_ficha: str,
    ):
        super().__init__(timeout=300)
        self.alvo = alvo
        self.estado = estado

        avatar = alvo.display_avatar.url
        row = discord.ui.ActionRow()

        b_off = discord.ui.Button(
            label="Forçar desligar", style=discord.ButtonStyle.danger
        )
        b_off.callback = self._forcar_desligar
        row.add_item(b_off)

        b_zero = discord.ui.Button(
            label="Zerar ciclo", style=discord.ButtonStyle.secondary
        )
        b_zero.callback = self._zerar_ciclo
        row.add_item(b_zero)

        row2 = discord.ui.ActionRow()
        b_moeda = discord.ui.Button(
            label="Ajustar moedas", style=discord.ButtonStyle.primary
        )
        b_moeda.callback = self._abrir_modal_moedas
        row2.add_item(b_moeda)

        b_edit = discord.ui.Button(
            label="Editar ID FiveM", style=discord.ButtonStyle.secondary
        )
        b_edit.callback = self._abrir_modal_fivem
        row2.add_item(b_edit)

        row3 = discord.ui.ActionRow()
        b_voltar = discord.ui.Button(
            label="↩️ Nova busca", style=discord.ButtonStyle.secondary
        )
        b_voltar.callback = self._voltar
        row3.add_item(b_voltar)

        self.container = discord.ui.Container(
            discord.ui.Section(
                texto_ficha[:3500],
                accessory=discord.ui.Thumbnail(avatar),
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row,
            row2,
            row3,
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
        texto = await montar_texto_ficha(self.alvo, estado)
        await interaction.edit_original_response(
            view=FichaMembroAdminView(self.alvo, estado, texto)
        )

    async def _forcar_desligar(self, interaction: discord.Interaction):
        if not await self._garantir_perm(interaction):
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

    async def _voltar(self, interaction: discord.Interaction):
        if not await self._garantir_perm(interaction):
            return
        await interaction.response.edit_message(view=SeletorMembroAdminView())


class ModalAjustarMoedas(discord.ui.Modal, title="Ajustar moedas"):
    valor = discord.ui.TextInput(
        label="Novo saldo (número inteiro ≥ 0)",
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
        texto = await montar_texto_ficha(self.alvo, estado)
        await interaction.edit_original_response(
            view=FichaMembroAdminView(self.alvo, estado, texto)
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
            if usuario:
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
        texto = await montar_texto_ficha(self.alvo, estado)
        await interaction.edit_original_response(
            view=FichaMembroAdminView(self.alvo, estado, texto)
        )
