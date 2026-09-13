"""
Busca o status do servidor FiveM da cidade e calcula o próximo restart.

Tenta primeiro a API pública do CFX (mais estável quando o servidor está
listado). Se falhar, tenta os endpoints diretos do domínio. Se nada
responder, devolve online=False para o painel mostrar OFFLINE.
"""

from __future__ import annotations

import logging
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
    resultado_cfx = await _consultar_api_cfx()
    if resultado_cfx is not None:
        return resultado_cfx

    resultado_direto = await _consultar_endpoint_direto()
    if resultado_direto is not None:
        return resultado_direto

    return {
        "online": False,
        "jogadores": 0,
        "max_jogadores": STATUS_SERVIDOR["MAX_JOGADORES"],
        "nome": STATUS_SERVIDOR["NOME_SERVIDOR"],
        "erro": "Não consegui alcançar o servidor.",
    }


async def _consultar_api_cfx() -> dict | None:
    """
    Consulta a API oficial do CFX pelo código de join.
    Devolve None quando a API não responde ou o código não existe.
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
                    registrador.warning(
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
                nome = (
                    dados_servidor.get("hostname")
                    or STATUS_SERVIDOR["NOME_SERVIDOR"]
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


async def _consultar_endpoint_direto() -> dict | None:
    """
    Tenta o dynamic.json no domínio/IP do servidor.
    Devolve None se nenhuma URL responder.
    """
    urls_possiveis = STATUS_SERVIDOR.get("URLS_DYNAMIC") or [
        "http://valleyfivem.com:30120/dynamic.json",
    ]

    for url in urls_possiveis:
        try:
            async with aiohttp.ClientSession() as sessao_http:
                async with sessao_http.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=6),
                ) as resposta:
                    if resposta.status != 200:
                        continue

                    dados = await resposta.json()
                    jogadores = int(dados.get("clients") or 0)
                    max_jogadores = int(
                        dados.get("sv_maxclients")
                        or STATUS_SERVIDOR["MAX_JOGADORES"]
                    )
                    nome = (
                        dados.get("hostname")
                        or STATUS_SERVIDOR["NOME_SERVIDOR"]
                    )

                    return {
                        "online": True,
                        "jogadores": jogadores,
                        "max_jogadores": max_jogadores,
                        "nome": nome,
                        "erro": None,
                    }
        except Exception:
            continue

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
