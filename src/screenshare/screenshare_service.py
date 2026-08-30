"""
Chamadas HTTP a cmsvalley-api para criar salas de compartilhamento.

O bot nao transporta video: so pede a criacao da sala e devolve o link
ao membro. A midia fica no navegador (WebRTC).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import aiohttp

registrador = logging.getLogger(__name__)


def _com_esquema(url_bruta: str) -> str:
    """Garante https:// se a URL veio sem esquema."""
    texto = (url_bruta or "").strip()
    if not texto:
        return ""
    if texto.startswith("//"):
        return "https:" + texto
    if not texto.startswith(("http://", "https://")):
        return "https://" + texto
    return texto


def _origem_da_url(url_bruta: str) -> str:
    """
    Reduz qualquer URL (com path) para esquema + host.

    Exemplos:
    - https://host/ocr/ems  -> https://host
    - https://host/         -> https://host
    - https://host          -> https://host
    """
    texto = _com_esquema(url_bruta).rstrip("/")
    if not texto:
        return ""
    partes = urlparse(texto)
    if partes.scheme and partes.netloc:
        return f"{partes.scheme}://{partes.netloc}"
    return texto


def _url_publica_completa(url_bruta: str) -> str:
    """
    URL do site onde o membro abre o compartilhamento.

    Mantém o path (ex.: /screenshare). Só normaliza esquema e barra final.
    """
    return _com_esquema(url_bruta).rstrip("/")


# URL base da API (Render). Sem path.
_url_bruta = os.getenv("SCREENSHARE_API_URL", "").strip()
if not _url_bruta:
    _url_bruta = os.getenv(
        "EMS_OCR_API_URL",
        "https://ems-ocr-api.onrender.com",
    )
SCREENSHARE_API_URL = _origem_da_url(_url_bruta) or (
    "https://ems-ocr-api.onrender.com"
)

# Site publico (Vercel). Pode incluir /screenshare.
_public_bruta = os.getenv("SCREENSHARE_PUBLIC_URL", "").strip()
if not _public_bruta:
    _public_bruta = SCREENSHARE_API_URL
SCREENSHARE_PUBLIC_URL = _url_publica_completa(_public_bruta)

SCREENSHARE_EXPIRA_EM = os.getenv("SCREENSHARE_EXPIRA_EM", "1d")
SCREENSHARE_MAX_USOS = os.getenv("SCREENSHARE_MAX_USOS", "unlimited")
SCREENSHARE_MAX_PARTICIPANTES = int(os.getenv("SCREENSHARE_MAX_PARTICIPANTES", "4"))

# Keepalive a cada 3 minutos (pedido do projeto).
SCREENSHARE_KEEPALIVE_MINUTOS = int(os.getenv("SCREENSHARE_KEEPALIVE_MINUTOS", "3"))


def montar_url_health() -> str:
    """Endpoint leve para manter a API acordada e validar o deploy."""
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


def _mensagem_http_falha(status: int, corpo: str, url: str) -> str:
    """Traduz status HTTP em texto que o membro e a admin entendem."""
    if status == 404:
        return (
            f"A API em `{url}` ainda nao tem as rotas de compartilhamento "
            f"(HTTP 404). Faca o deploy do cmsvalley-api com a pasta "
            f"`api/screenshare/` e reinicie o servico no Render."
        )
    if status == 503:
        return (
            "O store de salas nao subiu na API (HTTP 503). "
            "Confira os logs de startup do Render."
        )
    if status == 429:
        return "Muitas salas criadas em pouco tempo. Aguarde um minuto."
    trecho = (corpo or "").replace("\n", " ")[:180]
    return f"A API respondeu HTTP {status} em `{url}`. Detalhe: {trecho}"


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
                texto = await resposta.text()
                if resposta.status >= 400:
                    return False, _mensagem_http_falha(
                        resposta.status, texto, url
                    )
                try:
                    dados = json.loads(texto) if texto else {}
                except json.JSONDecodeError:
                    return False, f"Resposta invalida de `{url}`: {texto[:120]}"
                return True, dados
    except aiohttp.ClientError as erro_de_rede:
        registrador.warning("Falha de rede no health screenshare: %s", erro_de_rede)
        return False, f"Rede: nao alcancei `{url}` ({erro_de_rede})"
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
                        "API screenshare recusou criar sala (%s) em %s: %s",
                        resposta.status,
                        url,
                        texto[:300],
                    )
                    raise RuntimeError(
                        _mensagem_http_falha(resposta.status, texto, url)
                    )
                try:
                    dados = json.loads(texto) if texto else {}
                except json.JSONDecodeError as erro_json:
                    raise RuntimeError(
                        f"A API respondeu texto invalido em `{url}`."
                    ) from erro_json

                codigo = dados.get("code") or ""
                if not codigo:
                    raise RuntimeError(
                        "A API criou a sala mas nao devolveu o codigo."
                    )

                dados["invite_url"] = montar_link_convite(codigo)
                return dados
    except RuntimeError:
        raise
    except aiohttp.ClientError as erro_de_rede:
        registrador.warning("Falha de rede ao criar sala: %s", erro_de_rede)
        raise RuntimeError(
            f"Nao consegui falar com a API em `{url}`."
        ) from erro_de_rede
    except Exception as erro_inesperado:
        registrador.exception("Erro inesperado ao criar sala")
        raise RuntimeError(
            "Ocorreu um erro inesperado ao criar o link."
        ) from erro_inesperado
