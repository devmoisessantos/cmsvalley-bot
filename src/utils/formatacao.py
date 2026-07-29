def formatar_hms(segundos: int) -> str:
    horas, resto = divmod(segundos, 3600)
    minutos, segs = divmod(resto, 60)
    return f"{horas:02d}:{minutos:02d}:{segs:02d}"


def formatar_dinheiro(valor: int) -> str:
    """Formata no padrão brasileiro: $1.000.000 em vez de $1,000,000"""
    return f"${valor:,}".replace(",", ".")