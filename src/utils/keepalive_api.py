# src/utils/keepalive_api.py
"""
Mantém a API externa (Render free) acordada enquanto o bot estiver online.

O plano gratuito do Render “dorme” sem tráfego. Este módulo faz um GET
leve de tempos em tempos na URL de health/raiz — sem impacto no uso real.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("cmsvalley-bot.keepalive")

# Endpoint real do OCR (pode ser .../ocr/ems). O keep-alive prefere a raiz do host.
EMS_OCR_API_URL = os.getenv(
    "EMS_OCR_API_URL",
    "https://ems-ocr-api-59sa.onrender.com/ocr/ems",
)

# Se quiser forçar outra URL de ping (ex.: https://servico.onrender.com/health)
EMS_OCR_KEEPALIVE_URL = os.getenv("EMS_OCR_KEEPALIVE_URL", "").strip()

# Intervalo em minutos (Render free costuma dormir ~15 min sem tráfego)
KEEPALIVE_INTERVALO_MINUTOS = int(os.getenv("KEEPALIVE_INTERVALO_MINUTOS", "10"))


def montar_url_keepalive() -> str:
    """
    Decide qual URL pingar.

    1. EMS_OCR_KEEPALIVE_URL se estiver definida
    2. Senão, a origem (esquema + host) da EMS_OCR_API_URL
    """
    if EMS_OCR_KEEPALIVE_URL:
        return EMS_OCR_KEEPALIVE_URL

    partes = urlparse(EMS_OCR_API_URL)
    if not partes.scheme or not partes.netloc:
        return EMS_OCR_API_URL

    return f"{partes.scheme}://{partes.netloc}/"


async def ping_api_keepalive() -> tuple[bool, str]:
    """
    Faz um GET silencioso na URL de keep-alive.

    Retorna (sucesso, detalhe).
    """
    url = montar_url_keepalive()
    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as sessao:
            async with sessao.get(url) as resposta:
                # Qualquer resposta HTTP conta como “serviço acordado”
                # (mesmo 404 na raiz — o processo já acordou).
                detalhe = f"HTTP {resposta.status} em {url}"
                logger.info("Keep-alive API: %s", detalhe)
                return True, detalhe
    except Exception as erro:
        detalhe = f"Falha ao pingar {url}: {erro}"
        logger.warning("Keep-alive API: %s", detalhe)
        return False, detalhe
