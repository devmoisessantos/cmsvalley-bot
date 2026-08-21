"""Rankings de plantão:

- Chamadas: quantas chamadas cada doutor realizou (tabela chamadas)
- Horas: tempo em call por membro (soma de log_plantao.duracao_segundos)

Contagem em tempo real (igual ao ranking de moedas):
- O cog RankingPlantaoTasks roda um loop a cada 1 minuto.
- No período ``tempo_real``, além dos logs fechados, soma o trecho
  ainda aberto em call (estado_plantao.segmento_iniciado_em → agora).
- Só entram membros elegíveis (mesma regra do ranking de moedas:
  no servidor + cargo de hierarquia).

Ciclos oficiais: semanal (sábado) e mensal (dia 1), sem trecho ao vivo.
UI: Components V2.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import (
    datetime,
    timezone,
)
from zoneinfo import ZoneInfo

import discord
from sqlalchemy import (
    func,
    select,
)

from src.config import (
    CARGOS,
    PREMIOS_RANKING_HORAS,
    RANKING_HORAS_CARGOS_EXCLUIDOS,
    RANKING_HORAS_IDS_EXCLUIDOS,
    TIMEZONE_LOCAL,
)
from src.database.conexao import async_session
from src.database.models import (
    Chamada,
    EstadoPlantao,
    LogPlantao,
    RankingHistorico,
    agora,
)
from src.plantao.carteira_service import membro_elegivel_ranking_moedas
from src.recrutamento.ranking_service import (
    obter_periodo_ciclo_mensal,
    obter_periodo_ciclo_semanal,
    obter_periodo_postagem_mensal,
    obter_periodo_postagem_semanal,
)
from src.utils.formatacao import (
    formatar_data_curta,
    formatar_hms,
    formatar_mes_e_ano,
    formatar_reais,
)


def _tz() -> ZoneInfo:
    return ZoneInfo(TIMEZONE_LOCAL)


def _formatar_data_curta(data_e_hora: datetime) -> str:
    """Data curta DD/MM. Delega para o formatador comum do projeto."""
    return formatar_data_curta(data_e_hora)


def _formatar_mes_ano(data_e_hora: datetime) -> str:
    """Mes abreviado com ano. Delega para o formatador comum do projeto."""
    return formatar_mes_e_ano(data_e_hora)


def _medalha(posicao: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(posicao, "🏅")


def _formatar_mencoes(ids: list[int]) -> str:
    tags = [f"<@{uid}>" for uid in ids]
    if len(tags) == 1:
        return tags[0]
    if len(tags) == 2:
        return f"{tags[0]} e {tags[1]}"
    if len(tags) <= 4:
        return ", ".join(tags[:-1]) + f" e {tags[-1]}"
    meio = (len(tags) + 1) // 2
    return ", ".join(tags[:meio]) + ",\n" + ", ".join(tags[meio:-1]) + f" e {tags[-1]}"


# ── Consultas ────────────────────────────────────────────────────────────


async def buscar_chamadas_por_doutor(
    inicio_utc: datetime,
    fim_utc: datetime,
) -> dict[int, int]:
    """{doutor_id: qtd_chamadas} no período [inicio, fim)."""
    async with async_session() as session:
        resultado = await session.execute(
            select(Chamada.doutor_id, func.count())
            .where(
                Chamada.criada_em >= inicio_utc,
                Chamada.criada_em < fim_utc,
            )
            .group_by(Chamada.doutor_id)
        )
        return {int(did): int(quantidade) for did, quantidade in resultado.all()}


async def _somar_segundos_em_call_ao_vivo() -> dict[int, int]:
    """
    Segundos ainda não gravados no log: plantão ligado e cronômetro aberto.

    Como o ranking de moedas lê o saldo vivo em ``estado_plantao``, o ranking
    de horas em tempo real precisa enxergar o trecho corrente da call.
    Só conta ``segmento_iniciado_em`` → agora (mesmo critério usado ao sair
    da call para gravar ``duracao_segundos``). Quem está surdo/pausado não
    tem segmento aberto e não entra aqui.
    """
    agora_utc = datetime.now(timezone.utc)
    ao_vivo: dict[int, int] = {}
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(
                EstadoPlantao.toggle_ligado.is_(True),
                EstadoPlantao.em_call_valida.is_(True),
                EstadoPlantao.segmento_iniciado_em.is_not(None),
            )
        )
        for estado in resultado.scalars().all():
            inicio_segmento = estado.segmento_iniciado_em
            if inicio_segmento is None:
                continue
            if inicio_segmento.tzinfo is None:
                inicio_segmento = inicio_segmento.replace(tzinfo=timezone.utc)
            decorrido = int((agora_utc - inicio_segmento).total_seconds())
            if decorrido > 0:
                ao_vivo[int(estado.discord_id)] = decorrido
    return ao_vivo


async def buscar_horas_por_membro(
    inicio_utc: datetime,
    fim_utc: datetime,
    *,
    guild: discord.Guild | None = None,
    incluir_ao_vivo: bool = False,
) -> dict[int, int]:
    """
    Devolve ``{discord_id: segundos_totais}`` no intervalo pedido.

    Base (sempre):
    - Soma ``log_plantao.duracao_segundos`` dos eventos já fechados no período.

    Tempo real (``incluir_ao_vivo=True``), no mesmo espírito do ranking de moedas:
    - Soma também o trecho ainda aberto em call (não está no log até sair).
    - O loop de 1 min em ``ranking_plantao_tasks`` republica o card com esses totais.

    Filtro final (guild informada):
    - IDs em RANKING_HORAS_IDS_EXCLUIDOS
    - cargos em RANKING_HORAS_CARGOS_EXCLUIDOS
    - mesma elegibilidade do ranking de moedas (no servidor + hierarquia)
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(
                LogPlantao.discord_id,
                func.coalesce(func.sum(LogPlantao.duracao_segundos), 0),
            )
            .where(
                LogPlantao.criado_em >= inicio_utc,
                LogPlantao.criado_em < fim_utc,
                LogPlantao.duracao_segundos.is_not(None),
                LogPlantao.duracao_segundos > 0,
            )
            .group_by(LogPlantao.discord_id)
        )
        bruto = {int(did): int(segs) for did, segs in resultado.all() if int(segs) > 0}

    # Contagem ao vivo: só no ranking tempo real (não mistura no semanal/mensal oficial)
    if incluir_ao_vivo:
        for discord_id, segundos_abertos in (
            await _somar_segundos_em_call_ao_vivo()
        ).items():
            bruto[discord_id] = int(bruto.get(discord_id, 0)) + int(segundos_abertos)
            if bruto[discord_id] <= 0:
                bruto.pop(discord_id, None)

    return filtrar_participantes_ranking_horas(bruto, guild=guild)


def filtrar_participantes_ranking_horas(
    contagem: dict[int, int],
    *,
    guild: discord.Guild | None = None,
) -> dict[int, int]:
    """
    Aplica as mesmas regras do ranking de moedas, mais exclusões de horas.

    Fora do ranking:
    - IDs em RANKING_HORAS_IDS_EXCLUIDOS
    - quem saiu do servidor
    - quem não tem mais cargo da hierarquia (mesma regra das moedas)
    - cargos em RANKING_HORAS_CARGOS_EXCLUIDOS (ex.: Exonerado / Adv)
    """
    ids_bloqueados = {
        int(id_bloqueado) for id_bloqueado in (RANKING_HORAS_IDS_EXCLUIDOS or [])
    }
    cargos_bloqueados: set[int] = set()
    for nome in RANKING_HORAS_CARGOS_EXCLUIDOS or []:
        cargo_id = CARGOS.get(nome)
        if cargo_id is not None:
            cargos_bloqueados.add(int(cargo_id))

    filtrado: dict[int, int] = {}
    for discord_id, segundos in contagem.items():
        if int(discord_id) in ids_bloqueados:
            continue
        if guild is not None:
            membro = guild.get_member(int(discord_id))
            # Saiu do servidor OU sem hierarquia → fora (igual ranking de moedas)
            if not membro_elegivel_ranking_moedas(membro):
                continue
            if any(cargo.id in cargos_bloqueados for cargo in membro.roles):
                continue
        filtrado[int(discord_id)] = int(segundos)
    return filtrado


async def obter_segundos_plantao_totais(discord_id: int) -> int:
    """Soma de todas as durações de plantão do membro (banco de horas)."""
    async with async_session() as session:
        resultado = await session.execute(
            select(func.coalesce(func.sum(LogPlantao.duracao_segundos), 0)).where(
                LogPlantao.discord_id == int(discord_id),
                LogPlantao.duracao_segundos.is_not(None),
                LogPlantao.duracao_segundos > 0,
            )
        )
        valor = resultado.scalar_one()
        return int(valor or 0)


# ── Montagem de ranking genérico (valor numérico decrescente) ─────────────


def _montar_corpo_por_valor(
    contagem: dict[int, int],
    *,
    formatar_valor,
    label_singular: str,
    label_plural: str,
    vazio_msg: str,
    total_label: str,
) -> tuple[str, int]:
    """Retorna (corpo_texto, total_agregado). total = soma dos valores * pessoas?
    Para contagens: soma de qtd*pessoas; para segundos: soma dos segundos."""
    if not contagem:
        return (
            f"{vazio_msg}\n\n# 📌 **TOTAL GERAL**\n{total_label}: **0**",
            0,
        )

    por_valor: dict[int, list[int]] = defaultdict(list)
    for uid, valor in contagem.items():
        por_valor[valor].append(uid)

    blocos: list[str] = []
    posicao = 1
    total = 0

    for valor in sorted(por_valor.keys(), reverse=True):
        ids = sorted(por_valor[valor])
        medalha = _medalha(posicao)
        mencoes = _formatar_mencoes(ids)
        label = label_singular if valor == 1 else label_plural
        # horas: val é segundos; chamadas: val é quantidade
        blocos.append(f"{medalha} {mencoes}\n↳ **{formatar_valor(valor)}** {label}")
        total += valor * len(ids)
        posicao += len(ids)

    corpo = (
        "\n\n".join(blocos)
        + "\n\n"
        + "# 📌 **TOTAL GERAL**\n"
        + f"{total_label}: **{formatar_valor(total)}**"
    )
    return corpo, total


def montar_view_ranking_chamadas(
    contagem: dict[int, int],
    inicio_utc: datetime,
    fim_utc: datetime,
    *,
    periodo: str,  # semanal | mensal | tempo_real
    guild: discord.Guild | None = None,
    titulo_override: str | None = None,
) -> tuple[discord.ui.LayoutView, int]:
    """Monta o ranking visual de chamadas e calcula o total representado.

    Adapta título, período e cor ao modo semanal, mensal ou em tempo real. Devolve a
    view pronta para o Discord junto do total agregado, para que a publicação e o
    histórico usem exatamente a mesma contagem exibida ao servidor.
    """
    if periodo == "mensal":
        titulo = titulo_override or "🩺 **RANKING MENSAL DE CHAMADAS**"
        sub = (
            f"**Mês:** **{_formatar_mes_ano(inicio_utc)}** "
            f"({_formatar_data_curta(inicio_utc)} até {_formatar_data_curta(fim_utc)})"
        )
        cor = discord.Color.blue()
    elif periodo == "tempo_real":
        titulo = titulo_override or "🩺 **RANKING DE CHAMADAS — TEMPO REAL**"
        sub = (
            f"**Ciclo atual:** **{_formatar_data_curta(inicio_utc)} até "
            f"{_formatar_data_curta(fim_utc)}** _(parcial)_"
        )
        cor = discord.Color.blurple()
    else:
        titulo = titulo_override or "🩺 **RANKING SEMANAL DE CHAMADAS**"
        sub = (
            f"**Início:** **{_formatar_data_curta(inicio_utc)} até "
            f"{_formatar_data_curta(fim_utc)}**"
        )
        cor = discord.Color.dark_blue()

    cabecalho = (
        f"# {titulo}\n"
        f"# 📅 **Período**\n"
        f"{sub}\n"
        f"📋 Contagem de chamadas realizadas (tabela `chamadas`)"
    )
    corpo, total = _montar_corpo_por_valor(
        contagem,
        formatar_valor=lambda numero: str(numero),
        label_singular="chamada",
        label_plural="chamadas",
        vazio_msg="_Nenhuma chamada neste período._",
        total_label="🩺 **Chamadas realizadas**",
    )

    agora_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = (
        f"-# {guild.name} • <t:{agora_ts}:f>"
        if guild
        else f"-# Ranking • <t:{agora_ts}:f>"
    )
    icon_url = guild.icon.url if guild.icon else None

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.Section(
                cabecalho,
                accessory=discord.ui.Thumbnail(icon_url) if icon_url else None,
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(corpo),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(rodape),
            accent_color=cor,
        )
    )
    return view, total


def ordenar_ranking_individual(contagem: dict[int, int]) -> list[tuple[int, int]]:
    """Lista (discord_id, valor) ordenada do maior para o menor (desempate por id)."""
    return sorted(
        ((int(uid), int(valor)) for uid, valor in contagem.items() if int(valor) > 0),
        key=lambda par: (-par[1], par[0]),
    )


def premio_por_posicao(posicao: int) -> int:
    """Posição 1-based → valor do prêmio (0 se fora do top)."""
    if posicao < 1 or posicao > len(PREMIOS_RANKING_HORAS):
        return 0
    return int(PREMIOS_RANKING_HORAS[posicao - 1])


def total_premios_configurados() -> int:
    """Soma os prêmios configurados para exibir o compromisso financeiro do ranking."""
    return sum(int(valor_do_premio) for valor_do_premio in PREMIOS_RANKING_HORAS)


def montar_lista_premiados(
    contagem: dict[int, int],
) -> list[tuple[int, int, int, int]]:
    """
    Retorna lista de (posicao, discord_id, segundos, premio).
    Só quem entra no top de premiação (1..len PREMIOS).
    """
    ordenados = ordenar_ranking_individual(contagem)
    resultado: list[tuple[int, int, int, int]] = []
    for indice, (discord_id, segundos) in enumerate(ordenados):
        posicao = indice + 1
        premio = premio_por_posicao(posicao)
        if premio <= 0:
            break
        resultado.append((posicao, discord_id, segundos, premio))
    return resultado


def _montar_corpo_horas_com_premios(contagem: dict[int, int]) -> tuple[str, int]:
    """
    Corpo do ranking de horas com medalha, tempo e prêmio no top.
    total = soma dos segundos de todos.
    """
    ordenados = ordenar_ranking_individual(contagem)
    if not ordenados:
        return (
            "_Nenhum tempo de plantão registrado neste período._\n\n"
            "# 📌 **TOTAL GERAL**\n"
            "⏱️ **Tempo total da equipe**: **0**",
            0,
        )

    blocos: list[str] = []
    total = 0
    for indice, (discord_id, segundos) in enumerate(ordenados):
        posicao = indice + 1
        medalha = _medalha(posicao)
        premio = premio_por_posicao(posicao)
        total += segundos
        linha_tempo = f"↳ **{formatar_hms(segundos)}**"
        if premio > 0:
            linha_tempo += f" · 🏆 `{formatar_reais(premio)}`"
        blocos.append(f"{medalha} <@{discord_id}>\n{linha_tempo}")

    corpo = (
        "\n\n".join(blocos)
        + "\n\n"
        + "# 📌 **TOTAL GERAL**\n"
        + f"⏱️ **Tempo total da equipe**: **{formatar_hms(total)}**"
    )
    return corpo, total


def montar_view_ranking_horas(
    contagem_segundos: dict[int, int],
    inicio_utc: datetime,
    fim_utc: datetime,
    *,
    periodo: str,
    guild: discord.Guild | None = None,
    titulo_override: str | None = None,
) -> tuple[discord.ui.LayoutView, int]:
    """Monta o relatório visual de horas, destacando prêmios quando cabíveis.

    Recebe segundos já filtrados e devolve a view mais o total acumulado do período.
    Separar essa montagem da consulta preserva o mesmo critério de premiação nos cards
    em tempo real, semanais e mensais, sem misturar dados de membros excluídos.
    """
    total_premios = total_premios_configurados()
    linha_premio = (
        f"🏆 **Premiação configurada** · "
        f"**{formatar_reais(total_premios)}** em prêmios (Top 1 ao Top "
        f"{len(PREMIOS_RANKING_HORAS)})"
    )

    if periodo == "mensal":
        titulo = titulo_override or "⏱️ **RANKING MENSAL DE HORAS — PLANTÃO**"
        sub = (
            f"**Mês:** **{_formatar_mes_ano(inicio_utc)}** "
            f"({_formatar_data_curta(inicio_utc)} até {_formatar_data_curta(fim_utc)})"
        )
        cor = discord.Color.green()
        # Mensal: só relatório (sem foco em premiação semanal)
        linha_extra = "📊 Relatório mensal de tempo em call"
    elif periodo == "tempo_real":
        titulo = titulo_override or "⏱️ **RANKING DE HORAS — TEMPO REAL**"
        sub = (
            f"**Ciclo atual:** **{_formatar_data_curta(inicio_utc)} até "
            f"{_formatar_data_curta(fim_utc)}** _(parcial · atualiza a cada 1 min)_"
        )
        cor = discord.Color.dark_green()
        linha_extra = linha_premio
    else:
        titulo = titulo_override or "⏱️ **RANKING SEMANAL DE HORAS — PLANTÃO**"
        sub = (
            f"**Início:** **{_formatar_data_curta(inicio_utc)} até "
            f"{_formatar_data_curta(fim_utc)}**"
        )
        cor = discord.Color.brand_green()
        linha_extra = linha_premio

    cabecalho = f"# {titulo}\n# 📅 **Período**\n{sub}\n{linha_extra}"
    corpo, total = _montar_corpo_horas_com_premios(contagem_segundos)

    agora_ts = int(datetime.now(ZoneInfo("UTC")).timestamp())
    rodape = (
        f"-# Relatório · {guild.name} • <t:{agora_ts}:f>"
        if guild
        else f"-# Relatório • <t:{agora_ts}:f>"
    )
    icon_url = guild.icon.url if guild and guild.icon else None

    componentes: list = []
    if icon_url:
        componentes.append(
            discord.ui.Section(
                cabecalho,
                accessory=discord.ui.Thumbnail(icon_url),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(cabecalho))
    componentes.extend(
        [
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(corpo),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(rodape),
        ]
    )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            *componentes,
            accent_color=cor,
        )
    )
    return view, total


# ── Geração por tipo ─────────────────────────────────────────────────────


def _periodos(
    categoria: str,  # chamada | horas
    periodo: str,  # semanal | mensal | tempo_real
    referencia: datetime | None,
    modo_postagem: bool,
) -> tuple[datetime, datetime, str]:
    """Retorna inicio, fim, periodo_view."""
    agora_utc = datetime.now(ZoneInfo("UTC"))
    if periodo == "mensal":
        if modo_postagem:
            return (*obter_periodo_postagem_mensal(referencia), "mensal")
        inicio, _ = obter_periodo_ciclo_mensal(referencia)
        return inicio, agora_utc, "tempo_real"
    if periodo == "tempo_real":
        inicio, _ = obter_periodo_ciclo_semanal(referencia)
        return inicio, agora_utc, "tempo_real"
    # semanal
    if modo_postagem:
        return (*obter_periodo_postagem_semanal(referencia), "semanal")
    inicio, _ = obter_periodo_ciclo_semanal(referencia)
    return inicio, agora_utc, "tempo_real"


async def gerar_view_ranking_chamadas(
    periodo: str,
    *,
    guild: discord.Guild | None = None,
    referencia: datetime | None = None,
    modo_postagem: bool = False,
) -> tuple[discord.ui.LayoutView, dict[int, int], datetime, datetime, int]:
    """Reúne período, consulta e card de chamadas em uma saída pronta para publicar.

    ``modo_postagem`` seleciona um ciclo já encerrado, enquanto o padrão usa o período
    atual parcial. A tupla retorna também contagem e datas para salvar um histórico que
    corresponda fielmente ao card mostrado no Discord.
    """
    inicio, fim, periodo_view = _periodos("chamada", periodo, referencia, modo_postagem)
    contagem = await buscar_chamadas_por_doutor(inicio, fim)
    view, total = montar_view_ranking_chamadas(
        contagem, inicio, fim, periodo=periodo_view, guild=guild
    )
    return view, contagem, inicio, fim, total


async def gerar_view_ranking_horas(
    periodo: str,
    *,
    guild: discord.Guild | None = None,
    referencia: datetime | None = None,
    modo_postagem: bool = False,
) -> tuple[discord.ui.LayoutView, dict[int, int], datetime, datetime, int]:
    """Reúne período, filtro de participantes e card de horas para publicação.

    Usa a guilda, quando disponível, para remover ex-membros e quem perdeu
    cargo de hierarquia (mesma regra do ranking de moedas).

    Em ``periodo == "tempo_real"`` a contagem inclui o tempo ainda em call
    (trecho aberto), para o card acompanhar o minuto a minuto como o ranking
    de moedas acompanha o saldo. Semanal e mensal usam só logs fechados.

    A tupla inclui contagem e datas para o histórico bater com o card publicado.
    """
    inicio, fim, periodo_view = _periodos("horas", periodo, referencia, modo_postagem)
    # Tempo real = logs do ciclo + segundos ainda abertos em call (como saldo vivo)
    incluir_ao_vivo = periodo_view == "tempo_real" and not modo_postagem
    contagem = await buscar_horas_por_membro(
        inicio,
        fim,
        guild=guild,
        incluir_ao_vivo=incluir_ao_vivo,
    )
    view, total = montar_view_ranking_horas(
        contagem, inicio, fim, periodo=periodo_view, guild=guild
    )
    return view, contagem, inicio, fim, total


async def salvar_historico_plantao(
    *,
    tipo: str,
    inicio: datetime,
    fim: datetime,
    contagem: dict[int, int],
    total: int,
    channel_id: int | None = None,
    message_id: int | None = None,
) -> RankingHistorico:
    """Grava o resultado do ranking no banco, preservando a contagem em JSON.

    O tipo, intervalo e total descrevem o ciclo publicado; os identificadores de canal
    e mensagem mantêm um vínculo opcional com o Discord. Essa fotografia evita que
    rankings futuros alterem a referência histórica de horas ou chamadas passadas.
    """
    registro = RankingHistorico(
        tipo=tipo,
        periodo_inicio=inicio,
        periodo_fim=fim,
        total_recrutamentos=total,  # reutilizado: total chamadas ou total segundos
        total_pago=0,  # horas não têm pagamento; chamadas também 0 aqui
        payload_json=json.dumps(
            {
                str(id_do_membro): quantidade_contada
                for id_do_membro, quantidade_contada in contagem.items()
            }
        ),
        channel_id=channel_id,
        message_id=message_id,
        criado_em=agora(),
    )
    async with async_session() as session:
        session.add(registro)
        await session.commit()
        await session.refresh(registro)
    return registro


async def listar_historico_plantao(
    prefixo: str,
    limite: int = 10,
) -> list[RankingHistorico]:
    """prefixo: 'chamada' | 'horas'"""
    async with async_session() as session:
        resultado = await session.execute(
            select(RankingHistorico)
            .where(RankingHistorico.tipo.startswith(prefixo))
            .order_by(RankingHistorico.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())
