"""
Escolhe o banco de perguntas conforme o tipo do laudo.

PORTE        → perguntas_porte
LAUDO_ADOCAO → perguntas_adocao
"""

from __future__ import annotations

from typing import (
    Any,
    Callable,
)

from src.laudos import perguntas_adocao
from src.laudos import perguntas_porte

TIPO_PORTE = "PORTE"
TIPO_ADOCAO = "LAUDO_ADOCAO"

TIPOS_VALIDOS = (TIPO_PORTE, TIPO_ADOCAO)

ROTULO_TIPO = {
    TIPO_PORTE: "Laudo (porte)",
    TIPO_ADOCAO: "Laudo técnico (adoção)",
}


def normalizar_tipo_laudo(tipo: str | None) -> str:
    """Garante um tipo válido; desconhecido vira PORTE."""
    valor = (tipo or "").strip().upper()
    if valor in TIPOS_VALIDOS:
        return valor
    return TIPO_PORTE


def total_perguntas(tipo: str | None) -> int:
    """Quantidade de perguntas do tipo informado."""
    tipo_ok = normalizar_tipo_laudo(tipo)
    if tipo_ok == TIPO_ADOCAO:
        return perguntas_adocao.TOTAL_PERGUNTAS_ADOCAO
    return perguntas_porte.TOTAL_PERGUNTAS_PORTE


def nota_minima(tipo: str | None) -> int:
    """Nota mínima para parecer sugerido APROVADO."""
    tipo_ok = normalizar_tipo_laudo(tipo)
    if tipo_ok == TIPO_ADOCAO:
        return perguntas_adocao.NOTA_MINIMA_ADOCAO
    return perguntas_porte.NOTA_MINIMA_PORTE


def tamanho_bloco(tipo: str | None) -> int:
    """Tamanho do bloco visual de perguntas na prova."""
    tipo_ok = normalizar_tipo_laudo(tipo)
    if tipo_ok == TIPO_ADOCAO:
        return perguntas_adocao.TAMANHO_BLOCO_ADOCAO
    return perguntas_porte.TAMANHO_BLOCO_PORTE


def obter_pergunta(tipo: str | None, numero: int) -> Any:
    """Devolve a pergunta do banco certo ou None."""
    tipo_ok = normalizar_tipo_laudo(tipo)
    if tipo_ok == TIPO_ADOCAO:
        return perguntas_adocao.obter_pergunta(numero)
    return perguntas_porte.obter_pergunta(numero)


def texto_da_opcao(tipo: str | None, pergunta: Any, letra: str) -> str:
    """Texto da alternativa A/B/C no banco do tipo."""
    tipo_ok = normalizar_tipo_laudo(tipo)
    if tipo_ok == TIPO_ADOCAO:
        return perguntas_adocao.texto_da_opcao(pergunta, letra)
    return perguntas_porte.texto_da_opcao(pergunta, letra)


def rotulo_tipo(tipo: str | None) -> str:
    """Texto amigável do tipo para interface."""
    return ROTULO_TIPO.get(normalizar_tipo_laudo(tipo), "Laudo (porte)")
