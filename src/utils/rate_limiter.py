# src/utils/rate_limiter.py
"""
Detecta remoções de cargo em massa em pouco tempo.

Não usa banco de dados: é só um histórico em memória de curto prazo.
Se alguém remover muitos cargos rápido, devolve a lista para o bot reverter.
"""

import time

from src.config import (
    JANELA_TEMPO_SUSPEITA_SEGUNDOS,
    LIMITE_REMOCOES_SUSPEITAS,
)

# Estrutura em memória:
# { id_do_executor: [(timestamp, id_do_candidato, id_do_cargo, nome_do_cargo), ...] }
_historico_de_remocoes: dict[int, list[tuple[float, int, int, str]]] = {}


def registrar_remocao(
    executor_id: int,
    candidato_id: int,
    cargo_id: int,
    nome_cargo: str,
):
    """
    Registra uma remoção de cargo.

    Se o executor ultrapassar o limite dentro da janela de tempo,
    retorna a lista das remoções recentes (para o sistema reverter).
    Caso contrário, retorna None.
    """
    momento_atual = time.monotonic()
    historico_do_executor = _historico_de_remocoes.setdefault(executor_id, [])

    # Mantém só as entradas que ainda estão dentro da janela de tempo.
    historico_do_executor[:] = [
        entrada
        for entrada in historico_do_executor
        if momento_atual - entrada[0] <= JANELA_TEMPO_SUSPEITA_SEGUNDOS
    ]

    historico_do_executor.append((momento_atual, candidato_id, cargo_id, nome_cargo))

    atingiu_o_limite = len(historico_do_executor) >= LIMITE_REMOCOES_SUSPEITAS

    if atingiu_o_limite:
        remocoes_recentes = list(historico_do_executor)
        historico_do_executor.clear()
        return remocoes_recentes

    return None
