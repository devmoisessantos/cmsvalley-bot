"""
Contador de mensagens por canal/dia (memória, sem banco e sem Discord).

O painel mostra "mensagens por canal" a partir de `dashboard_channel_stats`,
mas ninguém alimentava essa tabela — o gráfico e o KPI viviam zerados.

Este módulo só conta. Quem envia para a API é o cog de tarefas, e quem chama
`registrar_mensagem` é o listener. Assim a conta pode ser testada sem Discord.

Não guarda conteúdo de mensagem: só o número.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

registrador = logging.getLogger(__name__)

FUSO_PADRAO = "America/Sao_Paulo"

# (canal_id, "AAAA-MM-DD") -> mensagens do dia naquele canal
_contagem: dict[tuple[int, str], int] = {}


def _fuso() -> ZoneInfo:
    """Fuso do bot; cai no de São Paulo se o config não estiver disponível."""
    try:
        from src.config import TIMEZONE_LOCAL

        return ZoneInfo(TIMEZONE_LOCAL)
    except Exception:  # noqa: BLE001 — import opcional em teste isolado
        return ZoneInfo(FUSO_PADRAO)


def dia_de_hoje(quando: datetime | None = None) -> str:
    """Data local no formato do banco (AAAA-MM-DD)."""
    return (quando or datetime.now(_fuso())).date().isoformat()


def registrar_mensagem(canal_id: int, quando: datetime | None = None) -> None:
    """Soma uma mensagem no canal do dia."""
    chave = (int(canal_id), dia_de_hoje(quando))
    _contagem[chave] = _contagem.get(chave, 0) + 1


def semear(contagens: dict, dia: str | None = None) -> int:
    """
    Parte do que já está gravado no banco para o dia.

    Evita que um restart no meio do dia faça o total desabar.
    """
    referencia = dia or dia_de_hoje()
    semeados = 0
    for canal_id, quantidade in (contagens or {}).items():
        try:
            chave = (int(canal_id), referencia)
        except (TypeError, ValueError):
            continue
        atual = _contagem.get(chave, 0)
        _contagem[chave] = max(atual, int(quantidade or 0))
        semeados += 1
    if semeados:
        registrador.info(
            "Contador de mensagens semeado com %s canais de %s.", semeados, referencia
        )
    return semeados


def montar_itens(bot, dias: int = 2) -> list[dict]:
    """Itens no formato que `/internal/status` espera."""
    limite = date.today() - timedelta(days=dias)
    itens: list[dict] = []
    for (canal_id, dia), mensagens in list(_contagem.items()):
        try:
            if datetime.strptime(dia, "%Y-%m-%d").date() < limite:
                continue
        except ValueError:
            continue
        canal = bot.get_channel(canal_id) if bot is not None else None
        nome = getattr(canal, "name", None) if canal else None
        itens.append(
            {
                "canal_id": canal_id,
                "canal_nome": nome or f"canal-{canal_id}",
                "dia": dia,
                "mensagens": mensagens,
            }
        )
    return itens


def limpar_antigos(dias: int = 3) -> int:
    """Descarta contadores de dias que já foram enviados."""
    limite = date.today() - timedelta(days=dias)
    removidos = 0
    for chave in list(_contagem):
        try:
            antigo = datetime.strptime(chave[1], "%Y-%m-%d").date() < limite
        except ValueError:
            antigo = True
        if antigo:
            del _contagem[chave]
            removidos += 1
    return removidos


def contagem_atual() -> dict[tuple[int, str], int]:
    """Cópia da contagem (para teste e diagnóstico)."""
    return dict(_contagem)


def zerar() -> None:
    """Limpa a contagem em memória (usado nos testes)."""
    _contagem.clear()
