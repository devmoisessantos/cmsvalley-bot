"""
Cliente HTTP: envia o transcript HTML para a cmsvalley-api.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import aiohttp

from src.database.connection import async_session
from src.database.models import Ticket

logger = logging.getLogger("cmsvalley-bot.tickets.transcript")

CMSVALLEY_API_URL = os.getenv(
    "CMSVALLEY_API_URL",
    os.getenv("EMS_OCR_API_URL", "https://ems-ocr-api-59sa.onrender.com"),
).rstrip("/")
if "/ocr/" in CMSVALLEY_API_URL or CMSVALLEY_API_URL.endswith("/ocr"):
    partes = urlparse(CMSVALLEY_API_URL)
    CMSVALLEY_API_URL = f"{partes.scheme}://{partes.netloc}"

BACKUP_API_TOKEN = os.getenv("BACKUP_API_TOKEN", "").strip()

# Site público (Vercel) — link do botão "Acessar o Transcript"
TRANSCRIPT_SITE_URL = os.getenv(
    "TRANSCRIPT_SITE_URL",
    "https://cmsvalley-api.vercel.app",
).rstrip("/")


def _cabecalhos() -> dict[str, str]:
    cabecalhos = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if BACKUP_API_TOKEN:
        cabecalhos["X-Backup-Token"] = BACKUP_API_TOKEN
    return cabecalhos


async def enviar_transcript_para_api(
    ticket: Ticket,
    html: str,
) -> str | None:
    """
    POST /transcripts na API.

    Retorna a URL pública de visualização ou None se falhar.
    Também grava ticket.url_transcript no banco quando obtém a URL.
    """
    if not CMSVALLEY_API_URL:
        msg = "CMSVALLEY_API_URL/EMS_OCR_API_URL não configurada — transcript não enviado."
        logger.error(msg)
        print(f"⚠️ [transcript] {msg}")
        return None

    if not BACKUP_API_TOKEN:
        msg = (
            "BACKUP_API_TOKEN não configurado no BOT — a API rejeita com 401. "
            "Defina a mesma senha do Render no ambiente do bot."
        )
        logger.error(msg)
        print(f"⚠️ [transcript] {msg}")
        return None

    if not ticket.senha_transcript:
        msg = f"Ticket #{ticket.id} sem senha de transcript."
        logger.error(msg)
        print(f"⚠️ [transcript] {msg}")
        return None

    if not html or not str(html).strip():
        msg = f"Ticket #{ticket.id} HTML de transcript vazio."
        logger.error(msg)
        print(f"⚠️ [transcript] {msg}")
        return None

    payload = {
        "ticket_id": int(ticket.id),
        "senha": ticket.senha_transcript,
        "html": html,
        "categoria": ticket.categoria_rotulo or "",
        "autor_nome": ticket.autor_nome or "",
        "staff_nome": ticket.staff_finalizou_nome or "",
    }

    url = f"{CMSVALLEY_API_URL}/transcripts"
    print(f"📤 [transcript] POST {url} ticket=#{ticket.id}")

    try:
        async with aiohttp.ClientSession() as sessao_http:
            async with sessao_http.post(
                url,
                json=payload,
                headers=_cabecalhos(),
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resposta:
                texto = await resposta.text()
                if resposta.status == 401:
                    msg = (
                        f"Token inválido ao enviar transcript #{ticket.id}. "
                        "BACKUP_API_TOKEN do bot deve ser idêntico ao do Render."
                    )
                    logger.error(msg)
                    print(f"⚠️ [transcript] {msg}")
                    return None
                if resposta.status >= 400:
                    msg = (
                        f"Falha ao enviar transcript #{ticket.id}: "
                        f"HTTP {resposta.status} — {texto[:300]}"
                    )
                    logger.error(msg)
                    print(f"⚠️ [transcript] {msg}")
                    return None

                try:
                    dados = await resposta.json(content_type=None)
                except Exception:
                    msg = (
                        f"Resposta inválida ao enviar transcript #{ticket.id}: "
                        f"{texto[:200]}"
                    )
                    logger.error(msg)
                    print(f"⚠️ [transcript] {msg}")
                    return None
    except Exception as erro:
        msg = f"Erro de rede ao enviar transcript #{ticket.id}: {erro}"
        logger.error(msg)
        print(f"⚠️ [transcript] {msg}")
        return None

    # Preferir página do site (Vercel); a API só guarda o HTML
    if TRANSCRIPT_SITE_URL:
        url_visualizacao = f"{TRANSCRIPT_SITE_URL}/transcript?id={ticket.id}"
    else:
        url_visualizacao = (dados or {}).get("url_visualizacao") or (
            f"{CMSVALLEY_API_URL}/transcript/{ticket.id}"
        )

    async with async_session() as sessao:
        ticket_db = await sessao.get(Ticket, ticket.id)
        if ticket_db is not None:
            ticket_db.url_transcript = url_visualizacao
            await sessao.commit()
            await sessao.refresh(ticket_db)

    logger.info("Transcript #%s publicado em %s", ticket.id, url_visualizacao)
    print(f"✅ [transcript] Ticket #{ticket.id} → {url_visualizacao}")
    return url_visualizacao
