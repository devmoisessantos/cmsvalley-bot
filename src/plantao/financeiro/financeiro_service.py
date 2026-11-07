# src/plantao/financeiro/financeiro_service.py
"""
Análise financeira do plantão: horas válidas, moedas esperadas e extrato.

Reconstrói a linha do tempo do membro a partir de log_plantao e compara
com movimentacoes_moedas. Serve ao painel admin /plantao painel-financeiro.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from datetime import (
    datetime,
    timezone,
)
from typing import Any

from sqlalchemy import select

from src.config import VALOR_MOEDA_INGAME
from src.database.conexao import async_session
from src.database.models import (
    EstadoPlantao,
    LogPlantao,
    LoteMoeda,
    MovimentacaoMoeda,
)
from src.plantao.eventos_plantao import (
    CALL_ENCERRADA,
    CALL_ENCERRADA_POR_AFK,
    CICLO_MINUTO,
    CICLO_PERIODO,
    EVENTOS_AFK as EVENTOS_AFK_CANONICOS,
    EVENTOS_DURACAO_CALL_UTIL,
    HOUSEKEEPING,
    MOEDA_PAUSADA,
    SAIU_CALL,
    SAIU_CALL_VALIDA,
    TEMPO_MOEDA_FINALIZADO,
    TROCOU_CALL,
    TROCOU_CALL_VALIDA,
)
from src.plantao.financeiro.moeda_helper import (
    AnomaliaMoeda,
    ORDEM_SEVERIDADE,
    SEVERIDADE_CRITICO,
    SEVERIDADE_GRAVE,
    SEVERIDADE_LEVE,
    anomalia_acumulado,
    anomalia_credito_lote,
    credito_em_lote_e_invalido,
    pior_severidade,
)
from src.plantao.financeiro.regras_moeda_service import (
    segundos_para_moeda,
    teto_moedas_por_dia,
    teto_moedas_por_semana,
)
from src.utils.formatacao import (
    formatar_data_hora_local,
    formatar_hms,
)


# Eventos que fecham trecho contável (legado + nomes canônicos)
EVENTOS_COM_DURACAO_CALL = frozenset(
    set(EVENTOS_DURACAO_CALL_UTIL)
    | {
        CALL_ENCERRADA,
        CALL_ENCERRADA_POR_AFK,
        TROCOU_CALL,
        TROCOU_CALL_VALIDA,
        CICLO_MINUTO,
        CICLO_PERIODO,
        HOUSEKEEPING,
        "HOUSEKEEPING_CALL_ENCERRADA",
        MOEDA_PAUSADA,
        TEMPO_MOEDA_FINALIZADO,
        SAIU_CALL,
        SAIU_CALL_VALIDA,
    }
)

EVENTOS_AFK = frozenset(EVENTOS_AFK_CANONICOS)


@dataclass
class ResumoCarteiraMembro:
    """Snapshot da carteira e do estado de plantão de um membro."""

    discord_id: int
    id_fivem: str | None
    saldo_moedas: int
    segundos_acumulados: int
    progresso_proxima_moeda_segundos: int
    segundos_para_proxima_moeda: int
    moedas_ganhas_no_dia: int
    moedas_ganhas_na_semana: int
    data_moedas_dia: str | None
    chave_semana_moedas: str | None
    toggle_ligado: bool
    em_call_valida: bool
    canal_atual_id: int | None
    segmento_aberto_segundos: int
    afk_desde: datetime | None
    lotes: list[tuple[int, int]] = field(default_factory=list)
    valor_total_lotes: int = 0


@dataclass
class AnaliseCompletaMembro:
    """Resultado da reconciliação horas × moedas do membro."""

    discord_id: int
    id_fivem: str | None
    total_eventos_log: int
    primeiro_evento_em: datetime | None
    ultimo_evento_em: datetime | None
    segundos_em_call_registrados: int
    segundos_afk_registrados: int
    segundos_uteis_estimados: int
    moedas_esperadas_brutas: int
    moedas_ganhas_plantao_extrato: int
    moedas_outras_movimentacoes: int
    saldo_atual: int
    diferenca_esperada_vs_extrato: int
    avisos: list[str] = field(default_factory=list)
    extrato_recente: list[dict[str, Any]] = field(default_factory=list)
    eventos_resumo: list[dict[str, Any]] = field(default_factory=list)


async def buscar_discord_id_por_fivem(id_fivem: str) -> int | None:
    """Resolve Discord ID pelo ID FiveM gravado no estado de plantão."""
    texto = (id_fivem or "").strip()
    if not texto:
        return None
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao.discord_id).where(
                EstadoPlantao.id_fivem == texto
            )
        )
        return resultado.scalar_one_or_none()


async def buscar_estado_e_carteira(discord_id: int) -> ResumoCarteiraMembro | None:
    """Lê estado_plantao + lotes e monta o resumo da carteira."""
    from src.plantao.plantao_service import calcular_segundos_do_segmento_aberto

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(
                EstadoPlantao.discord_id == discord_id
            )
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return None

        resultado_lotes = await sessao.execute(
            select(LoteMoeda)
            .where(LoteMoeda.discord_id == discord_id)
            .where(LoteMoeda.quantidade > 0)
            .order_by(LoteMoeda.cotacao.asc())
        )
        lotes_db = list(resultado_lotes.scalars().all())

    lotes = [(int(lote.cotacao), int(lote.quantidade)) for lote in lotes_db]
    valor_total = sum(cot * qtd for cot, qtd in lotes)
    acumulado = int(estado.segundos_acumulados or 0)
    segmento_aberto = calcular_segundos_do_segmento_aberto(estado)
    progresso = acumulado + segmento_aberto
    resto_no_bloco = progresso % segundos_para_moeda()
    faltam = segundos_para_moeda() - resto_no_bloco
    if resto_no_bloco == 0 and progresso > 0:
        # Acabou de fechar um bloco; falta um bloco inteiro para a próxima
        faltam = segundos_para_moeda()

    return ResumoCarteiraMembro(
        discord_id=discord_id,
        id_fivem=estado.id_fivem,
        saldo_moedas=int(estado.saldo_moedas or 0),
        segundos_acumulados=acumulado,
        progresso_proxima_moeda_segundos=resto_no_bloco,
        segundos_para_proxima_moeda=faltam if progresso > 0 or acumulado > 0 else segundos_para_moeda(),
        moedas_ganhas_no_dia=int(estado.moedas_ganhas_no_dia or 0),
        moedas_ganhas_na_semana=int(estado.moedas_ganhas_na_semana or 0),
        data_moedas_dia=estado.data_moedas_dia,
        chave_semana_moedas=estado.chave_semana_moedas,
        toggle_ligado=bool(estado.toggle_ligado),
        em_call_valida=bool(estado.em_call_valida),
        canal_atual_id=estado.canal_atual_id,
        segmento_aberto_segundos=segmento_aberto,
        afk_desde=estado.afk_mudo_surdo_desde,
        lotes=lotes,
        valor_total_lotes=valor_total,
    )


async def listar_logs_plantao_membro(
    discord_id: int,
    *,
    limite: int | None = None,
) -> list[LogPlantao]:
    """Todos os eventos de log_plantao do membro, do mais antigo ao mais novo."""
    async with async_session() as sessao:
        consulta = (
            select(LogPlantao)
            .where(LogPlantao.discord_id == discord_id)
            .order_by(LogPlantao.criado_em.asc(), LogPlantao.id.asc())
        )
        if limite is not None and limite > 0:
            consulta = consulta.limit(limite)
        resultado = await sessao.execute(consulta)
        return list(resultado.scalars().all())


async def listar_movimentacoes_membro(
    discord_id: int,
    *,
    limite: int | None = None,
) -> list[MovimentacaoMoeda]:
    """Extrato completo (ou limitado) em ordem cronológica."""
    async with async_session() as sessao:
        consulta = (
            select(MovimentacaoMoeda)
            .where(MovimentacaoMoeda.discord_id == discord_id)
            .order_by(
                MovimentacaoMoeda.criado_em.asc(),
                MovimentacaoMoeda.id.asc(),
            )
        )
        if limite is not None and limite > 0:
            consulta = consulta.limit(limite)
        resultado = await sessao.execute(consulta)
        return list(resultado.scalars().all())


async def analisar_membro_completo(discord_id: int) -> AnaliseCompletaMembro:
    """
    Cruza log_plantao com o extrato de moedas.

    Não tem pressa: lê o histórico inteiro e calcula totais.
    A estimativa de moedas esperadas é bruta (segundos úteis / 1800),
    sem aplicar teto diário por dia civil — o painel avisa essa limitação.
    """
    logs = await listar_logs_plantao_membro(discord_id)
    movs = await listar_movimentacoes_membro(discord_id)

    async with async_session() as sessao:
        resultado_estado = await sessao.execute(
            select(EstadoPlantao).where(
                EstadoPlantao.discord_id == discord_id
            )
        )
        estado = resultado_estado.scalar_one_or_none()

    id_fivem = estado.id_fivem if estado else None
    saldo_atual = int(estado.saldo_moedas or 0) if estado else 0

    segundos_call = 0
    segundos_afk = 0
    logs_importados_ignorados = 0
    for log in logs:
        detalhes_txt = (log.detalhes or "").lower()
        # Importações do Discord duplicam duração dos eventos nativos.
        # Contar os dois infla horas e moedas esperadas.
        if detalhes_txt.startswith("import_log:"):
            logs_importados_ignorados += 1
            continue
        duracao = int(log.duracao_segundos or 0)
        if duracao <= 0:
            continue
        if log.evento in EVENTOS_AFK:
            segundos_afk += duracao
        if log.evento in EVENTOS_COM_DURACAO_CALL:
            # CICLO_MINUTO e saídas de call são tempo em call contável.
            # MOEDA_PAUSADA fecha o trecho antes do AFK — conta como útil
            # até a pausa; o AFK em si entra em segundos_afk.
            if log.evento != "CALL_ENCERRADA_POR_AFK":
                segundos_call += duracao

    # Tempo útil estimado: soma dos fechamentos de segmento (sem import_log).
    segundos_uteis = max(0, segundos_call)
    moedas_brutas = segundos_uteis // segundos_para_moeda()

    moedas_plantao = 0
    moedas_outras = 0
    moedas_em_lote_suspeito = 0
    creditos_lote_suspeitos = 0
    extrato_recente: list[dict[str, Any]] = []
    for mov in movs:
        valor = int(mov.valor or 0)
        tipo = (mov.tipo or "").upper()
        e_plantao = (
            tipo == "GANHO_PLANTAO"
            or "plantão" in (mov.referencia or "").lower()
            or "plantao" in (mov.referencia or "").lower()
        )
        if e_plantao:
            if valor > 0:
                moedas_plantao += valor
            # Crédito de 5+ de uma vez = padrão do bug de segundos_acumulados
            if valor >= 5:
                moedas_em_lote_suspeito += valor
                creditos_lote_suspeitos += 1
        else:
            moedas_outras += valor

        extrato_recente.append(
            {
                "id": mov.id,
                "tipo": mov.tipo,
                "valor": valor,
                "saldo_apos": int(mov.saldo_apos or 0),
                "referencia": mov.referencia,
                "criado_em": formatar_data_hora_local(mov.criado_em),
            }
        )

    # Mantém só as últimas 30 linhas no resumo visual; o YAML leva tudo
    extrato_para_card = extrato_recente[-30:]

    eventos_resumo: list[dict[str, Any]] = []
    for log in logs[-40:]:
        eventos_resumo.append(
            {
                "id": log.id,
                "evento": log.evento,
                "duracao_segundos": log.duracao_segundos,
                "canal_id": log.canal_id,
                "detalhes": log.detalhes,
                "criado_em": formatar_data_hora_local(log.criado_em),
            }
        )

    avisos: list[str] = []
    if logs_importados_ignorados > 0:
        avisos.append(
            f"{logs_importados_ignorados} log(s) `import_log` ignorados no "
            "cálculo de tempo (duplicata do evento nativo)."
        )

    diferenca = moedas_brutas - moedas_plantao
    if diferenca > 0:
        avisos.append(
            f"Extrato tem {diferenca} moeda(s) a menos que a estimativa "
            f"bruta (sem teto diário/semanal)."
        )
    elif diferenca < 0:
        avisos.append(
            f"Extrato tem {abs(diferenca)} moeda(s) a mais que a estimativa "
            f"bruta — possível crédito em lote ou ajuste manual."
        )

    if creditos_lote_suspeitos > 0:
        avisos.append(
            f"{creditos_lote_suspeitos} crédito(s) de plantão em lote "
            f"(≥5 de uma vez), total `{moedas_em_lote_suspeito}` moedas — "
            "padrão do bug de segundos_acumulados / teto."
        )

    if estado and int(estado.segundos_acumulados or 0) >= segundos_para_moeda():
        avisos.append(
            "segundos_acumulados ainda tem bloco completo pendente "
            f"({estado.segundos_acumulados}s). Após a correção do teto, "
            "isso não deveria mais gerar dump de 14 moedas."
        )

    primeiro = logs[0].criado_em if logs else None
    ultimo = logs[-1].criado_em if logs else None

    return AnaliseCompletaMembro(
        discord_id=discord_id,
        id_fivem=id_fivem,
        total_eventos_log=len(logs),
        primeiro_evento_em=primeiro,
        ultimo_evento_em=ultimo,
        segundos_em_call_registrados=segundos_call,
        segundos_afk_registrados=segundos_afk,
        segundos_uteis_estimados=segundos_uteis,
        moedas_esperadas_brutas=moedas_brutas,
        moedas_ganhas_plantao_extrato=moedas_plantao,
        moedas_outras_movimentacoes=moedas_outras,
        saldo_atual=saldo_atual,
        diferenca_esperada_vs_extrato=diferenca,
        avisos=avisos,
        extrato_recente=extrato_para_card,
        eventos_resumo=eventos_resumo,
    )


def montar_yaml_auditoria_membro(
    resumo: ResumoCarteiraMembro | None,
    analise: AnaliseCompletaMembro,
    logs: list[LogPlantao],
    movs: list[MovimentacaoMoeda],
) -> str:
    """
    Gera texto YAML legível com estado, análise, logs e extrato.

    Feito à mão (sem PyYAML) para não adicionar dependência.
    """

    def _escapar(texto: str | None) -> str:
        if texto is None:
            return '""'
        limpo = str(texto).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{limpo}"'

    def _dt(valor: datetime | None) -> str:
        if valor is None:
            return "null"
        if valor.tzinfo is None:
            valor = valor.replace(tzinfo=timezone.utc)
        return _escapar(valor.isoformat())

    linhas: list[str] = []
    linhas.append("# Auditoria financeira de plantão — CMS Valley")
    linhas.append(f"discord_id: {analise.discord_id}")
    linhas.append(f"id_fivem: {_escapar(analise.id_fivem)}")
    linhas.append("")
    linhas.append("regras:")
    linhas.append(f"  segundos_por_moeda: {segundos_para_moeda()}")
    linhas.append(f"  teto_moedas_por_dia: {teto_moedas_por_dia()}")
    linhas.append(f"  teto_moedas_por_semana: {teto_moedas_por_semana()}")
    linhas.append(f"  cotacao_minima: {VALOR_MOEDA_INGAME}")
    linhas.append("")
    linhas.append("analise:")
    linhas.append(f"  total_eventos_log: {analise.total_eventos_log}")
    linhas.append(f"  primeiro_evento_em: {_dt(analise.primeiro_evento_em)}")
    linhas.append(f"  ultimo_evento_em: {_dt(analise.ultimo_evento_em)}")
    linhas.append(
        f"  segundos_em_call_registrados: {analise.segundos_em_call_registrados}"
    )
    linhas.append(
        f"  segundos_em_call_hms: {_escapar(formatar_hms(analise.segundos_em_call_registrados))}"
    )
    linhas.append(
        f"  segundos_afk_registrados: {analise.segundos_afk_registrados}"
    )
    linhas.append(
        f"  segundos_uteis_estimados: {analise.segundos_uteis_estimados}"
    )
    linhas.append(
        f"  segundos_uteis_hms: {_escapar(formatar_hms(analise.segundos_uteis_estimados))}"
    )
    linhas.append(
        f"  moedas_esperadas_brutas: {analise.moedas_esperadas_brutas}"
    )
    linhas.append(
        f"  moedas_ganhas_plantao_extrato: {analise.moedas_ganhas_plantao_extrato}"
    )
    linhas.append(
        f"  moedas_outras_movimentacoes: {analise.moedas_outras_movimentacoes}"
    )
    linhas.append(f"  saldo_atual: {analise.saldo_atual}")
    linhas.append(
        f"  diferenca_esperada_vs_extrato: {analise.diferenca_esperada_vs_extrato}"
    )
    linhas.append("  avisos:")
    if analise.avisos:
        for aviso in analise.avisos:
            linhas.append(f"    - {_escapar(aviso)}")
    else:
        linhas.append("    []")
    linhas.append("")

    if resumo is not None:
        linhas.append("estado_atual:")
        linhas.append(f"  toggle_ligado: {str(resumo.toggle_ligado).lower()}")
        linhas.append(f"  em_call_valida: {str(resumo.em_call_valida).lower()}")
        linhas.append(f"  canal_atual_id: {resumo.canal_atual_id or 'null'}")
        linhas.append(f"  saldo_moedas: {resumo.saldo_moedas}")
        linhas.append(f"  segundos_acumulados: {resumo.segundos_acumulados}")
        linhas.append(
            f"  segmento_aberto_segundos: {resumo.segmento_aberto_segundos}"
        )
        linhas.append(
            f"  progresso_proxima_moeda_segundos: {resumo.progresso_proxima_moeda_segundos}"
        )
        linhas.append(
            f"  segundos_para_proxima_moeda: {resumo.segundos_para_proxima_moeda}"
        )
        linhas.append(f"  moedas_ganhas_no_dia: {resumo.moedas_ganhas_no_dia}")
        linhas.append(
            f"  moedas_ganhas_na_semana: {resumo.moedas_ganhas_na_semana}"
        )
        linhas.append(f"  data_moedas_dia: {_escapar(resumo.data_moedas_dia)}")
        linhas.append(
            f"  chave_semana_moedas: {_escapar(resumo.chave_semana_moedas)}"
        )
        linhas.append(f"  valor_total_lotes: {resumo.valor_total_lotes}")
        linhas.append("  lotes:")
        if resumo.lotes:
            for cotacao, quantidade in resumo.lotes:
                linhas.append(f"    - cotacao: {cotacao}")
                linhas.append(f"      quantidade: {quantidade}")
        else:
            linhas.append("    []")
        linhas.append("")

    linhas.append("logs_plantao:")
    if not logs:
        linhas.append("  []")
    for log in logs:
        linhas.append(f"  - id: {log.id}")
        linhas.append(f"    evento: {_escapar(log.evento)}")
        linhas.append(f"    criado_em: {_dt(log.criado_em)}")
        linhas.append(f"    canal_id: {log.canal_id or 'null'}")
        linhas.append(
            f"    duracao_segundos: {log.duracao_segundos if log.duracao_segundos is not None else 'null'}"
        )
        linhas.append(f"    detalhes: {_escapar(log.detalhes)}")
        linhas.append(f"    id_fivem: {_escapar(log.id_fivem)}")

    linhas.append("")
    linhas.append("movimentacoes_moedas:")
    if not movs:
        linhas.append("  []")
    for mov in movs:
        linhas.append(f"  - id: {mov.id}")
        linhas.append(f"    tipo: {_escapar(mov.tipo)}")
        linhas.append(f"    valor: {int(mov.valor or 0)}")
        linhas.append(f"    saldo_apos: {int(mov.saldo_apos or 0)}")
        linhas.append(f"    referencia: {_escapar(mov.referencia)}")
        linhas.append(
            f"    outro_discord_id: {mov.outro_discord_id or 'null'}"
        )
        linhas.append(f"    criado_em: {_dt(mov.criado_em)}")

    linhas.append("")
    return "\n".join(linhas)


async def gerar_yaml_completo_membro(discord_id: int) -> str:
    """Carrega tudo e devolve o YAML de auditoria."""
    resumo = await buscar_estado_e_carteira(discord_id)
    analise = await analisar_membro_completo(discord_id)
    logs = await listar_logs_plantao_membro(discord_id)
    movs = await listar_movimentacoes_membro(discord_id)
    return montar_yaml_auditoria_membro(resumo, analise, logs, movs)


@dataclass
class AlertaAcumuladoTravado:
    """Membro com blocos completos parados em segundos_acumulados."""

    discord_id: int
    id_fivem: str | None
    segundos_acumulados: int
    blocos_completos: int
    resto_segundos: int
    saldo_moedas: int
    toggle_ligado: bool
    em_call_valida: bool


@dataclass
class SuspeitoDumpMoedas:
    """Dia em que o membro creditou muitas moedas de plantão de uma vez."""

    discord_id: int
    data_local: str
    moedas_no_dia: int
    primeira_credito_em: str | None
    ultima_credito_em: str | None
    saldo_apos_ultimo: int


async def listar_alertas_acumulado_travado(
    limite: int = 40,
) -> list[AlertaAcumuladoTravado]:
    """
    Quem tem segundos_acumulados >= 1 bloco completo parado no banco.

    Depois da correção do crédito, isso não deveria crescer; a lista
    serve para limpar resíduos do bug antigo.
    """
    limite_bloco = segundos_para_moeda()
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao)
            .where(EstadoPlantao.segundos_acumulados >= limite_bloco)
            .order_by(EstadoPlantao.segundos_acumulados.desc())
            .limit(limite)
        )
        estados = list(resultado.scalars().all())

    alertas: list[AlertaAcumuladoTravado] = []
    for estado in estados:
        acumulado = int(estado.segundos_acumulados or 0)
        alertas.append(
            AlertaAcumuladoTravado(
                discord_id=int(estado.discord_id),
                id_fivem=estado.id_fivem,
                segundos_acumulados=acumulado,
                blocos_completos=acumulado // limite_bloco,
                resto_segundos=acumulado % limite_bloco,
                saldo_moedas=int(estado.saldo_moedas or 0),
                toggle_ligado=bool(estado.toggle_ligado),
                em_call_valida=bool(estado.em_call_valida),
            )
        )
    return alertas


async def listar_suspeitos_dump_moedas(
    *,
    minimo_moedas_dia: int | None = None,
    limite: int = 50,
) -> list[SuspeitoDumpMoedas]:
    """
    Lista dias em que o membro ganhou muitas moedas de plantão no extrato.

    Heurística do bug antigo: creditou perto do teto diário em um único dia.
    Não prova fraude sozinha — é fila para revisão humana.
    """
    from collections import defaultdict

    teto = minimo_moedas_dia if minimo_moedas_dia is not None else teto_moedas_por_dia()

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(MovimentacaoMoeda)
            .where(MovimentacaoMoeda.valor > 0)
            .order_by(MovimentacaoMoeda.criado_em.asc())
        )
        movs = list(resultado.scalars().all())

    # Agrupa por (discord_id, data local YYYY-MM-DD)
    por_dia: dict[tuple[int, str], list[MovimentacaoMoeda]] = defaultdict(list)
    for mov in movs:
        tipo = (mov.tipo or "").upper()
        ref = (mov.referencia or "").lower()
        e_plantao = tipo == "GANHO_PLANTAO" or "plantão" in ref or "plantao" in ref
        if not e_plantao:
            continue
        criado = mov.criado_em
        if criado is None:
            continue
        if criado.tzinfo is None:
            criado = criado.replace(tzinfo=timezone.utc)
        data_local = formatar_data_hora_local(criado)[:10]
        if not data_local or data_local.startswith("—"):
            data_local = criado.date().isoformat()
        por_dia[(int(mov.discord_id), data_local)].append(mov)

    suspeitos: list[SuspeitoDumpMoedas] = []
    for (discord_id, data_local), lista in por_dia.items():
        total = sum(int(m.valor or 0) for m in lista)
        if total < teto:
            continue
        primeiro = lista[0]
        ultimo = lista[-1]
        suspeitos.append(
            SuspeitoDumpMoedas(
                discord_id=discord_id,
                data_local=data_local,
                moedas_no_dia=total,
                primeira_credito_em=formatar_data_hora_local(primeiro.criado_em),
                ultima_credito_em=formatar_data_hora_local(ultimo.criado_em),
                saldo_apos_ultimo=int(ultimo.saldo_apos or 0),
            )
        )

    suspeitos.sort(key=lambda item: (-item.moedas_no_dia, item.data_local))
    return suspeitos[:limite]



@dataclass
class MembroIrregular:
    """Resumo de anomalias de um membro, com a pior severidade."""

    discord_id: int
    id_fivem: str | None
    severidade: str
    segundos_acumulados: int
    saldo_moedas: int
    qtd_creditos_lote: int
    moedas_em_lote: int
    anomalias: list[AnomaliaMoeda] = field(default_factory=list)


async def listar_membros_irregulares(
    *,
    limite: int = 60,
) -> list[MembroIrregular]:
    """
    Varre estado + extrato e devolve membros com falha de regra.

    Severidade:
    - LEVE: 1 bloco parado ou lote pequeno (>=5 e <10)
    - GRAVE: 2+ blocos parados ou lote 10–13
    - CRITICO: 10+ blocos parados ou lote >= teto diário
    """
    limite_bloco = segundos_para_moeda()
    membros: dict[int, MembroIrregular] = {}

    async with async_session() as sessao:
        resultado_estado = await sessao.execute(select(EstadoPlantao))
        estados = list(resultado_estado.scalars().all())

        resultado_mov = await sessao.execute(
            select(MovimentacaoMoeda)
            .where(MovimentacaoMoeda.valor >= 5)
            .order_by(MovimentacaoMoeda.criado_em.asc())
        )
        movs = list(resultado_mov.scalars().all())

    for estado in estados:
        discord_id = int(estado.discord_id)
        acumulado = int(estado.segundos_acumulados or 0)
        achado = anomalia_acumulado(discord_id, acumulado)
        if achado is None:
            continue
        membro = membros.get(discord_id)
        if membro is None:
            membro = MembroIrregular(
                discord_id=discord_id,
                id_fivem=estado.id_fivem,
                severidade=achado.severidade,
                segundos_acumulados=acumulado,
                saldo_moedas=int(estado.saldo_moedas or 0),
                qtd_creditos_lote=0,
                moedas_em_lote=0,
                anomalias=[],
            )
            membros[discord_id] = membro
        membro.anomalias.append(achado)
        membro.severidade = pior_severidade(
            [membro.severidade, achado.severidade]
        ) or achado.severidade
        membro.segundos_acumulados = acumulado

    for mov in movs:
        tipo = (mov.tipo or "").upper()
        ref = (mov.referencia or "").lower()
        e_plantao = (
            tipo == "GANHO_PLANTAO"
            or "plantão" in ref
            or "plantao" in ref
        )
        if not e_plantao:
            continue
        quantidade = int(mov.valor or 0)
        if not credito_em_lote_e_invalido(quantidade):
            continue
        discord_id = int(mov.discord_id)
        achado = anomalia_credito_lote(
            discord_id,
            quantidade,
            movimento_id=int(mov.id) if mov.id else None,
            criado_em=formatar_data_hora_local(mov.criado_em),
        )
        if achado is None:
            continue
        membro = membros.get(discord_id)
        if membro is None:
            membro = MembroIrregular(
                discord_id=discord_id,
                id_fivem=None,
                severidade=achado.severidade,
                segundos_acumulados=0,
                saldo_moedas=int(mov.saldo_apos or 0),
                qtd_creditos_lote=0,
                moedas_em_lote=0,
                anomalias=[],
            )
            membros[discord_id] = membro
        membro.anomalias.append(achado)
        membro.qtd_creditos_lote += 1
        membro.moedas_em_lote += quantidade
        membro.severidade = pior_severidade(
            [membro.severidade, achado.severidade]
        ) or achado.severidade

    lista = list(membros.values())
    lista.sort(
        key=lambda item: (
            -ORDEM_SEVERIDADE.get(item.severidade, 0),
            -item.segundos_acumulados,
            -item.moedas_em_lote,
            item.discord_id,
        )
    )
    return lista[:limite]



def filtrar_irregulares_da_hierarquia(
    irregulares: list[MembroIrregular],
    ids_na_hierarquia: set[int],
) -> list[MembroIrregular]:
    """Mantém só quem ainda está na hierarquia do servidor."""
    if not ids_na_hierarquia:
        return list(irregulares)
    return [
        item
        for item in irregulares
        if int(item.discord_id) in ids_na_hierarquia
    ]
