# src/plantao/eventos_plantao.py
"""
Constantes de eventos de plantão e carteira.

Um único lugar para o nome de cada evento. Logs, logger visual e análise
financeira usam estas constantes — evita string solta e tipagem errada.

Histórico antigo pode ter nomes legados (ex.: ENTROU_CALL). A análise
aceita o nome atual e o legado via ALIASES_EVENTO.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Serviço (toggle)
# ---------------------------------------------------------------------------
TOGGLE_ON = "TOGGLE_ON"
TOGGLE_OFF = "TOGGLE_OFF"
OCIOSO_ENCERRADO = "OCIOSO_ENCERRADO"
DESLIGAMENTO_AUTOMATICO = "DESLIGAMENTO_AUTOMATICO"
HOUSEKEEPING = "HOUSEKEEPING"

# ---------------------------------------------------------------------------
# Call válida (voz que conta para moeda)
# ---------------------------------------------------------------------------
ENTROU_CALL_VALIDA = "ENTROU_CALL_VALIDA"
SAIU_CALL_VALIDA = "SAIU_CALL_VALIDA"
TROCOU_CALL_VALIDA = "TROCOU_CALL_VALIDA"
# Legado (banco antigo / import)
ENTROU_CALL = "ENTROU_CALL"
SAIU_CALL = "SAIU_CALL"
TROCOU_CALL = "TROCOU_CALL"
CALL_ENCERRADA = "CALL_ENCERRADA"

# ---------------------------------------------------------------------------
# Tempo de moeda (ciclo de crédito)
# ---------------------------------------------------------------------------
TEMPO_MOEDA_INICIADO = "TEMPO_MOEDA_INICIADO"
TEMPO_MOEDA_FINALIZADO = "TEMPO_MOEDA_FINALIZADO"
MOEDA_CREDITADA = "MOEDA_CREDITADA"
CICLO_MINUTO = "CICLO_MINUTO"
CICLO_PERIODO = "CICLO_PERIODO"

# ---------------------------------------------------------------------------
# AFK / pausa de moeda (surdo = tempo não conta)
# ---------------------------------------------------------------------------
MOEDA_PAUSADA = "MOEDA_PAUSADA"
MOEDA_RETOMADA = "MOEDA_RETOMADA"
MEMBRO_AFK = "MEMBRO_AFK"
AFK_AVISO = "AFK_AVISO"
CALL_ENCERRADA_POR_AFK = "CALL_ENCERRADA_POR_AFK"
PENALIDADE_AFK = "PENALIDADE_AFK"

# ---------------------------------------------------------------------------
# Lembretes de inatividade
# ---------------------------------------------------------------------------
LEMBRETE_10 = "LEMBRETE_10"
LEMBRETE_15 = "LEMBRETE_15"
LEMBRETE_25 = "LEMBRETE_25"

# ---------------------------------------------------------------------------
# Carteira / financeiro
# ---------------------------------------------------------------------------
TROCA_MOEDAS_SOLICITADA = "TROCA_MOEDAS_SOLICITADA"
AJUSTE_SALDO_STAFF = "AJUSTE_SALDO_STAFF"
RECALCULO_MOEDAS = "RECALCULO_MOEDAS"
LIMPEZA_ACUMULADO = "LIMPEZA_ACUMULADO"

# ---------------------------------------------------------------------------
# Conjuntos para análise e crédito
# ---------------------------------------------------------------------------

# Tempo em call que entra no cálculo de segundos úteis
EVENTOS_DURACAO_CALL_UTIL = frozenset(
    {
        SAIU_CALL,
        SAIU_CALL_VALIDA,
        CALL_ENCERRADA,
        CICLO_MINUTO,
        CICLO_PERIODO,
        MOEDA_PAUSADA,
        TEMPO_MOEDA_FINALIZADO,
    }
)

# Trechos em que o tempo NÃO conta para moeda
EVENTOS_AFK = frozenset(
    {
        AFK_AVISO,
        CALL_ENCERRADA_POR_AFK,
        PENALIDADE_AFK,
        MOEDA_PAUSADA,
        MEMBRO_AFK,
    }
)

# Eventos que o log visual manda para LOG_CARTEIRA
EVENTOS_LOG_CARTEIRA = frozenset(
    {
        MOEDA_CREDITADA,
        TROCA_MOEDAS_SOLICITADA,
        AJUSTE_SALDO_STAFF,
        RECALCULO_MOEDAS,
        LIMPEZA_ACUMULADO,
        TEMPO_MOEDA_INICIADO,
        TEMPO_MOEDA_FINALIZADO,
    }
)

# Nome legado → nome preferido (análise trata como o mesmo fato)
ALIASES_EVENTO: dict[str, str] = {
    ENTROU_CALL: ENTROU_CALL_VALIDA,
    SAIU_CALL: SAIU_CALL_VALIDA,
    TROCOU_CALL: TROCOU_CALL_VALIDA,
}


def normalizar_evento(nome: str | None) -> str:
    """Devolve o nome canônico do evento (legado vira o nome novo)."""
    if not nome:
        return ""
    texto = str(nome).strip().upper()
    return ALIASES_EVENTO.get(texto, texto)
