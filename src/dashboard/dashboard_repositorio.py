"""
Metas por cargo: aplicação em memória e leitura do banco.

O dashboard web grava as metas na tabela `metas_por_cargo`. O bot precisa
delas para calcular promoção:

- `aplicar_metas_por_cargo()` recebe as linhas (da API ou do banco) e atualiza
  `METAS_POR_CARGO` e `TRILHAS_PROMOCAO` em memória.
- `sincronizar_metas_do_banco()` lê a tabela direto, para quando a API está
  fora do ar no momento em que o bot sobe.

Antes, a única fonte era a API: restart com a API fora voltava para o
`src/config.py` e as metas editadas no site sumiam até o próximo sync.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from src.config import (
    CARGO_DOUTOR,
    CARGO_INSTRUTOR,
    CARGO_PSICOLOGO,
    CARGO_RECRUTADOR,
    METAS_POR_CARGO,
    TRILHAS_PROMOCAO,
)
from src.database.conexao import async_session
from src.database.models import MetaPorCargo

registrador = logging.getLogger(__name__)

# Chaves de produção que vêm do dashboard (as demais ficam como estão).
CHAVES_DE_PRODUCAO = (
    "meta_laudos",
    "meta_recrutamentos",
    "meta_chamadas",
    "meta_cursos_aplicados",
    "meta_tickets",
)

# Formato de um cargo novo, criado quando o dashboard envia um cargo que o
# config.py ainda não conhece.
CARGO_VAZIO = {
    "segundos_minimos_plantao": 0,
    "meta_laudos": 0,
    "meta_recrutamentos": 0,
    "meta_chamadas": 0,
    "meta_cursos_aplicados": 0,
    "meta_tickets": 0,
    "exige_avaliacao_hp": False,
}


def _garantir_cargo(nome_do_cargo: str) -> dict:
    """Devolve as metas do cargo, criando o registro zerado se faltar."""
    if nome_do_cargo not in METAS_POR_CARGO:
        METAS_POR_CARGO[nome_do_cargo] = dict(CARGO_VAZIO)
    return METAS_POR_CARGO[nome_do_cargo]


def _aplicar_linha(linha: dict) -> str | None:
    """Aplica uma linha de meta e devolve o cargo atualizado (ou None)."""
    nome_do_cargo = linha.get("cargo_nome")
    if not nome_do_cargo:
        return None

    metas = _garantir_cargo(nome_do_cargo)
    metas["segundos_minimos_plantao"] = int(
        linha.get("segundos_minimos_plantao") or 0
    )
    for chave in CHAVES_DE_PRODUCAO:
        metas[chave] = int(linha.get(chave) or 0)
    metas["exige_avaliacao_hp"] = bool(linha.get("exige_avaliacao_hp") or False)
    return nome_do_cargo


def _aplicar_trilhas() -> None:
    """
    Copia as metas de produção para as trilhas de promoção.

    Trilha de primeira área e área → área não cobram produção: mexer nelas
    mudaria a regra de promoção, então ficam de fora.
    """
    for trilha in TRILHAS_PROMOCAO:
        if trilha.get("primeira_area"):
            continue
        observacao = (trilha.get("observacao") or "").lower()
        if "entre áreas" in observacao or "área → área" in observacao:
            continue

        if trilha.get("exige_todas_as_areas"):
            _aplicar_trilha_do_supervisor(trilha)
            continue

        destino = trilha.get("para_cargo")
        if not destino or destino not in METAS_POR_CARGO:
            continue
        base = METAS_POR_CARGO[destino]
        for chave in CHAVES_DE_PRODUCAO:
            trilha[chave] = int(base.get(chave) or 0)
        trilha["exige_avaliacao_hp"] = bool(base.get("exige_avaliacao_hp") or False)


def _aplicar_trilha_do_supervisor(trilha: dict) -> None:
    """Supervisor cobra a soma das quatro especialidades."""
    trilha["meta_laudos"] = int(
        METAS_POR_CARGO.get(CARGO_PSICOLOGO, {}).get("meta_laudos") or 0
    )
    trilha["meta_recrutamentos"] = int(
        METAS_POR_CARGO.get(CARGO_RECRUTADOR, {}).get("meta_recrutamentos") or 0
    )
    trilha["meta_chamadas"] = int(
        METAS_POR_CARGO.get(CARGO_DOUTOR, {}).get("meta_chamadas") or 0
    )
    trilha["meta_cursos_aplicados"] = int(
        METAS_POR_CARGO.get(CARGO_INSTRUTOR, {}).get("meta_cursos_aplicados") or 0
    )


def aplicar_metas_por_cargo(linhas: list[dict]) -> int:
    """Aplica as linhas em METAS_POR_CARGO + TRILHAS_PROMOCAO."""
    atualizados = 0
    for linha in linhas:
        if _aplicar_linha(linha) is not None:
            atualizados += 1

    if atualizados:
        _aplicar_trilhas()

    registrador.info(
        "Metas por cargo sincronizadas do dashboard (%s cargos).",
        atualizados,
    )
    return atualizados


async def sincronizar_metas_do_banco() -> int:
    """
    Lê `metas_por_cargo` direto do banco e aplica.

    Usada quando a API não responde na subida: as metas editadas no site
    continuam valendo sem depender da API estar no ar.
    """
    try:
        async with async_session() as sessao:
            resultado = await sessao.execute(select(MetaPorCargo))
            linhas = [registro.como_dicionario() for registro in resultado.scalars()]
    except Exception as falha:  # noqa: BLE001
        registrador.warning(
            "Não consegui ler metas_por_cargo do banco: %s", falha
        )
        return 0

    if not linhas:
        return 0

    return aplicar_metas_por_cargo(linhas)
