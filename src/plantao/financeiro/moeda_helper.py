# src/plantao/financeiro/moeda_helper.py
"""
Regras cirúrgicas de moeda do plantão.

Funções puras (sem I/O) para:
- validar quantas moedas um crédito pode emitir de uma vez
- classificar anomalia por severidade (LEVE / GRAVE / CRITICO)
- descrever correção sugerida

O crédito em runtime continua em plantao_service._creditar_moedas_de_acumulado.
Este helper é a prova de falha usada pelo painel e pela auditoria.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.plantao.financeiro.regras_moeda_service import (
    segundos_para_moeda,
    teto_moedas_por_dia,
)

# ---------------------------------------------------------------------------
# Severidade
# ---------------------------------------------------------------------------
SEVERIDADE_LEVE = "LEVE"
SEVERIDADE_GRAVE = "GRAVE"
SEVERIDADE_CRITICO = "CRITICO"

ORDEM_SEVERIDADE = {
    SEVERIDADE_LEVE: 1,
    SEVERIDADE_GRAVE: 2,
    SEVERIDADE_CRITICO: 3,
}

# Limites em múltiplos de segundos_para_moeda()
# LEVE: 1 bloco completo parado
# GRAVE: >= 2 blocos
# CRITICO: >= 10 blocos (ou dump de teto diário inteiro)
MULTIPLOS_LEVE = 1
MULTIPLOS_GRAVE = 2
MULTIPLOS_CRITICO = 10

# Crédito em lote no extrato
LOTE_MINIMO_SUSPEITO = 5


@dataclass(frozen=True)
class AnomaliaMoeda:
    """Um achado auditável sobre um membro."""

    discord_id: int
    severidade: str
    codigo: str
    titulo: str
    detalhe: str
    correcao_sugerida: str
    valor_referencia: int = 0


def maximo_moedas_por_credito_unico() -> int:
    """
    Teto rígido: um único crédito nunca passa do teto diário.

    Na regra correta o ciclo emite 1 moeda por vez. Qualquer crédito
    com valor > 1 já é desvio (legado do bug de acumulado).
    """
    return int(teto_moedas_por_dia())


def credito_em_lote_e_invalido(quantidade: int) -> bool:
    """True se a quantidade creditada de uma vez viola a regra atual."""
    if quantidade <= 0:
        return False
    # Regra desejada: 1 moeda por fechamento de bloco.
    # Lotes >= LOTE_MINIMO_SUSPEITO são o padrão histórico do bug.
    if quantidade >= LOTE_MINIMO_SUSPEITO:
        return True
    if quantidade > maximo_moedas_por_credito_unico():
        return True
    return False


def severidade_por_segundos_acumulados(segundos: int) -> str | None:
    """
    Classifica segundos_acumulados travados no estado.

    None = dentro do progresso normal (< 1 bloco completo).
    """
    limite = segundos_para_moeda()
    if limite <= 0:
        return None
    segundos_inteiros = max(0, int(segundos or 0))
    if segundos_inteiros < limite:
        return None
    blocos = segundos_inteiros // limite
    if blocos >= MULTIPLOS_CRITICO:
        return SEVERIDADE_CRITICO
    if blocos >= MULTIPLOS_GRAVE:
        return SEVERIDADE_GRAVE
    return SEVERIDADE_LEVE


def severidade_por_credito_lote(quantidade: int) -> str | None:
    """Severidade de um único crédito em lote no extrato."""
    quantidade_inteira = int(quantidade or 0)
    if quantidade_inteira < LOTE_MINIMO_SUSPEITO:
        return None
    teto = maximo_moedas_por_credito_unico()
    if quantidade_inteira >= teto:
        return SEVERIDADE_CRITICO
    if quantidade_inteira >= 10:
        return SEVERIDADE_GRAVE
    return SEVERIDADE_LEVE


def anomalia_acumulado(
    discord_id: int,
    segundos_acumulados: int,
) -> AnomaliaMoeda | None:
    """Monta anomalia se o acumulado tem bloco completo parado."""
    severidade = severidade_por_segundos_acumulados(segundos_acumulados)
    if severidade is None:
        return None
    limite = segundos_para_moeda()
    blocos = int(segundos_acumulados) // limite
    resto = int(segundos_acumulados) % limite
    return AnomaliaMoeda(
        discord_id=discord_id,
        severidade=severidade,
        codigo="ACUMULADO_TRAVADO",
        titulo="Segundos acumulados com bloco completo",
        detalhe=(
            f"`{segundos_acumulados}` s "
            f"({blocos} bloco(s) de {limite}s + resto {resto}s). "
            "Não deve existir bloco completo sem crédito ou descarte."
        ),
        correcao_sugerida=(
            "Limpar acumulado no painel (descarta blocos completos, "
            "mantém só o resto incompleto). Não creditar os blocos."
        ),
        valor_referencia=int(segundos_acumulados),
    )


def anomalia_credito_lote(
    discord_id: int,
    quantidade: int,
    movimento_id: int | None = None,
    criado_em: str | None = None,
) -> AnomaliaMoeda | None:
    """Monta anomalia se o crédito do extrato veio em lote."""
    severidade = severidade_por_credito_lote(quantidade)
    if severidade is None:
        return None
    quando = criado_em or "—"
    id_txt = f"#{movimento_id}" if movimento_id else "—"
    return AnomaliaMoeda(
        discord_id=discord_id,
        severidade=severidade,
        codigo="CREDITO_EM_LOTE",
        titulo="Crédito de plantão em lote",
        detalhe=(
            f"Movimento {id_txt} em {quando}: "
            f"`+{quantidade}` moeda(s) de uma vez. "
            "Regra atual: 1 moeda por bloco de tempo."
        ),
        correcao_sugerida=(
            "Registrar na fila de dump. Ajuste manual só com decisão "
            "da diretoria (motivo obrigatório + auditoria)."
        ),
        valor_referencia=int(quantidade),
    )


def pior_severidade(lista: list[str]) -> str | None:
    """Devolve a severidade mais alta da lista."""
    if not lista:
        return None
    return max(lista, key=lambda s: ORDEM_SEVERIDADE.get(s, 0))


def progresso_proxima_moeda(segundos_acumulados: int) -> tuple[int, int]:
    """
    (progresso no bloco atual, segundos que faltam).

    Usa só o resto incompleto — blocos completos não contam como progresso.
    """
    limite = segundos_para_moeda()
    if limite <= 0:
        return 0, 0
    resto = int(segundos_acumulados or 0) % limite
    faltam = limite - resto if resto > 0 else limite
    if resto == 0 and int(segundos_acumulados or 0) == 0:
        faltam = limite
    return resto, faltam
