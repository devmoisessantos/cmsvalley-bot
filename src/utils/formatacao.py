# src/utils/formatacao.py
"""
Funções pequenas de formatação de texto (tempo, dinheiro, data).
"""

from src.config import MESES_ABREV


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

    Exemplo: 1000000 → "$1.000.000"
    """
    return f"R${valor:,}".replace(",", ".")


def formatar_reais(valor: int) -> str:
    """
    Formata número no padrão brasileiro com R$.

    Exemplo: 1000000 → "R$ 1.000.000"
    """
    return f"R$ {valor:,}".replace(",", ".")


def formatar_data_hora(data_hora) -> str:
    """
    Formata datetime em texto amigável em português.

    Exemplo: 7 de Ago às 14:30
    """
    nome_do_mes = MESES_ABREV[data_hora.month]
    horario = data_hora.strftime("%H:%M")
    return f"{data_hora.day} de {nome_do_mes} às {horario}"
