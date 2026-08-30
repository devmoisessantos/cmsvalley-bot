"""
Chamadas HTTP a cmsvalley-api para criar salas de compartilhamento.

O bot nao transporta video: so pede a criacao da sala e devolve o link
ao membro. A midia fica no navegador (WebRTC).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

registrador = logging.getLogger(__name__)

# URL base da API (mesmo host do OCR/backup). Sem barra no final.
SCREENSHARE_API_URL = os.getenv(
    "SCREENSHARE_API_URL",
    os.getenv("EMS_OCR_API_URL", "https://ems-ocr-api-59sa.onrender.com"),
).rstrip("/")

# Se a API OCR termina em /ocr/ems, sobe para a origem do host.
if SCREENSHARE_API_URL.endswith("/ocr/ems"):
    from urllib.parse import urlparse

    partes = urlparse(SCREENSHARE_API_URL)
    SCREENSHARE_API_URL = f"{partes.scheme}://{partes.netloc}"

# Site publico onde o membro abre o link (pode ser diferente da API).
SCREENSHARE_PUBLIC_URL = os.getenv(
    "SCREENSHARE_PUBLIC_URL",
    SCREENSHARE_API_URL,
).rstrip("/")

SCREENSHARE_EXPIRA_EM = os.getenv("SCREENSHARE_EXPIRA_EM", "1d")
SCREENSHARE_MAX_USOS = os.getenv("SCREENSHARE_MAX_USOS", "unlimited")
SCREENSHARE_MAX_PARTICIPANTES = int(os.getenv("SCREENSHARE_MAX_PARTICIPANTES", "4"))

# Keepalive a cada 3 minutos (pedido do projeto).
SCREENSHARE_KEEPALIVE_MINUTOS = int(os.getenv("SCREENSHARE_KEEPALIVE_MINUTOS", "3"))


def montar_url_health() -> str:
    """Endpoint leve para manter a API acordada."""
    return f"{SCREENSHARE_API_URL}/health/screenshare"


def montar_url_criar_sala() -> str:
    return f"{SCREENSHARE_API_URL}/api/rooms"


def montar_link_convite(codigo: str) -> str:
    """
    Link que o membro compartilha.

    Preferimos a URL publica do site. Formato compativel com o frontend
    Lisboa/Nexus (hash route /call/CODIGO).
    """
    base = SCREENSHARE_PUBLIC_URL.rstrip("/")
    return f"{base}/#/call/{codigo}"


async def checar_saude() -> tuple[bool, dict[str, Any] | str]:
    """
    GET /health/screenshare.

    Devolve (True, payload) ou (False, motivo).
    """
    url = montar_url_health()
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sessao:
            async with sessao.get(url) as resposta:
                if resposta.status >= 400:
                    texto = await resposta.text()
                    return False, f"HTTP {resposta.status}: {texto[:200]}"
                dados = await resposta.json(content_type=None)
                return True, dados
    except aiohttp.ClientError as erro_de_rede:
        registrador.warning("Falha de rede no health screenshare: %s", erro_de_rede)
        return False, str(erro_de_rede)
    except Exception as erro_inesperado:
        registrador.exception("Erro inesperado no health screenshare")
        return False, str(erro_inesperado)


async def criar_sala(nome_exibicao: str) -> dict[str, Any]:
    """
    Cria uma sala na API e devolve o JSON com code e invite_url.

    Levanta RuntimeError com mensagem amigavel se a API falhar.
    """
    url = montar_url_criar_sala()
    corpo = {
        "display_name": (nome_exibicao or "Membro")[:64],
        "expires_in": SCREENSHARE_EXPIRA_EM,
        "max_uses": SCREENSHARE_MAX_USOS,
        "max_participants": SCREENSHARE_MAX_PARTICIPANTES,
    }
    timeout = aiohttp.ClientTimeout(total=25)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sessao:
            async with sessao.post(url, json=corpo) as resposta:
                texto = await resposta.text()
                if resposta.status >= 400:
                    registrador.error(
                        "API screenshare recusou criar sala (%s): %s",
                        resposta.status,
                        texto[:300],
                    )
                    raise RuntimeError(
                        "A API de compartilhamento recusou criar a sala. "
                        "Tente de novo em instantes."
                    )
                try:
                    dados = await resposta.json(content_type=None)
                except Exception:
                    # alguns proxies devolvem texto; tenta parse manual
                    import json

                    dados = json.loads(texto)

                codigo = dados.get("code") or ""
                if not codigo:
                    raise RuntimeError(
                        "A API criou a sala mas nao devolveu o codigo."
                    )

                # Preferimos nosso link publico (site separado da API).
                dados["invite_url"] = montar_link_convite(codigo)
                return dados
    except RuntimeError:
        raise
    except aiohttp.ClientError as erro_de_rede:
        registrador.warning("Falha de rede ao criar sala: %s", erro_de_rede)
        raise RuntimeError(
            "Nao consegui falar com a API de compartilhamento agora."
        ) from erro_de_rede
    except Exception as erro_inesperado:
        registrador.exception("Erro inesperado ao criar sala")
        raise RuntimeError(
            "Ocorreu um erro inesperado ao criar o link."
        ) from erro_inesperado
