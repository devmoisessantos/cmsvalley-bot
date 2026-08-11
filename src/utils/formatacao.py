# src/utils/formatacao.py
"""
Funções pequenas de formatação de texto (tempo, dinheiro, data).

Regra de horário: todo texto legível para humano usa America/Sao_Paulo
(Brasília). Valores no banco e timestamps Unix continuam em UTC.
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from zoneinfo import ZoneInfo

from src.config import (
    MESES_ABREV,
    TIMEZONE_LOCAL,
)


def fuso_brasilia() -> ZoneInfo:
    """Fuso oficial do projeto (Brasília / São Paulo)."""
    return ZoneInfo(TIMEZONE_LOCAL)


def para_horario_brasilia(data_hora: datetime | None) -> datetime | None:
    """
    Converte qualquer datetime para America/Sao_Paulo.

    - None → None
    - naive → assume UTC (padrão do banco / agora() do projeto)
    - aware → converte para Brasília
    """
    if data_hora is None:
        return None
    if data_hora.tzinfo is None:
        data_hora = data_hora.replace(tzinfo=timezone.utc)
    return data_hora.astimezone(fuso_brasilia())


def agora_brasilia() -> datetime:
    """Momento atual em America/Sao_Paulo."""
    return datetime.now(fuso_brasilia())


def formatar_hms(segundos: int) -> str:
    """
    Transforma segundos em texto HH:MM:SS.

    Exemplo: 3661 → "01:01:01"
    """
    horas, resto = divmod(segundos, 3600)
    minutos, segundos_restantes = divmod(resto, 60)
    return f"{horas:02d}:{minutos:02d}:{segundos_restantes:02d}"


def formatar_dinheiro(valor: int) -> str:
    """
    Formata número no padrão brasileiro com cifrão.

    Exemplo: 1000000 → "R$1.000.000"
    """
    return f"R${valor:,}".replace(",", ".")


def formatar_reais(valor: int) -> str:
    """
    Formata número no padrão brasileiro com R$.

    Exemplo: 1000000 → "R$ 1.000.000"
    """
    return f"R$ {valor:,}".replace(",", ".")


def formatar_data_hora(data_hora: datetime | None) -> str:
    """
    Formata datetime em texto amigável em português (horário de Brasília).

    Exemplo: 7 de Ago às 14:30
    """
    local = para_horario_brasilia(data_hora)
    if local is None:
        return "—"
    nome_do_mes = MESES_ABREV[local.month]
    horario = local.strftime("%H:%M")
    return f"{local.day} de {nome_do_mes} às {horario}"


def formatar_data_hora_local(data_hora: datetime | None) -> str:
    """
    Converte datetime (UTC ou naive) para America/Sao_Paulo
    no formato `YYYY-MM-DD HH:MM:SS`.

    Exemplo: 2026-08-09 02:04:29
    """
    local = para_horario_brasilia(data_hora)
    if local is None:
        return "—"
    return local.strftime("%Y-%m-%d %H:%M:%S")


def formatar_data_curta(data_hora: datetime | None) -> str:
    """
    Data curta em Brasília: DD/MM.

    Exemplo: 10/08
    """
    local = para_horario_brasilia(data_hora)
    if local is None:
        return "—"
    return f"{local.day:02d}/{local.month:02d}"


def formatar_data_hora_rodape(data_hora: datetime | None = None) -> str:
    """
    Rodapé legível em Brasília.

    Exemplo: 11 ago de 2026 • 02:30
    """
    local = (
        para_horario_brasilia(data_hora) if data_hora is not None else agora_brasilia()
    )
    assert local is not None
    meses = (
        "jan",
        "fev",
        "mar",
        "abr",
        "mai",
        "jun",
        "jul",
        "ago",
        "set",
        "out",
        "nov",
        "dez",
    )
    return (
        f"{local.day} {meses[local.month - 1]} de {local.year} "
        f"• {local.strftime('%H:%M')}"
    )


def formatar_data_solicitacao(data_hora: datetime | None = None) -> str:
    """
    Data de solicitação em Brasília.

    Exemplo: 10 de Ago 2026 14:26
    """
    local = (
        para_horario_brasilia(data_hora) if data_hora is not None else agora_brasilia()
    )
    assert local is not None
    return (
        f"{local.day} de {MESES_ABREV[local.month]} "
        f"{local.year} {local.strftime('%H:%M')}"
    )
