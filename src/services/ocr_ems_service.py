import asyncio
from asyncio.log import logger
import cv2
import numpy as np
import easyocr
import aiohttp

_easyocr_reader = None

_ALLOWLIST_EMS = "0123456789:.- abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZÀÁÂÃÉÊÍÓÔÕÚÇàáâãéêíóôõúç"


def _obter_leitor_easyocr():
    global _easyocr_reader
    if _easyocr_reader is None:
        _easyocr_reader = easyocr.Reader(["pt", "en"], gpu=False)
    return _easyocr_reader


async def _baixar_imagem_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resposta:
            return await resposta.read()


def _preprocessar_imagem(bytes_imagem: bytes) -> np.ndarray:
    """Upscale + contraste local apenas — SEM binarização/denoise, que quebravam
    o texto anti-aliased do jogo e geravam erros de caractere."""
    array = np.frombuffer(bytes_imagem, dtype=np.uint8)
    imagem = cv2.imdecode(array, cv2.IMREAD_COLOR)

    imagem = cv2.resize(imagem, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_LANCZOS4)
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contraste = clahe.apply(cinza)

    return contraste


def _rodar_easyocr(imagem_processada: np.ndarray) -> list[tuple[str, float]]:
    """Retorna lista de (texto, confiança) — a confiança é repassada adiante
    pra sinalizar entradas que merecem checagem visual do Doutor."""
    leitor = _obter_leitor_easyocr()
    resultados = leitor.readtext(
        imagem_processada,
        detail=1,
        paragraph=False,
        allowlist=_ALLOWLIST_EMS,
        mag_ratio=2.0,
    )
    return [(texto, confianca) for _bbox, texto, confianca in resultados]


async def extrair_linhas_do_print_ems(url_anexo: str) -> list[tuple[str, float]]:
    """Baixa a imagem, pré-processa, roda EasyOCR. Retorna lista de (texto_linha, confiança)."""
    bytes_imagem = await _baixar_imagem_bytes(url_anexo)

    loop = asyncio.get_event_loop()
    imagem_processada = await loop.run_in_executor(None, _preprocessar_imagem, bytes_imagem)
    return await loop.run_in_executor(None, _rodar_easyocr, imagem_processada)


async def aquecer_modelo_easyocr():
    """Força o download/carregamento do EasyOCR no início do bot, não na primeira
    chamada real — evita que o primeiro Doutor a usar o sistema fique travado
    esperando o download dos modelos sem saber o que está acontecendo."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _obter_leitor_easyocr)
    logger.info("✅ Modelo EasyOCR pré-carregado com sucesso")