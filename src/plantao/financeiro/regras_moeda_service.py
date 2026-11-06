# src/plantao/financeiro/regras_moeda_service.py
"""
Regras de recompensa de moeda do plantão.

Valores ficam em cache em memória (leitura síncrona no crédito de moeda)
e são persistidos na tabela regras_moeda_plantao. Na ausência de linha
no banco, usa os padrões de src.config.
"""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)

from sqlalchemy import select

from src.config import (
    HORA_LIMITE_MOEDA_SABADO,
    SEGUNDOS_PARA_MOEDA,
    TETO_MOEDAS_POR_DIA,
    TETO_MOEDAS_POR_SEMANA,
)
from src.database.conexao import async_session
from src.database.models import RegraMoedaPlantao

registrador = logging.getLogger(__name__)

# Cache síncrono — o crédito de moeda roda em funções síncronas.
_segundos_para_moeda: int = int(SEGUNDOS_PARA_MOEDA)
_teto_moedas_por_dia: int = int(TETO_MOEDAS_POR_DIA)
_teto_moedas_por_semana: int = int(TETO_MOEDAS_POR_SEMANA)
_hora_limite_moeda_sabado: int = int(HORA_LIMITE_MOEDA_SABADO)
_atualizado_em: datetime | None = None
_atualizado_por: int | None = None
_carregado: bool = False


def segundos_para_moeda() -> int:
    """Segundos de call válida para ganhar 1 moeda."""
    return int(_segundos_para_moeda)


def teto_moedas_por_dia() -> int:
    """Máximo de moedas por dia civil (fuso do hospital)."""
    return int(_teto_moedas_por_dia)


def teto_moedas_por_semana() -> int:
    """Máximo de moedas por semana ISO."""
    return int(_teto_moedas_por_semana)


def hora_limite_moeda_sabado() -> int:
    """A partir desta hora no sábado, não gera moeda."""
    return int(_hora_limite_moeda_sabado)


def resumo_regras() -> dict[str, int | str | None]:
    """Dicionário legível com o estado atual do cache."""
    return {
        "segundos_para_moeda": segundos_para_moeda(),
        "teto_moedas_por_dia": teto_moedas_por_dia(),
        "teto_moedas_por_semana": teto_moedas_por_semana(),
        "hora_limite_moeda_sabado": hora_limite_moeda_sabado(),
        "atualizado_em": (
            _atualizado_em.isoformat() if _atualizado_em else None
        ),
        "atualizado_por": _atualizado_por,
    }


def _aplicar_no_cache(
    *,
    segundos: int,
    teto_dia: int,
    teto_semana: int,
    hora_sabado: int,
    atualizado_em: datetime | None = None,
    atualizado_por: int | None = None,
) -> None:
    global _segundos_para_moeda
    global _teto_moedas_por_dia
    global _teto_moedas_por_semana
    global _hora_limite_moeda_sabado
    global _atualizado_em
    global _atualizado_por
    global _carregado

    _segundos_para_moeda = max(60, int(segundos))
    _teto_moedas_por_dia = max(1, int(teto_dia))
    _teto_moedas_por_semana = max(1, int(teto_semana))
    _hora_limite_moeda_sabado = min(23, max(0, int(hora_sabado)))
    _atualizado_em = atualizado_em
    _atualizado_por = atualizado_por
    _carregado = True


async def carregar_regras_do_banco() -> dict[str, int | str | None]:
    """
    Lê a linha singleton do banco e atualiza o cache.

    Se a tabela ainda estiver vazia, grava os padrões do config.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(RegraMoedaPlantao).where(RegraMoedaPlantao.id == 1)
        )
        regra = resultado.scalar_one_or_none()
        if regra is None:
            regra = RegraMoedaPlantao(
                id=1,
                segundos_para_moeda=int(SEGUNDOS_PARA_MOEDA),
                teto_moedas_por_dia=int(TETO_MOEDAS_POR_DIA),
                teto_moedas_por_semana=int(TETO_MOEDAS_POR_SEMANA),
                hora_limite_moeda_sabado=int(HORA_LIMITE_MOEDA_SABADO),
            )
            sessao.add(regra)
            await sessao.commit()
            await sessao.refresh(regra)

        _aplicar_no_cache(
            segundos=int(regra.segundos_para_moeda),
            teto_dia=int(regra.teto_moedas_por_dia),
            teto_semana=int(regra.teto_moedas_por_semana),
            hora_sabado=int(regra.hora_limite_moeda_sabado),
            atualizado_em=regra.atualizado_em,
            atualizado_por=regra.atualizado_por,
        )

    registrador.info(
        "Regras de moeda carregadas: %ss/moeda teto dia=%s semana=%s sabado<%sh",
        segundos_para_moeda(),
        teto_moedas_por_dia(),
        teto_moedas_por_semana(),
        hora_limite_moeda_sabado(),
    )
    return resumo_regras()


async def salvar_regras(
    *,
    segundos: int,
    teto_dia: int,
    teto_semana: int,
    hora_sabado: int,
    atualizado_por: int,
) -> dict[str, int | str | None]:
    """Persiste novas regras e atualiza o cache."""
    agora = datetime.now(timezone.utc)
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(RegraMoedaPlantao).where(RegraMoedaPlantao.id == 1)
        )
        regra = resultado.scalar_one_or_none()
        if regra is None:
            regra = RegraMoedaPlantao(id=1)
            sessao.add(regra)

        regra.segundos_para_moeda = max(60, int(segundos))
        regra.teto_moedas_por_dia = max(1, int(teto_dia))
        regra.teto_moedas_por_semana = max(1, int(teto_semana))
        regra.hora_limite_moeda_sabado = min(23, max(0, int(hora_sabado)))
        regra.atualizado_em = agora
        regra.atualizado_por = int(atualizado_por)
        await sessao.commit()
        await sessao.refresh(regra)

        _aplicar_no_cache(
            segundos=int(regra.segundos_para_moeda),
            teto_dia=int(regra.teto_moedas_por_dia),
            teto_semana=int(regra.teto_moedas_por_semana),
            hora_sabado=int(regra.hora_limite_moeda_sabado),
            atualizado_em=regra.atualizado_em,
            atualizado_por=regra.atualizado_por,
        )

    return resumo_regras()
