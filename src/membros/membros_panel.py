"""Painel #gerenciar-membros — Diretoria++: ficha expansivel + acoes admin."""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from src.bau.bau_service import formatar_bloco_itens_yaml, ler_itens_do_caso
from src.config import NOMES_CANAIS_PLANTAO, VALOR_MOEDA_INGAME
from src.membros.cargos_panel import GerenciarCargosView
from src.membros.membros_service import (
    STATUS_USUARIO_CANONICOS,
    ajustar_saldo_moedas,
    buscar_estado_plantao,
    buscar_snapshot_cargos,
    buscar_usuario,
    contagens_resumo_ficha,
    editar_id_fivem_membro,
    editar_status_usuario,
    estatisticas_como_recrutador,
    formatar_cargos_do_membro,
    formatar_timestamp,
    formatar_timestamp_relativo,
    listar_ausencias,
    listar_casos_bau_membro,
    listar_chamadas_como_doutor,
    listar_cursos_membro,
    listar_demissoes,
    listar_faltas_chamada,
    listar_historico_cargos,
    listar_historico_promocoes,
    listar_laudos_como_paciente,
    listar_laudos_como_psicologo,
    listar_movimentacoes_moedas,
    listar_presencas_gate,
    listar_punicoes,
    listar_recrutamentos_candidato,
    listar_solicitacoes_promocao,
    listar_tickets_membro,
    listar_verbais_bau,
    membro_esta_no_servidor,
    resolver_discord_id_por_fivem,
    resolver_id_fivem_do_membro,
    tempo_total_segundos_plantao,
    ultimos_logs_plantao,
    zerar_ciclo_plantao,
)
from src.plantao.auditoria_service import registrar_auditoria_admin
from src.plantao.plantao_permissoes import e_diretoria, mensagem_sem_permissao
from src.plantao.plantao_service import desligar_servico, garantir_aware
from src.punicoes.punicoes_classes import limpar_sessao, obter_sessao
from src.punicoes.punicoes_helpers import resolver_id_fivem
from src.punicoes.punicoes_panel import (
    FluxoAplicarAdvertenciaView,
    FluxoConsultarPunicaoView,
)
from src.punicoes.punicoes_service import executar_exoneracao
from src.utils.error_handling import LoggingModalMixin, LoggingViewMixin
from src.utils.formatacao import formatar_dinheiro, formatar_hms
from src.utils.mensagens import (
    editar_mensagem_original,
    responder_aviso,
    responder_erro,
    responder_sucesso,
    responder_view,
)

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
BLOCO_AUSENCIA = "ausencia"
BLOCO_PROMOCOES = "promocoes"
BLOCO_MOEDAS = "moedas"
BLOCO_GATE = "gate"
BLOCO_TICKETS = "tickets"
BLOCO_CURSOS = "cursos"
BLOCO_SNAPSHOT = "snapshot"

OPCOES_BLOCOS = [
    (BLOCO_RESUMO, "Resumo"),
    (BLOCO_IDENTIDADE, "Identidade"),
    (BLOCO_AUSENCIA, "Ausencia/Demissao"),
    (BLOCO_PROMOCOES, "Promocoes"),
    (BLOCO_RECRUTAMENTO, "Recrutamento"),
    (BLOCO_RECRUTADOR, "Como recrutador"),
    (BLOCO_PLANTAO, "Plantao"),
    (BLOCO_MOEDAS, "Extrato moedas"),
    (BLOCO_CHAMADAS, "Chamadas"),
    (BLOCO_HIST_PLANTAO, "Hist. plantao"),
    (BLOCO_PUNICOES, "Punicoes"),
    (BLOCO_BAU, "Bau"),
    (BLOCO_CARGOS_HIST, "Hist. cargos"),
    (BLOCO_LAUDOS, "Laudos"),
    (BLOCO_GATE, "GATE"),
    (BLOCO_TICKETS, "Tickets"),
    (BLOCO_CURSOS, "Cursos"),
    (BLOCO_SNAPSHOT, "Snapshot"),
]
TIMEOUT_FICHA = 900


class MembroProxy:
    def __init__(self, usuario, guild):
        self.id = usuario.id
        self.display_name = usuario.display_name
        self.mention = usuario.mention
        self.display_avatar = usuario.display_avatar
        self.joined_at = None
        self.roles = []
        self.guild = guild


async def _montar_texto_bloco(chave, membro, estado):
    fn = {
        BLOCO_RESUMO: lambda: _t_resumo(membro, estado),
        BLOCO_IDENTIDADE: lambda: _t_id(membro),
        BLOCO_RECRUTAMENTO: lambda: _t_rec_cand(membro),
        BLOCO_RECRUTADOR: lambda: _t_rec_feito(membro),
        BLOCO_PLANTAO: lambda: _t_plantao(membro, estado),
        BLOCO_CHAMADAS: lambda: _t_chamadas(membro),
        BLOCO_HIST_PLANTAO: lambda: _t_hist_p(membro),
        BLOCO_PUNICOES: lambda: _t_pun(membro),
        BLOCO_BAU: lambda: _t_bau(membro),
        BLOCO_CARGOS_HIST: lambda: _t_cargos(membro),
        BLOCO_LAUDOS: lambda: _t_laudos(membro),
        BLOCO_AUSENCIA: lambda: _t_aus(membro),
        BLOCO_PROMOCOES: lambda: _t_prom(membro),
        BLOCO_MOEDAS: lambda: _t_moedas(membro, estado),
        BLOCO_GATE: lambda: _t_gate(membro),
        BLOCO_TICKETS: lambda: _t_tickets(membro),
        BLOCO_CURSOS: lambda: _t_cursos(membro),
        BLOCO_SNAPSHOT: lambda: _t_snap(membro),
    }.get(chave)
    return await fn() if fn else "_Bloco desconhecido._"


async def _t_resumo(membro, estado):
    fid = await resolver_id_fivem_do_membro(membro.id)
    c = await contagens_resumo_ficha(membro.id, fid)
    online = bool(estado and estado.toggle_ligado)
    saldo = estado.saldo_moedas if estado else 0
    alertas = []
    for k, label in [
        ("punicoes_ativas", "punicoes"),
        ("ausencias_abertas", "ausencias"),
        ("demissoes_pendentes", "demissoes"),
        ("casos_bau_abertos", "bau"),
        ("promocoes_pendentes", "promocoes"),
        ("tickets_abertos", "tickets"),
    ]:
        if c.get(k):
            alertas.append(f"`{c[k]}` {label}")
    al = ", ".join(alertas) if alertas else "nenhum"
    return (
        f"## Resumo\n**Plantao:** {'em servico' if online else 'fora'} · **Moedas:** `{saldo}`\n"
        f"**FiveM:** `{fid or '—'}`\n**Alertas:** {al}\n"
        f"**Faltas/chamadas doutor:** `{c['faltas_chamada']}` / `{c['chamadas_doutor']}`\n"
        f"**Recrutamentos:** `{c['recrutamentos']}` · **Laudos pac/psi:** "
        f"`{c['laudos_paciente']}`/`{c['laudos_psicologo']}` · **Hist cargos:** `{c['historico_cargos']}`"
    )


async def _t_id(membro):
    u = await buscar_usuario(membro.id)
    fid = await resolver_id_fivem_do_membro(membro.id)
    status = u.status if u else "—"
    nick = (u.nickname_atual if u and u.nickname_atual else None) or membro.display_name
    ja = "sim" if (u and u.ja_foi_aprovado) else "nao"
    entrou = (
        f"<t:{int(membro.joined_at.timestamp())}:f>"
        if getattr(membro, "joined_at", None)
        else "—"
    )
    return (
        f"## Identidade\n**Nick:** {membro.display_name} · **DB:** {nick}\n"
        f"**FiveM:** `{fid or '—'}` · **Status:** `{status}` · **Aprovado:** `{ja}`\n"
        f"**Entrou:** {entrou} · **No server:** `{'sim' if membro_esta_no_servidor(membro) else 'nao'}`\n"
        f"**Cargos:**\n{formatar_cargos_do_membro(membro)}"
    )


async def _t_rec_cand(membro):
    lista = await listar_recrutamentos_candidato(membro.id)
    if not lista:
        return "## Recrutamento\n_Nenhum._"
    linhas = ["## Recrutamento"]
    for i, r in enumerate(lista):
        p = "**Atual**" if i == 0 else "•"
        linhas.append(
            f"{p} `#{r.id}` **{r.status}** `{r.cargo_final or '—'}` "
            f"<@{r.discord_id_recrutador}> {formatar_timestamp(r.data_inicio)}"
        )
    return "\n".join(linhas)


async def _t_rec_feito(membro):
    total, sem, ult = await estatisticas_como_recrutador(membro.id)
    linhas = [f"## Como recrutador\n**Total:** `{total}` · **Semana:** `{sem}`"]
    for r in ult:
        linhas.append(
            f"`{r.id_fivem or '?'}` <@{r.discord_id_candidato}> {formatar_timestamp(r.data_fim)}"
        )
    return "\n".join(linhas)


async def _t_plantao(membro, estado):
    online = bool(estado and estado.toggle_ligado)
    saldo = estado.saldo_moedas if estado else 0
    segs = estado.segundos_acumulados if estado else 0
    total = await tempo_total_segundos_plantao(membro.id)
    if online and estado and estado.em_call_valida and estado.call_entrada_em:
        ciclo = int(
            (
                datetime.now(timezone.utc) - garantir_aware(estado.call_entrada_em)
            ).total_seconds()
        )
        nome = NOMES_CANAIS_PLANTAO.get(estado.canal_atual_id, "?")
        st = f"em servico · call `{formatar_hms(ciclo)}` · {nome}"
    elif online:
        st = "em servico · aguardando call"
    else:
        st = "fora de servico"
    return (
        f"## Plantao\n**{st}**\n**Moedas:** `{saldo}` ({formatar_dinheiro(saldo * VALOR_MOEDA_INGAME)})\n"
        f"**Ciclo:** `{segs}s` · **Total:** `{formatar_hms(total)}`"
    )


async def _t_chamadas(membro):
    faltas = await listar_faltas_chamada(membro.id)
    doutor = await listar_chamadas_como_doutor(membro.id)
    linhas = ["## Chamadas"]
    if faltas:
        linhas.append("**Faltas:**")
        for f in faltas:
            linhas.append(
                f"• `#{f.chamada_id}` `{f.motivo}` {formatar_timestamp_relativo(f.criado_em)}"
            )
    else:
        linhas.append("**Faltas:** nenhuma")
    if doutor:
        linhas.append("**Como doutor:**")
        for c in doutor:
            linhas.append(
                f"• `#{c.id}` p`{c.total_presentes}` a`{c.total_ausentes}` {formatar_timestamp(c.criada_em)}"
            )
    return "\n".join(linhas)


async def _t_hist_p(membro):
    logs = await ultimos_logs_plantao(membro.id, 10)
    if not logs:
        return "## Hist. plantao\n_Vazio._"
    return "## Hist. plantao\n" + "\n".join(
        f"`{l.evento}` {formatar_timestamp_relativo(l.criado_em)}" for l in logs
    )


async def _t_pun(membro):
    ativas = await listar_punicoes(membro.id, so_ativas=True, limite=8)
    recentes = await listar_punicoes(membro.id, so_ativas=None, limite=8)
    linhas = ["## Punicoes", "**Ativas:**" if ativas else "**Ativas:** nenhuma"]
    for p in ativas:
        linhas.append(
            f"• `#{p.id}` **{p.cargo_nome}** {formatar_timestamp(p.criada_em)} <@{p.executor_id}>"
        )
    ina = [p for p in recentes if not p.ativa][:5]
    if ina:
        linhas.append("**Historico:**")
        for p in ina:
            linhas.append(
                f"• `#{p.id}` ~~{p.cargo_nome}~~ {formatar_timestamp(p.criada_em)}"
            )
    return "\n".join(linhas)


async def _t_bau(membro):
    fid = await resolver_id_fivem_do_membro(membro.id)
    casos = await listar_casos_bau_membro(
        discord_id=membro.id, id_fivem=fid, so_abertos=True, limite=5
    )
    hist = await listar_casos_bau_membro(
        discord_id=membro.id, id_fivem=fid, so_abertos=False, limite=4
    )
    verb = await listar_verbais_bau(discord_id=membro.id, id_fivem=fid, limite=6)
    linhas = ["## Bau"]
    if casos:
        for c in casos:
            linhas.append(
                f"• `#{c.id}` `{c.status}`\n{formatar_bloco_itens_yaml(ler_itens_do_caso(c))}"
            )
    else:
        linhas.append("**Abertos:** nenhum")
    if hist:
        linhas.append(
            "**Historico:** " + ", ".join(f"`#{c.id}` {c.status}" for c in hist[:4])
        )
    if verb:
        for v in verb:
            linhas.append(f"• verbal `{v.tipo}` {formatar_timestamp(v.criada_em)}")
    return "\n".join(linhas)


async def _t_cargos(membro):
    h = await listar_historico_cargos(membro.id)
    if not h:
        return "## Hist. cargos\n_Vazio._"
    linhas = ["## Hist. cargos"]
    for i in h:
        s = "+" if i.acao == "ADICIONADO" else "-"
        linhas.append(
            f"{s} **{i.cargo}** {i.acao} {formatar_timestamp_relativo(i.data_hora)} <@{i.executor_id}>"
        )
    return "\n".join(linhas)


async def _t_laudos(membro):
    pac = await listar_laudos_como_paciente(membro.id)
    psi = await listar_laudos_como_psicologo(membro.id)
    linhas = ["## Laudos"]
    linhas.append("**Paciente:**" if pac else "**Paciente:** nenhum")
    for l in pac:
        linhas.append(f"• `#{l.id}` **{l.parecer}** {formatar_timestamp(l.criado_em)}")
    linhas.append("**Psicologo:**" if psi else "**Psicologo:** nenhum")
    for l in psi:
        linhas.append(f"• `#{l.id}` **{l.parecer}** <@{l.discord_id_paciente}>")
    return "\n".join(linhas)


async def _t_aus(membro):
    aus = await listar_ausencias(membro.id)
    dem = await listar_demissoes(membro.id)
    linhas = ["## Ausencia / Demissao"]
    if aus:
        for a in aus:
            linhas.append(
                f"• aus `#{a.id}` **{a.status}** `{a.tipo}` {a.periodo_rotulo}"
            )
    else:
        linhas.append("**Ausencias:** nenhuma")
    if dem:
        for d in dem:
            linhas.append(f"• dem `#{d.id}` **{d.status}** `{d.tipo_demissao}`")
    else:
        linhas.append("**Demissoes:** nenhuma")
    return "\n".join(linhas)


async def _t_prom(membro):
    s = await listar_solicitacoes_promocao(membro.id)
    h = await listar_historico_promocoes(membro.id)
    linhas = ["## Promocoes"]
    if s:
        for x in s:
            linhas.append(
                f"• sol `#{x.id}` **{x.status}** `{x.cargo_de}`→`{x.cargo_para}`"
            )
    else:
        linhas.append("**Solicitacoes:** nenhuma")
    if h:
        for x in h:
            linhas.append(
                f"• hist `{x.tipo}` `{x.cargo_de or '—'}`→`{x.cargo_para or '—'}`"
            )
    return "\n".join(linhas)


async def _t_moedas(membro, estado):
    saldo = estado.saldo_moedas if estado else 0
    movs = await listar_movimentacoes_moedas(membro.id)
    linhas = [f"## Extrato moedas\n**Saldo:** `{saldo}`"]
    for m in movs:
        sinal = f"+{m.valor}" if m.valor >= 0 else str(m.valor)
        linhas.append(
            f"• `{m.tipo}` **{sinal}** →`{m.saldo_apos}` {formatar_timestamp_relativo(m.criado_em)}"
        )
    if not movs:
        linhas.append("_Sem movimentacoes._")
    return "\n".join(linhas)


async def _t_gate(membro):
    itens = await listar_presencas_gate(membro.id)
    if not itens:
        return "## GATE\n_Vazio._"
    linhas = ["## GATE"]
    for p, e in itens:
        t = e.titulo if e else f"#{p.evento_id}"
        linhas.append(
            f"• **{t}** FiveM `{p.id_fivem}` {formatar_timestamp_relativo(p.confirmed_at)}"
        )
    return "\n".join(linhas)


async def _t_tickets(membro):
    ts = await listar_tickets_membro(membro.id)
    if not ts:
        return "## Tickets\n_Vazio._"
    return "## Tickets\n" + "\n".join(
        f"• `#{t.id}` **{t.status}** {t.categoria_rotulo} {formatar_timestamp(t.aberto_em)}"
        for t in ts
    )


async def _t_cursos(membro):
    cs = await listar_cursos_membro(membro.id)
    if not cs:
        return "## Cursos\n_Vazio._"
    return "## Cursos\n" + "\n".join(
        f"• `#{c.id}` **{c.status}** `{c.chave_curso}` {formatar_timestamp(c.criado_em)}"
        for c in cs
    )


async def _t_snap(membro):
    s = await buscar_snapshot_cargos(membro.id)
    if not s:
        return "## Snapshot\n_Vazio._"
    return (
        f"## Snapshot\n**Atualizado:** {formatar_timestamp_relativo(s.atualizado_em)}\n"
        f"**Nick:** `{s.nickname or '—'}`\n**Cargos:** `{(s.role_names or '[]')[:300]}`"
    )


async def _cabecalho(membro, estado):
    fid = await resolver_id_fivem_do_membro(membro.id)
    u = await buscar_usuario(membro.id)
    st = u.status if u else "—"
    online = bool(estado and estado.toggle_ligado)
    c = await contagens_resumo_ficha(membro.id, fid)
    badges = []
    for k, lab in [
        ("punicoes_ativas", "pun"),
        ("ausencias_abertas", "aus"),
        ("casos_bau_abertos", "bau"),
        ("promocoes_pendentes", "prom"),
        ("tickets_abertos", "tk"),
    ]:
        if c.get(k):
            badges.append(f"{lab}:{c[k]}")
    if not membro_esta_no_servidor(membro):
        badges.append("fora-server")
    b = " · ".join(badges) if badges else "ok"
    return (
        f"# {membro.display_name}\n{membro.mention} · `{membro.id}`\n"
        f"**FiveM:** `{fid or '—'}` · **Status:** `{st}` · **Plantao:** "
        f"{'em servico' if online else 'fora'}\n**Badges:** {b}"
    )


class PainelGerenciarMembrosLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        icon = guild.icon.url if guild.icon else None
        row = discord.ui.ActionRow()
        btn = discord.ui.Button(
            label="Gerenciar Membros",
            style=discord.ButtonStyle.primary,
            custom_id="admin:gerenciar_membros",
            emoji="🛡️",
        )
        btn.callback = self._ao_abrir
        row.add_item(btn)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# Gerenciar Membros"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.Section(
                    "> **Somente Diretoria++**",
                    "Ficha completa e acoes admin.\n-# Auditoria em todas as acoes.",
                    accessory=discord.ui.Thumbnail(icon) if icon else None,
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                row,
                accent_color=discord.Color.dark_gold(),
            )
        )

    async def _ao_abrir(self, i: discord.Interaction):
        if not isinstance(i.user, discord.Member):
            await responder_erro(i, titulo="Contexto", linhas=["Use no servidor."])
            return
        if not e_diretoria(i.user):
            await responder_erro(
                i,
                titulo="Sem permissao",
                linhas=[mensagem_sem_permissao("gerenciar membros")],
            )
            return
        await responder_view(i, SeletorMembroAdminView(), ephemeral=True)


class SeletorMembroAdminView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=300)
        ru = discord.ui.ActionRow()
        sel = discord.ui.UserSelect(
            placeholder="Selecione o membro…", min_values=1, max_values=1
        )
        sel.callback = self._on_select
        ru.add_item(sel)
        ri = discord.ui.ActionRow()
        b1 = discord.ui.Button(
            label="Discord ID", style=discord.ButtonStyle.secondary, emoji="🔍"
        )
        b1.callback = self._on_d
        b2 = discord.ui.Button(
            label="FiveM", style=discord.ButtonStyle.secondary, emoji="🪪"
        )
        b2.callback = self._on_f
        ri.add_item(b1)
        ri.add_item(b2)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# Buscar membro"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                ru,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                ri,
                accent_color=discord.Color.dark_gold(),
            )
        )

    async def _on_select(self, i: discord.Interaction):
        if not e_diretoria(i.user):
            await responder_erro(
                i, titulo="Sem permissao", linhas=[mensagem_sem_permissao("fichas")]
            )
            return
        ids = i.data.get("values", [])
        m = i.guild.get_member(int(ids[0])) if ids else None
        if not m:
            await responder_erro(i, titulo="Ausente", linhas=["Use busca por ID."])
            return
        await i.response.defer(ephemeral=True)
        await _abrir_ficha(i, m)

    async def _on_d(self, i: discord.Interaction):
        if not e_diretoria(i.user):
            await responder_erro(
                i, titulo="Sem permissao", linhas=[mensagem_sem_permissao("buscar")]
            )
            return
        await i.response.send_modal(ModalBuscarDiscordId())

    async def _on_f(self, i: discord.Interaction):
        if not e_diretoria(i.user):
            await responder_erro(
                i, titulo="Sem permissao", linhas=[mensagem_sem_permissao("buscar")]
            )
            return
        await i.response.send_modal(ModalBuscarFivemId())


class ModalBuscarDiscordId(LoggingModalMixin, discord.ui.Modal, title="Discord ID"):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID", required=True, max_length=20
    )

    async def on_submit(self, i: discord.Interaction):
        if not e_diretoria(i.user):
            await responder_erro(
                i, titulo="Sem permissao", linhas=[mensagem_sem_permissao("buscar")]
            )
            return
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await responder_erro(i, titulo="ID invalido", linhas=["Somente numeros."])
            return
        did = int(raw)
        await i.response.defer(ephemeral=True)
        m = i.guild.get_member(did) if i.guild else None
        if m is None:
            try:
                u = await i.client.fetch_user(did)
            except discord.NotFound:
                await responder_erro(i, titulo="Nao encontrado", linhas=[f"`{did}`"])
                return
            await responder_aviso(
                i,
                titulo="Fora do server",
                linhas=[f"<@{did}> fora do servidor."],
                delay=8,
            )
            m = MembroProxy(u, i.guild)
        await _abrir_ficha(i, m)


class ModalBuscarFivemId(LoggingModalMixin, discord.ui.Modal, title="ID FiveM"):
    id_fivem_input = discord.ui.TextInput(
        label="ID FiveM", required=True, max_length=20
    )

    async def on_submit(self, i: discord.Interaction):
        if not e_diretoria(i.user):
            await responder_erro(
                i, titulo="Sem permissao", linhas=[mensagem_sem_permissao("buscar")]
            )
            return
        v = self.id_fivem_input.value.strip()
        if not v.isdigit():
            await responder_erro(i, titulo="ID invalido", linhas=["Somente numeros."])
            return
        await i.response.defer(ephemeral=True)
        did = await resolver_discord_id_por_fivem(v)
        if did is None:
            await responder_erro(i, titulo="Nao encontrado", linhas=[f"FiveM `{v}`"])
            return
        m = i.guild.get_member(did) if i.guild else None
        if m is None:
            try:
                u = await i.client.fetch_user(did)
            except discord.NotFound:
                await responder_erro(i, titulo="Invalido", linhas=[f"`{did}`"])
                return
            m = MembroProxy(u, i.guild)
            await responder_aviso(
                i, titulo="Fora do server", linhas=[f"FiveM `{v}` → <@{did}>"], delay=8
            )
        await _abrir_ficha(i, m)


async def _abrir_ficha(i, membro, bloco=BLOCO_RESUMO, status=None):
    estado = await buscar_estado_plantao(membro.id)
    view = FichaMembroAdminView(
        membro, estado, bloco_ativo=bloco, mensagem_status=status
    )
    await view.preparar()
    await editar_mensagem_original(i, view=view)


class FichaMembroAdminView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        alvo,
        estado,
        bloco_ativo=BLOCO_RESUMO,
        mensagem_status=None,
        confirmar_desligar=False,
    ):
        super().__init__(timeout=TIMEOUT_FICHA)
        self.alvo = alvo
        self.estado = estado
        self.bloco_ativo = bloco_ativo
        self.mensagem_status = mensagem_status
        self.confirmar_desligar = confirmar_desligar
        self._txt = ""
        self._cab = ""

    async def preparar(self):
        self._cab = await _cabecalho(self.alvo, self.estado)
        self._txt = await _montar_texto_bloco(self.bloco_ativo, self.alvo, self.estado)
        self.clear_items()
        self._montar()

    def _montar(self):
        no = membro_esta_no_servidor(self.alvo)
        av = getattr(self.alvo, "display_avatar", None)
        comps = []
        if av:
            comps.append(
                discord.ui.Section(self._cab, accessory=discord.ui.Thumbnail(av.url))
            )
        else:
            comps.append(discord.ui.TextDisplay(self._cab))
        if self.mensagem_status:
            comps.append(discord.ui.TextDisplay(f"> {self.mensagem_status}"))
        comps += [
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(self._txt),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        ]
        row_s = discord.ui.ActionRow()
        sel = discord.ui.Select(
            placeholder="Expandir bloco…",
            options=[
                discord.SelectOption(
                    label=r[:100], value=v, default=(v == self.bloco_ativo)
                )
                for v, r in OPCOES_BLOCOS[:25]
            ],
            min_values=1,
            max_values=1,
        )
        sel.callback = self._trocar
        row_s.add_item(sel)
        comps.append(row_s)
        comps.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        if self.confirmar_desligar:
            comps.append(discord.ui.TextDisplay("### Confirmar forcar desligar?"))
            rc = discord.ui.ActionRow()
            bs = discord.ui.Button(
                label="Confirmar", style=discord.ButtonStyle.danger, emoji="🔴"
            )
            bs.callback = self._conf_desligar
            bn = discord.ui.Button(
                label="Cancelar", style=discord.ButtonStyle.secondary
            )
            bn.callback = self._canc_desligar
            rc.add_item(bs)
            rc.add_item(bn)
            comps.append(rc)
        else:
            r1 = discord.ui.ActionRow()
            for label, style, emoji, cb, dis in [
                (
                    "Forcar desligar",
                    discord.ButtonStyle.danger,
                    "🔴",
                    self._pedir_desligar,
                    not no,
                ),
                ("Zerar ciclo", discord.ButtonStyle.secondary, "⏱️", self._zerar, False),
                ("Moedas", discord.ButtonStyle.primary, "💰", self._moedas, False),
                ("FiveM", discord.ButtonStyle.primary, "🪪", self._fivem, False),
            ]:
                b = discord.ui.Button(
                    label=label, style=style, emoji=emoji, disabled=dis
                )
                b.callback = cb
                r1.add_item(b)
            comps.append(r1)
            r2 = discord.ui.ActionRow()
            for label, style, emoji, cb, dis in [
                ("Status DB", discord.ButtonStyle.secondary, "📝", self._status, False),
                ("Cargos", discord.ButtonStyle.primary, "🏷️", self._cargos, not no),
                ("Advertencia", discord.ButtonStyle.danger, "⚠️", self._adv, not no),
                ("Exonerar", discord.ButtonStyle.danger, "⛔", self._exon, not no),
            ]:
                b = discord.ui.Button(
                    label=label, style=style, emoji=emoji, disabled=dis
                )
                b.callback = cb
                r2.add_item(b)
            comps.append(r2)
            r3 = discord.ui.ActionRow()
            for label, emoji, cb in [
                ("Ver punicoes", "📋", self._ver_pun),
                ("Atualizar", "🔄", self._att),
                ("Nova busca", "↩️", self._voltar),
            ]:
                b = discord.ui.Button(
                    label=label, style=discord.ButtonStyle.secondary, emoji=emoji
                )
                b.callback = cb
                r3.add_item(b)
            comps.append(r3)
            if not no:
                comps.append(
                    discord.ui.TextDisplay(
                        "-# Fora do server: desligar/cargos/advertencia/exonerar bloqueados."
                    )
                )
        self.add_item(
            discord.ui.Container(*comps, accent_color=discord.Color.dark_gold())
        )

    async def _perm(self, i):
        if not e_diretoria(i.user):
            await responder_erro(
                i, titulo="Sem permissao", linhas=[mensagem_sem_permissao("acoes")]
            )
            return False
        return True

    async def _trocar(self, i):
        if not await self._perm(i):
            return
        v = (i.data.get("values") or [BLOCO_RESUMO])[0]
        await i.response.defer(ephemeral=True)
        await _abrir_ficha(i, self.alvo, bloco=v)

    async def _refresh(self, i, status=None, conf=False):
        estado = await buscar_estado_plantao(self.alvo.id)
        view = FichaMembroAdminView(
            self.alvo,
            estado,
            bloco_ativo=self.bloco_ativo,
            mensagem_status=status,
            confirmar_desligar=conf,
        )
        await view.preparar()
        await editar_mensagem_original(i, view=view)

    async def _att(self, i):
        if not await self._perm(i):
            return
        await i.response.defer(ephemeral=True)
        await self._refresh(i, "Ficha atualizada.")

    async def _pedir_desligar(self, i):
        if not await self._perm(i):
            return
        await i.response.defer(ephemeral=True)
        await self._refresh(i, conf=True)

    async def _canc_desligar(self, i):
        if not await self._perm(i):
            return
        await i.response.defer(ephemeral=True)
        await self._refresh(i, "Desligar cancelado.")

    async def _conf_desligar(self, i):
        if not await self._perm(i):
            return
        if not isinstance(self.alvo, discord.Member):
            await responder_erro(i, titulo="Alvo", linhas=["Precisa estar no server."])
            return
        await i.response.defer(ephemeral=True)
        res = await desligar_servico(self.alvo)
        await registrar_auditoria_admin(
            i.guild,
            executor=i.user,
            alvo=self.alvo,
            acao="FORCAR_DESLIGAR_SERVICO",
            detalhes=str(res),
            cor=discord.Color.red(),
        )
        await self._refresh(i, f"Desligado. {res}")

    async def _zerar(self, i):
        if not await self._perm(i):
            return
        await i.response.defer(ephemeral=True)
        ok = await zerar_ciclo_plantao(self.alvo.id)
        await registrar_auditoria_admin(
            i.guild,
            executor=i.user,
            alvo=self.alvo,
            acao="ZERAR_CICLO",
            detalhes="0" if ok else "sem estado",
        )
        await self._refresh(i, "Ciclo zerado." if ok else "Sem estado de plantao.")

    async def _moedas(self, i):
        if not await self._perm(i):
            return
        await i.response.send_modal(ModalAjustarMoedas(self.alvo, self.bloco_ativo))

    async def _fivem(self, i):
        if not await self._perm(i):
            return
        atual = await resolver_id_fivem_do_membro(self.alvo.id)
        await i.response.send_modal(
            ModalEditarFivem(self.alvo, self.bloco_ativo, atual)
        )

    async def _status(self, i):
        if not await self._perm(i):
            return
        await i.response.send_modal(ModalEditarStatus(self.alvo, self.bloco_ativo))

    async def _cargos(self, i):
        if not await self._perm(i):
            return
        if not isinstance(i.user, discord.Member) or not isinstance(
            self.alvo, discord.Member
        ):
            await responder_erro(i, titulo="Alvo", linhas=["Precisa estar no server."])
            return
        await responder_view(
            i,
            GerenciarCargosView(i.user, membro_pre_selecionado=self.alvo),
            ephemeral=True,
        )

    async def _adv(self, i):
        if not await self._perm(i):
            return
        if not isinstance(self.alvo, discord.Member):
            await responder_erro(i, titulo="Alvo", linhas=["Precisa estar no server."])
            return
        limpar_sessao(i.user.id)
        s = obter_sessao(i.user.id)
        s.membro_id = self.alvo.id
        s.membro_mention = self.alvo.mention
        fid = await resolver_id_fivem_do_membro(self.alvo.id)
        if fid:
            s.id_fivem = fid
        await responder_view(i, FluxoAplicarAdvertenciaView(i.user.id), ephemeral=True)

    async def _ver_pun(self, i):
        if not await self._perm(i):
            return
        await responder_view(i, FluxoConsultarPunicaoView(), ephemeral=True)

    async def _exon(self, i):
        if not await self._perm(i):
            return
        if not isinstance(self.alvo, discord.Member):
            await responder_erro(i, titulo="Alvo", linhas=["Precisa estar no server."])
            return
        await i.response.send_modal(ModalExonerarMembro(self.alvo, self.bloco_ativo))

    async def _voltar(self, i):
        if not await self._perm(i):
            return
        await editar_mensagem_original(i, view=SeletorMembroAdminView())


class ModalAjustarMoedas(LoggingModalMixin, discord.ui.Modal, title="Ajustar moedas"):
    modo = discord.ui.TextInput(
        label="Modo ABSOLUTO ou DELTA", default="ABSOLUTO", max_length=10
    )
    valor = discord.ui.TextInput(label="Valor", required=True, max_length=8)
    motivo = discord.ui.TextInput(label="Motivo", required=False, max_length=120)

    def __init__(self, alvo, bloco=BLOCO_RESUMO):
        super().__init__()
        self.alvo = alvo
        self.bloco = bloco

    async def on_submit(self, i: discord.Interaction):
        if not e_diretoria(i.user):
            await responder_erro(
                i, titulo="Sem permissao", linhas=[mensagem_sem_permissao("moedas")]
            )
            return
        raw = (self.valor.value or "").strip()
        try:
            num = int(raw)
        except ValueError:
            await responder_erro(i, titulo="Valor", linhas=["Inteiro."])
            return
        await i.response.defer(ephemeral=True)
        try:
            if (self.modo.value or "").upper().startswith("D"):
                a, n = await ajustar_saldo_moedas(
                    self.alvo.id,
                    delta=num,
                    executor_id=i.user.id,
                    referencia=self.motivo.value or "delta",
                )
            else:
                a, n = await ajustar_saldo_moedas(
                    self.alvo.id,
                    novo_saldo=num,
                    executor_id=i.user.id,
                    referencia=self.motivo.value or "absoluto",
                )
        except ValueError as e:
            await responder_erro(i, titulo="Recusado", linhas=[str(e)])
            return
        await registrar_auditoria_admin(
            i.guild,
            executor=i.user,
            alvo=self.alvo,
            acao="AJUSTAR_MOEDAS",
            detalhes=f"{a}->{n}",
            cor=discord.Color.green(),
        )
        await _abrir_ficha(
            i, self.alvo, bloco=self.bloco, status=f"Moedas `{a}` → `{n}`"
        )


class ModalEditarFivem(LoggingModalMixin, discord.ui.Modal, title="Editar FiveM"):
    id_fivem = discord.ui.TextInput(
        label="ID FiveM (vazio=limpar)", required=False, max_length=20
    )

    def __init__(self, alvo, bloco=BLOCO_RESUMO, valor_atual=None):
        super().__init__()
        self.alvo = alvo
        self.bloco = bloco
        if valor_atual:
            self.id_fivem.default = str(valor_atual)[:20]

    async def on_submit(self, i: discord.Interaction):
        if not e_diretoria(i.user):
            await responder_erro(
                i, titulo="Sem permissao", linhas=[mensagem_sem_permissao("fivem")]
            )
            return
        v = (self.id_fivem.value or "").strip()
        await i.response.defer(ephemeral=True)
        try:
            ant = await editar_id_fivem_membro(self.alvo.id, v or None)
        except ValueError as e:
            await responder_erro(i, titulo="Invalido", linhas=[str(e)])
            return
        await registrar_auditoria_admin(
            i.guild,
            executor=i.user,
            alvo=self.alvo,
            acao="EDITAR_ID_FIVEM",
            detalhes=f"{ant}->{v or 'vazio'}",
        )
        await _abrir_ficha(
            i,
            self.alvo,
            bloco=self.bloco,
            status=f"FiveM `{ant or 'vazio'}` → `{v or 'vazio'}`",
        )


class ModalEditarStatus(LoggingModalMixin, discord.ui.Modal, title="Status DB"):
    status_input = discord.ui.TextInput(
        label="Status (VISITANTE|ESTUDANTE|PROVA|APROVADO)",
        required=True,
        max_length=30,
    )
    nick_input = discord.ui.TextInput(
        label="Nick DB (opcional)", required=False, max_length=100
    )
    sync_nick = discord.ui.TextInput(
        label="Sync nick Discord? sim/nao", default="nao", max_length=3
    )

    def __init__(self, alvo, bloco=BLOCO_RESUMO):
        super().__init__()
        self.alvo = alvo
        self.bloco = bloco

    async def on_submit(self, i: discord.Interaction):
        if not e_diretoria(i.user):
            await responder_erro(
                i, titulo="Sem permissao", linhas=[mensagem_sem_permissao("status")]
            )
            return
        st = self.status_input.value.strip().upper()
        nick = self.nick_input.value.strip() or None
        sync = (self.sync_nick.value or "").lower() in ("s", "sim", "y", "yes")
        await i.response.defer(ephemeral=True)
        try:
            ant, novo = await editar_status_usuario(
                self.alvo.id,
                st,
                nickname=nick,
                sincronizar_nick_discord=self.alvo.display_name if sync else None,
            )
        except ValueError as e:
            await responder_erro(
                i, titulo="Status", linhas=[str(e), ", ".join(STATUS_USUARIO_CANONICOS)]
            )
            return
        await registrar_auditoria_admin(
            i.guild,
            executor=i.user,
            alvo=self.alvo,
            acao="EDITAR_STATUS_USUARIO",
            detalhes=f"{ant}->{novo}",
        )
        await _abrir_ficha(
            i, self.alvo, bloco=self.bloco, status=f"Status `{ant}` → `{novo}`"
        )


class ModalExonerarMembro(LoggingModalMixin, discord.ui.Modal, title="Exonerar"):
    motivo = discord.ui.TextInput(
        label="Motivo",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1500,
    )
    links = discord.ui.TextInput(
        label="Provas",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )
    confirmar = discord.ui.TextInput(
        label="Digite EXONERAR", required=True, max_length=20
    )

    def __init__(self, alvo, bloco=BLOCO_RESUMO):
        super().__init__()
        self.alvo = alvo
        self.bloco = bloco

    async def on_submit(self, i: discord.Interaction):
        if not e_diretoria(i.user):
            await responder_erro(
                i, titulo="Sem permissao", linhas=[mensagem_sem_permissao("exonerar")]
            )
            return
        if (self.confirmar.value or "").strip().upper() != "EXONERAR":
            await responder_erro(i, titulo="Confirmacao", linhas=["Digite EXONERAR."])
            return
        if not isinstance(i.user, discord.Member) or i.guild is None:
            await responder_erro(i, titulo="Contexto", linhas=["Guilda invalida."])
            return
        await i.response.defer(ephemeral=True)
        fid = (
            await resolver_id_fivem(self.alvo.id)
            or await resolver_id_fivem_do_membro(self.alvo.id)
            or "—"
        )
        ok, msg = await executar_exoneracao(
            guild=i.guild,
            alvo=self.alvo,
            executor=i.user,
            id_fivem=fid,
            motivo=self.motivo.value.strip(),
            links_texto=(self.links.value or "").strip() or None,
            punicao_id=None,
            automatica=False,
        )
        await registrar_auditoria_admin(
            i.guild,
            executor=i.user,
            alvo=self.alvo,
            acao="EXONERAR_MEMBRO",
            detalhes=self.motivo.value[:300],
            cor=discord.Color.dark_red(),
        )
        m = i.guild.get_member(self.alvo.id) or self.alvo
        await _abrir_ficha(i, m, bloco=self.bloco, status=msg[:200])
        if ok:
            await responder_sucesso(i, titulo="Exoneracao", linhas=[msg], delay=15)
        else:
            await responder_erro(i, titulo="Exoneracao", linhas=[msg], delay=15)
