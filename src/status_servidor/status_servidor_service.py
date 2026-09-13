"""
Busca o status do servidor FiveM da cidade e calcula o próximo restart.

Ordem das tentativas:

1. Endpoints diretos (dynamic.json no IP/domínio configurado) — caminho
   principal: a API pública do CFX não lista este servidor.
2. Resolve o IP real pelo cabeçalho do cfx.re/join e consulta de novo.
3. API pública do CFX (só funciona se o servidor estiver listado).

Se nada responder, devolve online=False para o painel mostrar OFFLINE.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp

from src.config import (
    FUSO_HORARIO_LOCAL,
    RR_HORARIOS,
    STATUS_SERVIDOR,
)

registrador = logging.getLogger(__name__)

FUSO = ZoneInfo(FUSO_HORARIO_LOCAL)

# Códigos de cor do FiveM no hostname (^1, ^2, ^5, etc.)
PADRAO_COR_FIVEM = re.compile(r"\^[0-9a-zA-Z]")


async def buscar_status_do_servidor() -> dict:
    """
    Devolve o estado atual do servidor FiveM.

    Chaves:
    - online (bool)
    - jogadores (int)
    - max_jogadores (int)
    - nome (str)
    - erro (str | None)
    """
    resultado_direto = await _consultar_endpoint_direto()
    if resultado_direto is not None:
        return resultado_direto

    # IP pode ter mudado: descobre pelo join do CFX e tenta de novo
    url_descoberta = await _descobrir_url_dynamic_pelo_join()
    if url_descoberta is not None:
        resultado_descoberto = await _consultar_uma_url_dynamic(url_descoberta)
        if resultado_descoberto is not None:
            return resultado_descoberto

    resultado_cfx = await _consultar_api_cfx()
    if resultado_cfx is not None:
        return resultado_cfx

    return {
        "online": False,
        "jogadores": 0,
        "max_jogadores": STATUS_SERVIDOR["MAX_JOGADORES"],
        "nome": STATUS_SERVIDOR["NOME_SERVIDOR"],
        "erro": "Não consegui alcançar o servidor.",
    }


def _limpar_nome_do_servidor(nome_bruto: str) -> str:
    """
    Remove códigos de cor do FiveM (^5, ^1, ...) e espaços extras.
    """
    sem_cor = PADRAO_COR_FIVEM.sub("", nome_bruto or "")
    return " ".join(sem_cor.split()).strip() or STATUS_SERVIDOR["NOME_SERVIDOR"]


def _montar_resultado_de_dynamic(dados: dict) -> dict:
    """
    Converte o JSON do dynamic.json no dicionário padrão do domínio.
    """
    jogadores = int(dados.get("clients") or 0)
    max_bruto = dados.get("sv_maxclients") or STATUS_SERVIDOR["MAX_JOGADORES"]
    max_jogadores = int(max_bruto)
    nome = _limpar_nome_do_servidor(
        str(dados.get("hostname") or STATUS_SERVIDOR["NOME_SERVIDOR"])
    )

    return {
        "online": True,
        "jogadores": jogadores,
        "max_jogadores": max_jogadores,
        "nome": nome,
        "erro": None,
    }


async def _consultar_uma_url_dynamic(url: str) -> dict | None:
    """
    Consulta uma única URL de dynamic.json.
    """
    try:
        async with aiohttp.ClientSession() as sessao_http:
            async with sessao_http.get(
                url,
                timeout=aiohttp.ClientTimeout(total=6),
                headers={"User-Agent": "CitizenFX/1.0"},
            ) as resposta:
                if resposta.status != 200:
                    return None
                dados = await resposta.json(content_type=None)
                if not isinstance(dados, dict):
                    return None
                return _montar_resultado_de_dynamic(dados)
    except Exception as erro_capturado:
        registrador.debug(
            "Falha ao consultar dynamic.json em %s: %s",
            url,
            erro_capturado,
        )
        return None


async def _consultar_endpoint_direto() -> dict | None:
    """
    Tenta o dynamic.json nas URLs configuradas (IP real e domínio).
    """
    urls_possiveis = list(STATUS_SERVIDOR.get("URLS_DYNAMIC") or [])

    for url in urls_possiveis:
        resultado = await _consultar_uma_url_dynamic(url)
        if resultado is not None:
            return resultado

    return None


async def _descobrir_url_dynamic_pelo_join() -> str | None:
    """
    Lê o cabeçalho x-citizenfx-url da página cfx.re/join/{codigo}.

    Esse cabeçalho aponta para o endpoint real (ex.: http://IP:30120/).
    Devolve a URL completa do dynamic.json, ou None se não descobrir.
    """
    codigo = STATUS_SERVIDOR.get("CFX_CODIGO") or ""
    if not codigo:
        return None

    url_join = f"https://cfx.re/join/{codigo}"

    try:
        async with aiohttp.ClientSession() as sessao_http:
            async with sessao_http.get(
                url_join,
                timeout=aiohttp.ClientTimeout(total=8),
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
            ) as resposta:
                endpoint = resposta.headers.get("x-citizenfx-url")
                if not endpoint:
                    registrador.warning(
                        "Join CFX %s não trouxe x-citizenfx-url.",
                        codigo,
                    )
                    return None

                endpoint = endpoint.rstrip("/")
                url_dynamic = f"{endpoint}/dynamic.json"
                registrador.info(
                    "Endpoint real do servidor descoberto via join: %s",
                    endpoint,
                )
                return url_dynamic
    except Exception as erro_capturado:
        registrador.exception(
            "Falha ao resolver o join CFX %s: %s",
            codigo,
            erro_capturado,
        )
        return None


async def _consultar_api_cfx() -> dict | None:
    """
    Consulta a API pública do CFX pelo código de join.

    Só funciona se o servidor estiver listado. No Valley isso costuma
    falhar (404), por isso é o último recurso.
    """
    codigo = STATUS_SERVIDOR["CFX_CODIGO"]
    url = f"https://servers-frontend.fivem.net/api/servers/single/{codigo}"

    try:
        async with aiohttp.ClientSession() as sessao_http:
            async with sessao_http.get(
                url,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resposta:
                if resposta.status != 200:
                    registrador.debug(
                        "API CFX retornou status %s para o código %s",
                        resposta.status,
                        codigo,
                    )
                    return None

                dados_completos = await resposta.json()
                dados_servidor = dados_completos.get("Data") or {}

                jogadores = int(dados_servidor.get("clients") or 0)
                max_jogadores = int(
                    dados_servidor.get("sv_maxclients")
                    or STATUS_SERVIDOR["MAX_JOGADORES"]
                )
                nome = _limpar_nome_do_servidor(
                    str(
                        dados_servidor.get("hostname")
                        or STATUS_SERVIDOR["NOME_SERVIDOR"]
                    )
                )

                return {
                    "online": True,
                    "jogadores": jogadores,
                    "max_jogadores": max_jogadores,
                    "nome": nome,
                    "erro": None,
                }
    except Exception as erro_capturado:
        registrador.exception(
            "Falha ao consultar a API do CFX: %s",
            erro_capturado,
        )
        return None


def calcular_proximo_restart() -> str:
    """
    Calcula quanto tempo falta para o próximo restart (horários em RR_HORARIOS).

    Retorna texto legível, por exemplo: "em 2hrs 15m" ou "em 40m".
    """
    agora = datetime.now(FUSO)
    candidatos = []

    for horario_texto in RR_HORARIOS:
        partes = horario_texto.split(":")
        hora = int(partes[0])
        minuto = int(partes[1])

        candidato = agora.replace(
            hour=hora,
            minute=minuto,
            second=0,
            microsecond=0,
        )

        if candidato <= agora:
            candidato = candidato + timedelta(days=1)

        candidatos.append(candidato)

    if not candidatos:
        return "não configurado"

    proximo = min(candidatos)
    diferenca = proximo - agora
    total_segundos = int(diferenca.total_seconds())

    if total_segundos < 0:
        total_segundos = 0

    horas = total_segundos // 3600
    minutos = (total_segundos % 3600) // 60

    if horas > 0:
        return f"em {horas}hrs {minutos}m"
    return f"em {minutos}m"
