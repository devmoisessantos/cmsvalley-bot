"""
Parse das mensagens de LOG_TOGGLE_MEDICO.

Formato típico de cada evento (pode vir vários na mesma mensagem):

    [ESTAGIARIO]: 33240 BERLIM ALGUSTO
    [===========SAIU DE SERVICO==========]
    [Data]: 21/10/2026 [Hora]: 22:39:53

A log da cidade é instável: duplica linhas, às vezes falta evento. O parse
só extrai o que conseguir ler; o serviço decide o que é ruído.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

# Fuso padrão da cidade (mesmo de TIMEZONE_LOCAL em config).
# Mantido local para o parse não depender do .env ao ser testado isolado.
_FUSO_CIDADE = "America/Sao_Paulo"

# Cargo + passaporte + nome na mesma linha
_RE_CABECALHO = re.compile(
    r"\[([^\]]+)\]:\s*(\d+)\s+(.+)",
    re.IGNORECASE,
)

# Ação no meio do bloco
_RE_ACAO = re.compile(
    r"={2,}\s*(ENTROU EM SERVICO|SAIU DE SERVICO)\s*={2,}",
    re.IGNORECASE,
)

# Data e hora no final do bloco
_RE_DATA_HORA = re.compile(
    r"\[Data\]:\s*(\d{1,2}/\d{1,2}/\d{4})\s*\[Hora\]:\s*(\d{1,2}:\d{1,2}:\d{1,2})",
    re.IGNORECASE,
)


@dataclass
class EventoToggleParseado:
    """Um evento de entrou/saiu de serviço extraído da log."""

    id_fivem: str
    nome_cidade: str
    cargo_texto: str
    acao: str  # ENTROU | SAIU
    momento: datetime | None  # timezone local quando a log traz data/hora
    texto_bruto: str


def _montar_momento(data_texto: str, hora_texto: str) -> datetime | None:
    """Converte DD/MM/AAAA e HH:MM:SS para datetime com fuso de Brasília."""
    try:
        dia, mes, ano = data_texto.strip().split("/")
        hora, minuto, segundo = hora_texto.strip().split(":")
        fuso = ZoneInfo(_FUSO_CIDADE)
        return datetime(
            int(ano),
            int(mes),
            int(dia),
            int(hora),
            int(minuto),
            int(segundo),
            tzinfo=fuso,
        )
    except (ValueError, TypeError):
        return None


def parsear_mensagem_toggle(conteudo: str) -> list[EventoToggleParseado]:
    """
    Extrai todos os eventos de toggle presentes no texto.

    Retorna lista vazia se nada for reconhecido. Cada bloco completo
    (cabeçalho + ação + data) vira um EventoToggleParseado.
    """
    if not conteudo or not conteudo.strip():
        return []

    linhas = [linha.strip() for linha in conteudo.splitlines() if linha.strip()]
    eventos: list[EventoToggleParseado] = []

    indice = 0
    while indice < len(linhas):
        match_cabecalho = _RE_CABECALHO.match(linhas[indice])
        if match_cabecalho is None:
            indice += 1
            continue

        cargo_texto = match_cabecalho.group(1).strip()
        id_fivem = match_cabecalho.group(2).strip()
        nome_cidade = match_cabecalho.group(3).strip()

        acao: str | None = None
        momento: datetime | None = None
        trechos_brutos = [linhas[indice]]

        # Olha as próximas linhas do mesmo bloco (ação e data)
        cursor = indice + 1
        while cursor < len(linhas) and cursor <= indice + 4:
            linha_atual = linhas[cursor]
            match_acao = _RE_ACAO.search(linha_atual)
            if match_acao is not None:
                texto_acao = match_acao.group(1).upper()
                if "ENTROU" in texto_acao:
                    acao = "ENTROU"
                elif "SAIU" in texto_acao:
                    acao = "SAIU"
                trechos_brutos.append(linha_atual)
                cursor += 1
                continue

            match_data = _RE_DATA_HORA.search(linha_atual)
            if match_data is not None:
                momento = _montar_momento(
                    match_data.group(1),
                    match_data.group(2),
                )
                trechos_brutos.append(linha_atual)
                cursor += 1
                break

            # Linha intermediária que não é ação nem data: para de olhar
            if _RE_CABECALHO.match(linha_atual):
                break
            cursor += 1

        if acao is not None:
            eventos.append(
                EventoToggleParseado(
                    id_fivem=id_fivem,
                    nome_cidade=nome_cidade,
                    cargo_texto=cargo_texto,
                    acao=acao,
                    momento=momento,
                    texto_bruto="\n".join(trechos_brutos),
                )
            )

        indice = max(cursor, indice + 1)

    return eventos
