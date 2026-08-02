"""
Serviço de OCR do EMS: delega pra API externa (ems-ocr-service, no Render)
em vez de rodar qualquer OCR aqui dentro do bot.

Isso substitui a versão anterior, que tentava rodar EasyOCR + OpenCV
localmente — só que easyocr/opencv/numpy nunca estiveram no
requirements.txt do bot (e faltavam os imports de easyocr/cv2 no arquivo
original), então essa versão antiga nunca funcionou de verdade em
produção: na primeira chamada real ia estourar erro.
"""

import os
import logging

import aiohttp

logger = logging.getLogger("cmsvalley-bot")

# IMPORTANTE: precisa terminar em /ocr/ems — é o endpoint real da API,
# não a raiz do serviço.
API_URL = os.getenv("EMS_OCR_API_URL", "https://ems-ocr-api-59sa.onrender.com/ocr/ems")
TIMEOUT_SEGUNDOS = 90  # o motor de OCR externo pode demorar alguns segundos pra responder


class OcrEmsError(Exception):
    """Erro ao consultar a API externa de OCR do EMS (rede, timeout, ou erro devolvido por ela)."""


async def _baixar_imagem_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resposta:
            if resposta.status != 200:
                raise OcrEmsError(f"Não foi possível baixar o anexo do Discord (HTTP {resposta.status}).")
            return await resposta.read()


async def extrair_medicos_do_print_ems(url_anexo: str) -> dict:
    """
    Baixa o print do /ems anexado no Discord e manda pra API de OCR externa.

    Retorna o mesmo formato que a API devolve:
        {
          "total_detectado": int,
          "total_suspeitos": int,
          "medicos": [{"id": int, "nome": str, "suspeito": bool, "motivo_suspeita": str|None}, ...],
          "aviso": str | None,
        }

    A API já faz o parsing 'ID: Nome' e já sinaliza IDs fora do intervalo
    esperado (suspeito=True) — não precisa de nenhum parser adicional no
    lado do bot pra isso.
    """
    imagem_bytes = await _baixar_imagem_bytes(url_anexo)

    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SEGUNDOS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        form = aiohttp.FormData()
        form.add_field("file", imagem_bytes, filename="print_ems.png", content_type="image/png")

        try:
            async with session.post(API_URL, data=form) as resposta:
                dados = await resposta.json()
                if resposta.status != 200:
                    # a API usa HTTPException do FastAPI -> o corpo de erro vem
                    # como {"detail": "..."}, não {"erro": "..."}
                    raise OcrEmsError(dados.get("detail", "Erro desconhecido na API de OCR."))
                return dados
        except aiohttp.ClientError as exc:
            logger.error("Falha ao conectar na API de OCR do EMS: %s", exc)
            raise OcrEmsError(f"Não foi possível conectar na API de OCR ({exc})") from exc