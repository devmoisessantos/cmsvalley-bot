"""Painel #gerenciar-membros — Diretoria++: ficha com blocos expansíveis + ações admin.

Resumo compacto + select para expandir um bloco por vez (a mensagem é editada).
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select

from src.bau.bau_service import (
    formatar_bloco_itens_yaml,
    ler_itens_do_caso,
)
from src.config import (
    NOMES_CANAIS_PLANTAO,
    VALOR_MOEDA_INGAME,
)
from src.database.connection import async_session
from src.database.models import (
    EstadoPlantao,
    Usuario,
)
from src.membros.cargos_panel import GerenciarCargosView
from src.membros.membros_service import (
    buscar_estado_plantao,
    buscar_recrutamento_como_candidato,
    buscar_usuario,
    contagens_resumo_ficha,
    estatisticas_chamadas,
    estatisticas_como_recrutador,
    formatar_cargos_do_membro,
    formatar_timestamp,
    formatar_timestamp_relativo,
    listar_casos_bau_membro,
    listar_historico_cargos,
    listar_laudos_como_paciente,
    listar_laudos_como_psicologo,
    listar_punicoes,
    listar_verbais_bau,
    resolver_discord_id_por_fivem,
    resolver_id_fivem_do_membro,
    tempo_total_segundos_plantao,
    ultimos_logs_plantao,
)
from src.plantao.auditoria import registrar_auditoria_admin
from src.plantao.permissoes import (
    e_diretoria,
    mensagem_sem_permissao,
)
from src.plantao.plantao_service import (
    desligar_servico,
    garantir_aware,
)
from src.punicoes.helpers import resolver_id_fivem
from src.punicoes.services import executar_exoneracao
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
)
from src.utils.formatacao import (
    formatar_dinheiro,
    formatar_hms,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)

# ── Identificadores dos blocos expansíveis ───────────────────────────────

BLOCO_RESUMO = "resumo"
BLOCO_IDENTIDADE = "identidade"
BLOCO_RECRUTAMENTO = "recrutamento"
BLOCO_RECRUTADOR = "recrutador"
BLOCO_PLANTAO = "plantao"
BLOCO_CHAMADAS = "chamadas"
BLOCO_HIST_PLANTAO = "hist_plantao"
BLOCO_PUNICOES = "punicoes"
BLOCO_BAU = "bau"
BLOCO_CARGOS_HIST = "hist_cargos"
BLOCO_LAUDOS = "laudos"

OPCOES_BLOCOS = [
    (BLOCO_RESUMO, "📋 Resumo"),
    (BLOCO_IDENTIDADE, "👤 Identidade"),
    (BLOCO_RECRUTAMENTO, "📝 Recrutamento (candidato)"),
    (BLOCO_RECRUTADOR, "✈️ Recrutamentos feitos"),
    (BLOCO_PLANTAO, "⏱️ Plantão"),
    (BLOCO_CHAMADAS, "🚑 Chamadas"),
    (BLOCO_HIST_PLANTAO, "📜 Histórico plantão"),
    (BLOCO_PUNICOES, "⚠️ Punições"),
    (BLOCO_BAU, "📦 Baú"),
    (BLOCO_CARGOS_HIST, "🏷️ Histórico de cargos"),
    (BLOCO_LAUDOS, "🧠 Laudos"),
]


async def _montar_texto_bloco(
    chave: str,
    membro: discord.Member,
    estado: EstadoPlantao | None,
) -> str:
    """Gera o texto de um bloco específico da ficha."""
    if chave == BLOCO_RESUMO:
        return await _texto_resumo(membro, estado)
    if chave == BLOCO_IDENTIDADE:
        return await _texto_identidade(membro)
    if chave == BLOCO_RECRUTAMENTO:
        return await _texto_recrutamento_candidato(membro)
    if chave == BLOCO_RECRUTADOR:
        return await _texto_recrutador(membro)
    if chave == BLOCO_PLANTAO:
        return await _texto_plantao(membro, estado)
    if chave == BLOCO_CHAMADAS:
        return await _texto_chamadas(membro)
    if chave == BLOCO_HIST_PLANTAO:
        return await _texto_hist_plantao(membro)
    if chave == BLOCO_PUNICOES:
        return await _texto_punicoes(membro)
    if chave == BLOCO_BAU:
        return await _texto_bau(membro)
    if chave == BLOCO_CARGOS_HIST:
        return await _texto_hist_cargos(membro)
    if chave == BLOCO_LAUDOS:
        return await _texto_laudos(membro)
    return "_Bloco desconhecido._"


async def _texto_resumo(membro: discord.Member, estado: EstadoPlantao | None) -> str:
    id_fivem = await resolver_id_fivem_do_membro(membro.id)
    contagens = await contagens_resumo_ficha(membro.id, id_fivem)
    online = bool(estado and estado.toggle_ligado)
    status_plantao = "🟢 em serviço" if online else "🔴 fora de serviço"
    saldo = estado.saldo_moedas if estado else 0

    return (
        f"## 📋 Resumo\n"
        f"**Plantão:** {status_plantao} · **Moedas:** `{saldo}`\n"
        f"**FiveM:** `{id_fivem or '—'}`\n\n"
        f"**Punições ativas:** `{contagens['punicoes_ativas']}`\n"
        f"**Casos baú abertos:** `{contagens['casos_bau_abertos']}` · "
        f"**Verbais baú:** `{contagens['verbais_bau']}`\n"
        f"**Faltas chamada:** `{contagens['faltas_chamada']}` · "
        f"**Chamadas (doutor):** `{contagens['chamadas_doutor']}`\n"
        f"**Recrutamentos feitos:** `{contagens['recrutamentos']}`\n"
        f"**Laudos (paciente):** `{contagens['laudos_paciente']}` · "
        f"**Laudos (psicólogo):** `{contagens['laudos_psicologo']}`\n"
        f"**Movimentações de cargo:** `{contagens['historico_cargos']}`\n\n"
        f"_Use o menu **Expandir bloco** para ver detalhes._"
    )


async def _texto_identidade(membro: discord.Member) -> str:
    usuario = await buscar_usuario(membro.id)
    id_fivem = await resolver_id_fivem_do_membro(membro.id)
    status_db = usuario.status if usuario else "—"
    nick_db = (
        usuario.nickname_atual if usuario and usuario.nickname_atual else None
    ) or membro.display_name
    ja_aprovado = "sim" if (usuario and usuario.ja_foi_aprovado) else "não"
    ultima_rep = formatar_timestamp(usuario.data_ultima_reprovacao if usuario else None)
    entrou = (
        f"<t:{int(membro.joined_at.timestamp())}:f>"
        if getattr(membro, "joined_at", None)
        else "—"
    )
    return (
        f"## 👤 Identidade\n"
        f"**Nick (Discord):** {membro.display_name}\n"
        f"**Nick (DB):** {nick_db}\n"
        f"**ID FiveM:** `{id_fivem or '—'}`\n"
        f"**Status (DB):** `{status_db}` · **Já aprovado:** `{ja_aprovado}`\n"
        f"**Última reprovação:** {ultima_rep}\n"
        f"**Entrou no servidor:** {entrou}\n"
        f"**Cargos:**\n{formatar_cargos_do_membro(membro)}"
    )


async def _texto_recrutamento_candidato(membro: discord.Member) -> str:
    rec_cand = await buscar_recrutamento_como_candidato(membro.id)
    if not rec_cand:
        return "## 📝 Recrutamento (como candidato)\n_Nenhum registro._"
    return (
        f"## 📝 Recrutamento (como candidato)\n"
        f"**Status:** `{rec_cand.status}` · **Cargo final:** `{rec_cand.cargo_final or '—'}`\n"
        f"**Recrutador:** <@{rec_cand.discord_id_recrutador}>\n"
        f"**Início:** {formatar_timestamp(rec_cand.data_inicio)} · "
        f"**Fim:** {formatar_timestamp(rec_cand.data_fim)}\n"
        f"**Nota:** `{rec_cand.nota_percentual if rec_cand.nota_percentual is not None else '—'}` "
        f"· **Acertos:** `{rec_cand.acertos if rec_cand.acertos is not None else '—'}`\n"
        f"**ID FiveM (rec):** `{rec_cand.id_fivem or '—'}`"
    )


async def _texto_recrutador(membro: discord.Member) -> str:
    total_rec, sem_rec, ultimos = await estatisticas_como_recrutador(membro.id)
    if total_rec == 0:
        return "## ✈️ Recrutamentos feitos\n**Total:** `0` · **Última semana:** `0`"
    linhas_ult = []
    for r in ultimos:
        fid = r.id_fivem or "?"
        linhas_ult.append(
            f"`{fid}` — <@{r.discord_id_candidato}> · {formatar_timestamp(r.data_fim)}"
        )
    resto = total_rec - len(ultimos)
    extra = f"\n... e mais **{resto}**." if resto > 0 else ""
    return (
        f"## ✈️ Recrutamentos feitos\n"
        f"**Total:** `{total_rec}` · **Última semana:** `{sem_rec}`\n\n"
        + "\n".join(linhas_ult)
        + extra
    )


async def _texto_plantao(membro: discord.Member, estado: EstadoPlantao | None) -> str:
    online = bool(estado and estado.toggle_ligado)
    saldo = estado.saldo_moedas if estado else 0
    segs = estado.segundos_acumulados if estado else 0
    tempo_total = await tempo_total_segundos_plantao(membro.id)

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

    return (
        f"## ⏱️ Plantão\n"
        f"{linha_st}\n"
        f"**Saldo moedas:** `{saldo}` ({formatar_dinheiro(saldo * VALOR_MOEDA_INGAME)})\n"
        f"**Segundos do ciclo:** `{segs}`\n"
        f"**Tempo total (logs):** `{formatar_hms(tempo_total)}`\n"
        f"**ID FiveM (plantão):** `{(estado.id_fivem if estado and estado.id_fivem else '—')}`"
    )


async def _texto_chamadas(membro: discord.Member) -> str:
    faltas_n, doutor_n = await estatisticas_chamadas(membro.id)
    return (
        f"## 🚑 Chamadas\n"
        f"**Faltas registradas:** `{faltas_n}`\n"
        f"**Chamadas realizadas (como doutor):** `{doutor_n}`"
    )


async def _texto_hist_plantao(membro: discord.Member) -> str:
    logs = await ultimos_logs_plantao(membro.id, limite=10)
    if not logs:
        return "## 📜 Histórico recente (plantão)\n_Sem eventos._"
    linhas_log = []
    for log in logs:
        ts = formatar_timestamp_relativo(log.criado_em)
        linhas_log.append(f"`{log.evento}` {ts}")
    return "## 📜 Histórico recente (plantão)\n" + "\n".join(linhas_log)


async def _texto_punicoes(membro: discord.Member) -> str:
    ativas = await listar_punicoes(membro.id, so_ativas=True, limite=8)
    recentes = await listar_punicoes(membro.id, so_ativas=None, limite=8)
    linhas = ["## ⚠️ Punições / Advertências\n"]
    if ativas:
        linhas.append("**Ativas:**")
        for p in ativas:
            linhas.append(
                f"• `#{p.id}` **{p.cargo_nome}** · {formatar_timestamp(p.criada_em)}\n"
                f"  _{p.motivo[:120]}{'…' if len(p.motivo) > 120 else ''}_ · por <@{p.executor_id}>"
            )
    else:
        linhas.append("**Ativas:** nenhuma")

    inativas = [p for p in recentes if not p.ativa][:5]
    if inativas:
        linhas.append("\n**Histórico (removidas / antigas):**")
        for p in inativas:
            linhas.append(
                f"• `#{p.id}` ~~{p.cargo_nome}~~ · {formatar_timestamp(p.criada_em)}"
            )
    return "\n".join(linhas)


async def _texto_bau(membro: discord.Member) -> str:
    id_fivem = await resolver_id_fivem_do_membro(membro.id)
    casos = await listar_casos_bau_membro(
        discord_id=membro.id, id_fivem=id_fivem, so_abertos=True, limite=5
    )
    verbais = await listar_verbais_bau(
        discord_id=membro.id, id_fivem=id_fivem, limite=6
    )
    linhas = ["## 📦 Baú\n"]
    if casos:
        linhas.append("**Casos abertos:**")
        for caso in casos:
            mapa = ler_itens_do_caso(caso)
            grav = "🔴 GRAVE" if caso.e_grave else "🟠"
            linhas.append(
                f"• Caso `#{caso.id}` · `{caso.status}` · {grav}\n"
                f"{formatar_bloco_itens_yaml(mapa)}"
            )
    else:
        linhas.append("**Casos abertos:** nenhum")

    if verbais:
        linhas.append("\n**Advertências verbais:**")
        for v in verbais:
            linhas.append(
                f"• `{v.tipo}` · {formatar_timestamp(v.criada_em)} · "
                f"_{v.motivo[:80]}{'…' if len(v.motivo) > 80 else ''}_"
            )
    else:
        linhas.append("\n**Advertências verbais:** nenhuma")
    return "\n".join(linhas)


async def _texto_hist_cargos(membro: discord.Member) -> str:
    historico = await listar_historico_cargos(membro.id, limite=12)
    if not historico:
        return "## 🏷️ Histórico de cargos\n_Nenhuma movimentação registrada._"
    linhas = ["## 🏷️ Histórico de cargos\n"]
    for h in historico:
        simbolo = "➕" if h.acao == "ADICIONADO" else "➖"
        linhas.append(
            f"{simbolo} **{h.cargo}** · {h.acao} · "
            f"{formatar_timestamp_relativo(h.data_hora)} · por <@{h.executor_id}>"
        )
    return "\n".join(linhas)


async def _texto_laudos(membro: discord.Member) -> str:
    como_pac = await listar_laudos_como_paciente(membro.id, limite=6)
    como_psi = await listar_laudos_como_psicologo(membro.id, limite=6)
    linhas = ["## 🧠 Laudos psicológicos\n"]

    if como_pac:
        linhas.append("**Como paciente:**")
        for laudo in como_pac:
            linhas.append(
                f"• `#{laudo.id}` **{laudo.parecer}** · "
                f"{formatar_timestamp(laudo.criado_em)} · "
                f"psi <@{laudo.discord_id_psicologo}>\n"
                f"  _{laudo.motivo[:100]}{'…' if len(laudo.motivo) > 100 else ''}_"
            )
    else:
        linhas.append("**Como paciente:** nenhum")

    if como_psi:
        linhas.append("\n**Como psicólogo:**")
        for laudo in como_psi:
            linhas.append(
                f"• `#{laudo.id}` **{laudo.parecer}** · "
                f"paciente <@{laudo.discord_id_paciente}> · "
                f"{formatar_timestamp(laudo.criado_em)}"
            )
    else:
        linhas.append("\n**Como psicólogo:** nenhum")

    return "\n".join(linhas)


def _cabecalho_ficha(membro: discord.Member) -> str:
    return f"# 👤 {membro.display_name}\n{membro.mention} · `{membro.id}`"


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

    async def _callback_abrir(self, interacao: discord.Interaction):
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use este painel dentro de um servidor."],
            )
            return
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("acessar o gerenciamento de membros")],
            )
            return
        await interacao.response.send_message(
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
            label="Buscar por Discord ID",
            style=discord.ButtonStyle.secondary,
            emoji="🔍",
        )
        botao_id.callback = self._on_buscar_discord
        row_id.add_item(botao_id)

        botao_fivem = discord.ui.Button(
            label="Buscar por ID FiveM",
            style=discord.ButtonStyle.secondary,
            emoji="🪪",
        )
        botao_fivem.callback = self._on_buscar_fivem
        row_id.add_item(botao_fivem)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay(
                "# 🔍 Buscar membro\n"
                "Selecione no menu **ou** busque por Discord ID / ID FiveM."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row_user,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            row_id,
            accent_color=discord.Color.dark_gold(),
        )
        self.add_item(self.container)

    async def _on_select(self, interacao: discord.Interaction):
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("ver fichas")],
            )
            return
        user_ids = interacao.data.get("values", [])
        if not user_ids:
            await responder_erro(
                interacao, titulo="Seleção", linhas=["Nenhum membro selecionado."]
            )
            return
        membro = interacao.guild.get_member(int(user_ids[0]))
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Membro ausente",
                linhas=["Membro não está no servidor. Use busca por ID."],
            )
            return
        await interacao.response.defer(ephemeral=True)
        await _abrir_ficha(interacao, membro)

    async def _on_buscar_discord(self, interacao: discord.Interaction):
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("buscar membros")],
            )
            return
        await interacao.response.send_modal(ModalBuscarDiscordId())

    async def _on_buscar_fivem(self, interacao: discord.Interaction):
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("buscar membros")],
            )
            return
        await interacao.response.send_modal(ModalBuscarFivemId())


class ModalBuscarDiscordId(discord.ui.Modal, title="Buscar por Discord ID"):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID",
        placeholder="Ex: 547220861896097822",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interacao: discord.Interaction):
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("buscar membros")],
            )
            return
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await responder_erro(
                interacao,
                titulo="ID inválido",
                linhas=["Informe apenas números."],
            )
            return
        discord_id = int(raw)
        await interacao.response.defer(ephemeral=True)
        membro = interacao.guild.get_member(discord_id) if interacao.guild else None
        if membro is None:
            try:
                user = await interacao.client.fetch_user(discord_id)
            except discord.NotFound:
                await responder_erro(
                    interacao,
                    titulo="Não encontrado",
                    linhas=[f"Nenhum usuário com ID `{discord_id}`."],
                )
                return
            await responder_aviso(
                interacao,
                titulo="Fora do servidor",
                linhas=[
                    f"<@{discord_id}> não está no servidor.",
                    "Mostrando dados do banco + usuário Discord.",
                ],
                delay=8,
            )

            class _Proxy:
                def __init__(self, u):
                    self.id = u.id
                    self.display_name = u.display_name
                    self.mention = u.mention
                    self.display_avatar = u.display_avatar
                    self.joined_at = None
                    self.roles = []
                    self.guild = interacao.guild

            membro = _Proxy(user)  # type: ignore

        await _abrir_ficha(interacao, membro)


class ModalBuscarFivemId(discord.ui.Modal, title="Buscar por ID FiveM"):
    id_fivem_input = discord.ui.TextInput(
        label="ID FiveM (passaporte)",
        placeholder="Ex: 54623",
        required=True,
        max_length=12,
    )

    async def on_submit(self, interacao: discord.Interaction):
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("buscar membros")],
            )
            return
        valor = self.id_fivem_input.value.strip()
        if not valor.isdigit():
            await responder_erro(
                interacao,
                titulo="ID inválido",
                linhas=["Informe apenas números do passaporte."],
            )
            return
        await interacao.response.defer(ephemeral=True)
        discord_id = await resolver_discord_id_por_fivem(valor)
        if discord_id is None:
            await responder_erro(
                interacao,
                titulo="Não encontrado",
                linhas=[f"Nenhum vínculo Discord para FiveM `{valor}`."],
            )
            return
        membro = interacao.guild.get_member(discord_id) if interacao.guild else None
        if membro is None:
            try:
                user = await interacao.client.fetch_user(discord_id)
            except discord.NotFound:
                await responder_erro(
                    interacao,
                    titulo="Usuário inválido",
                    linhas=[f"Discord `{discord_id}` não existe mais."],
                )
                return

            class _Proxy:
                def __init__(self, u):
                    self.id = u.id
                    self.display_name = u.display_name
                    self.mention = u.mention
                    self.display_avatar = u.display_avatar
                    self.joined_at = None
                    self.roles = []
                    self.guild = interacao.guild

            membro = _Proxy(user)  # type: ignore
            await responder_aviso(
                interacao,
                titulo="Fora do servidor",
                linhas=[f"FiveM `{valor}` → <@{discord_id}> (não está no servidor)."],
                delay=8,
            )

        await _abrir_ficha(interacao, membro)


async def _abrir_ficha(
    interacao: discord.Interaction,
    membro: discord.Member,
    bloco: str = BLOCO_RESUMO,
):
    estado = await buscar_estado_plantao(membro.id)
    view = FichaMembroAdminView(membro, estado, bloco_ativo=bloco)
    await view.preparar()
    if interacao.response.is_done():
        await interacao.edit_original_response(view=view)
    else:
        await interacao.response.edit_message(view=view)


# ── Ficha + blocos expansíveis + ações ───────────────────────────────────


class FichaMembroAdminView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        alvo: discord.Member,
        estado: EstadoPlantao | None,
        bloco_ativo: str = BLOCO_RESUMO,
    ):
        super().__init__(timeout=360)
        self.alvo = alvo
        self.estado = estado
        self.bloco_ativo = bloco_ativo
        self._texto_bloco = ""

    async def preparar(self) -> None:
        """Carrega o texto do bloco e monta os componentes."""
        self._texto_bloco = await _montar_texto_bloco(
            self.bloco_ativo, self.alvo, self.estado
        )
        self.clear_items()
        self._montar_componentes()

    def _montar_componentes(self) -> None:
        avatar = getattr(self.alvo, "display_avatar", None)
        avatar_url = avatar.url if avatar else None

        cabecalho = _cabecalho_ficha(self.alvo)
        componentes: list = []

        if avatar_url:
            componentes.append(
                discord.ui.Section(
                    cabecalho,
                    accessory=discord.ui.Thumbnail(avatar_url),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(cabecalho))

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(discord.ui.TextDisplay(self._texto_bloco))
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Select: expandir / trocar bloco
        row_select = discord.ui.ActionRow()
        opcoes = []
        for valor, rotulo in OPCOES_BLOCOS:
            opcoes.append(
                discord.SelectOption(
                    label=rotulo[:100],
                    value=valor,
                    default=(valor == self.bloco_ativo),
                )
            )
        select_bloco = discord.ui.Select(
            placeholder="Expandir bloco…",
            options=opcoes,
            min_values=1,
            max_values=1,
        )
        select_bloco.callback = self._ao_trocar_bloco
        row_select.add_item(select_bloco)
        componentes.append(row_select)

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Ações linha 1
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
        componentes.append(row1)

        # Ações linha 2
        row2 = discord.ui.ActionRow()
        b5 = discord.ui.Button(
            label="Editar status DB", style=discord.ButtonStyle.secondary, emoji="📝"
        )
        b5.callback = self._abrir_modal_status
        row2.add_item(b5)

        b6 = discord.ui.Button(
            label="Gerenciar cargos", style=discord.ButtonStyle.primary, emoji="🏷️"
        )
        b6.callback = self._abrir_cargos
        row2.add_item(b6)

        b7 = discord.ui.Button(
            label="Exonerar", style=discord.ButtonStyle.danger, emoji="⛔"
        )
        b7.callback = self._abrir_modal_exonerar
        row2.add_item(b7)

        b8 = discord.ui.Button(
            label="Nova busca", style=discord.ButtonStyle.secondary, emoji="↩️"
        )
        b8.callback = self._voltar
        row2.add_item(b8)
        componentes.append(row2)

        self.container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.dark_gold(),
        )
        self.add_item(self.container)

    async def _garantir_perm(self, interacao: discord.Interaction) -> bool:
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("ações admin")],
            )
            return False
        return True

    async def _ao_trocar_bloco(self, interacao: discord.Interaction):
        if not await self._garantir_perm(interacao):
            return
        valores = interacao.data.get("values", [])
        novo_bloco = valores[0] if valores else BLOCO_RESUMO
        await interacao.response.defer(ephemeral=True)
        estado = await buscar_estado_plantao(self.alvo.id)
        view = FichaMembroAdminView(self.alvo, estado, bloco_ativo=novo_bloco)
        await view.preparar()
        await interacao.edit_original_response(view=view)

    async def _refresh(self, interacao: discord.Interaction):
        estado = await buscar_estado_plantao(self.alvo.id)
        view = FichaMembroAdminView(self.alvo, estado, bloco_ativo=self.bloco_ativo)
        await view.preparar()
        await interacao.edit_original_response(view=view)

    async def _forcar_desligar(self, interacao: discord.Interaction):
        if not await self._garantir_perm(interacao):
            return
        if not isinstance(self.alvo, discord.Member):
            await responder_erro(
                interacao,
                titulo="Alvo inválido",
                linhas=["Alvo precisa estar no servidor para forçar desligar."],
            )
            return
        await interacao.response.defer(ephemeral=True)
        resultado = await desligar_servico(self.alvo)
        await registrar_auditoria_admin(
            interacao.guild,
            executor=interacao.user,
            alvo=self.alvo,
            acao="FORCAR_DESLIGAR_SERVICO",
            detalhes=resultado,
            cor=discord.Color.red(),
        )
        await self._refresh(interacao)

    async def _zerar_ciclo(self, interacao: discord.Interaction):
        if not await self._garantir_perm(interacao):
            return
        await interacao.response.defer(ephemeral=True)
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
            interacao.guild,
            executor=interacao.user,
            alvo=self.alvo,
            acao="ZERAR_CICLO",
            detalhes="segundos_acumulados = 0",
        )
        await self._refresh(interacao)

    async def _abrir_modal_moedas(self, interacao: discord.Interaction):
        if not await self._garantir_perm(interacao):
            return
        await interacao.response.send_modal(
            ModalAjustarMoedas(self.alvo, self.bloco_ativo)
        )

    async def _abrir_modal_fivem(self, interacao: discord.Interaction):
        if not await self._garantir_perm(interacao):
            return
        await interacao.response.send_modal(
            ModalEditarFivem(self.alvo, self.bloco_ativo)
        )

    async def _abrir_modal_status(self, interacao: discord.Interaction):
        if not await self._garantir_perm(interacao):
            return
        await interacao.response.send_modal(
            ModalEditarStatus(self.alvo, self.bloco_ativo)
        )

    async def _abrir_cargos(self, interacao: discord.Interaction):
        if not await self._garantir_perm(interacao):
            return
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use dentro do servidor."],
            )
            return
        view_cargos = GerenciarCargosView(interacao.user)
        await interacao.response.send_message(view=view_cargos, ephemeral=True)

    async def _abrir_modal_exonerar(self, interacao: discord.Interaction):
        if not await self._garantir_perm(interacao):
            return
        if not isinstance(self.alvo, discord.Member):
            await responder_erro(
                interacao,
                titulo="Alvo inválido",
                linhas=["Alvo precisa estar no servidor para ser exonerado."],
            )
            return
        await interacao.response.send_modal(
            ModalExonerarMembro(self.alvo, self.bloco_ativo)
        )

    async def _voltar(self, interacao: discord.Interaction):
        if not await self._garantir_perm(interacao):
            return
        await interacao.response.edit_message(view=SeletorMembroAdminView())


class ModalAjustarMoedas(discord.ui.Modal, title="Ajustar moedas"):
    valor = discord.ui.TextInput(
        label="Novo saldo (inteiro ≥ 0)",
        placeholder="Ex: 12",
        required=True,
        max_length=6,
    )

    def __init__(self, alvo: discord.Member, bloco_ativo: str = BLOCO_RESUMO):
        super().__init__()
        self.alvo = alvo
        self.bloco_ativo = bloco_ativo

    async def on_submit(self, interacao: discord.Interaction):
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("ajustar moedas")],
            )
            return
        raw = self.valor.value.strip()
        if not raw.isdigit():
            await responder_erro(
                interacao, titulo="Valor inválido", linhas=["Informe um inteiro ≥ 0."]
            )
            return
        novo = int(raw)
        await interacao.response.defer(ephemeral=True)
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
            interacao.guild,
            executor=interacao.user,
            alvo=self.alvo,
            acao="AJUSTAR_MOEDAS",
            detalhes=f"{antigo} → {novo}",
            cor=discord.Color.green(),
        )
        estado = await buscar_estado_plantao(self.alvo.id)
        view = FichaMembroAdminView(self.alvo, estado, bloco_ativo=self.bloco_ativo)
        await view.preparar()
        await interacao.edit_original_response(view=view)


class ModalEditarFivem(discord.ui.Modal, title="Editar ID FiveM"):
    id_fivem = discord.ui.TextInput(
        label="ID FiveM",
        placeholder="Ex: 54623",
        required=True,
        max_length=6,
    )

    def __init__(self, alvo: discord.Member, bloco_ativo: str = BLOCO_RESUMO):
        super().__init__()
        self.alvo = alvo
        self.bloco_ativo = bloco_ativo

    async def on_submit(self, interacao: discord.Interaction):
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("editar ID FiveM")],
            )
            return
        valor = self.id_fivem.value.strip()
        if not valor.isdigit():
            await responder_erro(
                interacao, titulo="ID inválido", linhas=["Informe apenas números."]
            )
            return
        await interacao.response.defer(ephemeral=True)
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
            interacao.guild,
            executor=interacao.user,
            alvo=self.alvo,
            acao="EDITAR_ID_FIVEM",
            detalhes=f"{antigo} → {valor}",
        )
        estado = await buscar_estado_plantao(self.alvo.id)
        view = FichaMembroAdminView(self.alvo, estado, bloco_ativo=self.bloco_ativo)
        await view.preparar()
        await interacao.edit_original_response(view=view)


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

    def __init__(self, alvo: discord.Member, bloco_ativo: str = BLOCO_RESUMO):
        super().__init__()
        self.alvo = alvo
        self.bloco_ativo = bloco_ativo

    async def on_submit(self, interacao: discord.Interaction):
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("editar status")],
            )
            return
        status = self.status_input.value.strip().upper()
        nick = self.nick_input.value.strip() or None
        await interacao.response.defer(ephemeral=True)

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
            interacao.guild,
            executor=interacao.user,
            alvo=self.alvo,
            acao="EDITAR_STATUS_USUARIO",
            detalhes=f"status {antigo} → {status}"
            + (f" · nick={nick}" if nick else ""),
        )
        estado = await buscar_estado_plantao(self.alvo.id)
        view = FichaMembroAdminView(self.alvo, estado, bloco_ativo=self.bloco_ativo)
        await view.preparar()
        await interacao.edit_original_response(view=view)


class ModalExonerarMembro(
    LoggingModalMixin, discord.ui.Modal, title="⛔ Exonerar Membro"
):
    motivo = discord.ui.TextInput(
        label="📄 Motivo da exoneração",
        style=discord.TextStyle.paragraph,
        placeholder="Explique o motivo da exoneração...",
        required=True,
        max_length=1500,
    )
    links = discord.ui.TextInput(
        label="🔗 Provas (links ou texto)",
        style=discord.TextStyle.paragraph,
        placeholder="Links e/ou texto das provas (opcional)",
        required=False,
        max_length=1000,
    )

    def __init__(self, alvo: discord.Member, bloco_ativo: str = BLOCO_RESUMO):
        super().__init__()
        self.alvo = alvo
        self.bloco_ativo = bloco_ativo

    async def on_submit(self, interacao: discord.Interaction):
        if not e_diretoria(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("exonerar membro")],
            )
            return

        if not isinstance(interacao.user, discord.Member) or interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Guilda ou executor inválidos."],
            )
            return

        await interacao.response.defer(ephemeral=True)

        id_fivem = await resolver_id_fivem(self.alvo.id)
        if not id_fivem:
            id_fivem = await resolver_id_fivem_do_membro(self.alvo.id)
        id_fivem = id_fivem or "—"

        motivo_texto = self.motivo.value.strip()
        links_texto = self.links.value.strip() if self.links.value else None

        ok, mensagem = await executar_exoneracao(
            guild=interacao.guild,
            alvo=self.alvo,
            executor=interacao.user,
            id_fivem=id_fivem,
            motivo=motivo_texto,
            links_texto=links_texto,
            punicao_id=None,
            automatica=False,
        )

        await registrar_auditoria_admin(
            interacao.guild,
            executor=interacao.user,
            alvo=self.alvo,
            acao="EXONERAR_MEMBRO",
            detalhes=motivo_texto[:300],
            cor=discord.Color.dark_red(),
        )

        membro_atualizado = interacao.guild.get_member(self.alvo.id) or self.alvo
        estado = await buscar_estado_plantao(membro_atualizado.id)
        view = FichaMembroAdminView(
            membro_atualizado, estado, bloco_ativo=self.bloco_ativo
        )
        await view.preparar()
        await interacao.edit_original_response(view=view)

        if ok:
            await responder_sucesso(
                interacao,
                titulo="Exoneração",
                linhas=[mensagem],
                delay=15,
            )
        else:
            await responder_erro(
                interacao,
                titulo="Exoneração",
                linhas=[mensagem],
                delay=15,
            )
