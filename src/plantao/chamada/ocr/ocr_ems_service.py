"""
Serviço de OCR do EMS: delega pra API externa (cmsvalley-api / Render)
em vez de rodar OCR localmente no bot.

Endpoint correto da API: POST /ocr/ems
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("cmsvalley-bot")

# Preferir CMSVALLEY_API_URL (base do serviço) e cair no EMS_OCR_API_URL legado.
_URL_BRUTA = (
    os.getenv("EMS_OCR_API_URL")
    or os.getenv("CMSVALLEY_API_URL")
    or "https://ems-ocr-api-59sa.onrender.com"
).strip()

TIMEOUT_SEGUNDOS = 90


def normalizar_url_ocr_ems(url_bruta: str) -> str:
    """
    Garante que a URL termine em /ocr/ems (rota real do FastAPI).

    Aceita:
      https://host
      https://host/
      https://host/ocr
      https://host/ocr/
      https://host/ocr/ems
    """
    texto = (url_bruta or "").strip().rstrip("/")
    if not texto:
        return "https://ems-ocr-api-59sa.onrender.com/ocr/ems"

    partes = urlparse(texto)
    caminho = (partes.path or "").rstrip("/")

    if caminho.endswith("/ocr/ems"):
        return f"{partes.scheme}://{partes.netloc}{caminho}"
    if caminho.endswith("/ocr"):
        return f"{partes.scheme}://{partes.netloc}{caminho}/ems"
    if caminho in ("", "/"):
        return f"{partes.scheme}://{partes.netloc}/ocr/ems"

    # Qualquer outro path: força a rota conhecida na origem do host
    return f"{partes.scheme}://{partes.netloc}/ocr/ems"


API_URL = normalizar_url_ocr_ems(_URL_BRUTA)


class OcrEmsError(Exception):
    """
    Erro ao consultar a API externa de OCR do EMS (rede, timeout, ou erro devolvido por
    ela).
    """


async def _baixar_imagem_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resposta:
            if resposta.status != 200:
                raise OcrEmsError(
                    f"Não foi possível baixar o anexo do Discord "
                    f"(HTTP {resposta.status})."
                )
            return await resposta.read()


async def extrair_medicos_do_print_ems(url_anexo: str) -> dict:
    """
    Baixa o print do /ems anexado no Discord e manda pra API de OCR.

    Endpoint: POST {API_URL}  →  /ocr/ems
    Campo do form: file (imagem)

    Retorno esperado:
        {
          "total_detectado": int,
          "total_suspeitos": int,
          "medicos": [
            {"id": int, "nome": str, "suspeito": bool, "motivo_suspeita": str|None},
            ...
          ],
          "aviso": str | None,
        }
    """
    imagem_bytes = await _baixar_imagem_bytes(url_anexo)

    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SEGUNDOS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        form = aiohttp.FormData()
        form.add_field(
            "file",
            imagem_bytes,
            filename="print_ems.png",
            content_type="image/png",
        )

        logger.info("OCR EMS → POST %s", API_URL)

        try:
            async with session.post(API_URL, data=form) as resposta:
                # 404 quase sempre = URL sem /ocr/ems
                if resposta.status == 404:
                    corpo = await resposta.text()
                    logger.error(
                        "OCR EMS 404 em %s — confira "
                        "EMS_OCR_API_URL/CMSVALLEY_API_URL. "
                        "Corpo: %s",
                        API_URL,
                        corpo[:300],
                    )
                    raise OcrEmsError(
                        f"Endpoint OCR não encontrado (404) em `{API_URL}`. "
                        "Use a base da API ou a URL completa `.../ocr/ems`."
                    )

                try:
                    dados = await resposta.json()
                except Exception:
                    texto = await resposta.text()
                    raise OcrEmsError(
                        f"Resposta inválida da API de OCR "
                        f"(HTTP {resposta.status}): {texto[:200]}"
                    )

                if resposta.status != 200:
                    detalhe = dados.get("detail", "Erro desconhecido na API de OCR.")
                    raise OcrEmsError(detalhe)

                return dados
        except aiohttp.ClientError as exc:
            logger.error("Falha ao conectar na API de OCR do EMS: %s", exc)
            raise OcrEmsError(
                f"Não foi possível conectar na API de OCR ({exc})"
            ) from exc
