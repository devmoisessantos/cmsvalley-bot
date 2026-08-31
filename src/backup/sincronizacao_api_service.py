# src/backup/api_db_sync.py
"""
Sincroniza o banco do bot com o cofre JSON na API CMS Valley.

Fluxo no reinício / task automática:
  1. Exporta o Postgres local para JSON (tabelas + PKs).
  2. POST /backup/db/sync na API → merge só-aditivo (nunca apaga no cofre).
  3. GET /backup/db → snapshot mesclado.
  4. Restaura no Postgres local só o que faltar (INSERT de linhas ausentes).
     Nunca DELETE / UPDATE destrutivo.

Env:
  CMSVALLEY_API_URL   — ex: https://ems-ocr-api.onrender.com
  BACKUP_API_TOKEN    — mesmo token configurado na API
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import (
    date,
    datetime,
    timezone,
)
from decimal import Decimal
from typing import Any

import aiohttp
from sqlalchemy import (
    select,
    text,
)

from src.database.conexao import async_session
from src.database.models import Base

logger = logging.getLogger(__name__)

CMSVALLEY_API_URL = os.getenv(
    "CMSVALLEY_API_URL",
    os.getenv("EMS_OCR_API_URL", "https://ems-ocr-api.onrender.com"),
).rstrip("/")
# Se veio a URL completa do OCR, usa só a origem
if "/ocr/" in CMSVALLEY_API_URL:
    from urllib.parse import urlparse

    partes = urlparse(CMSVALLEY_API_URL)
    CMSVALLEY_API_URL = f"{partes.scheme}://{partes.netloc}"

BACKUP_API_TOKEN = os.getenv("BACKUP_API_TOKEN", "").strip()


def _headers() -> dict[str, str]:
    cabecalhos = {"Content-Type": "application/json", "Accept": "application/json"}
    if BACKUP_API_TOKEN:
        cabecalhos["X-Backup-Token"] = BACKUP_API_TOKEN
    return cabecalhos


def _serializar_valor(valor: Any) -> Any:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (bytes, bytearray)):
        return valor.hex()
    try:
        json.dumps(valor)
        return valor
    except TypeError:
        return str(valor)


def _hash_tabelas(tabelas: dict) -> str:
    serializado = json.dumps(tabelas, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


async def exportar_snapshot_banco() -> dict[str, Any]:
    """Lê todas as tabelas do ORM e monta o payload no formato da API."""
    tabelas: dict[str, Any] = {}

    async with async_session() as sessao:
        for tabela in Base.metadata.sorted_tables:
            nomes_pk = [coluna.name for coluna in tabela.primary_key.columns]
            resultado = await sessao.execute(select(tabela))
            linhas = []
            for linha in resultado.mappings().all():
                registro = {
                    chave: _serializar_valor(valor)
                    for chave, valor in dict(linha).items()
                }
                linhas.append(registro)
            tabelas[tabela.name] = {
                "chaves_primarias": nomes_pk,
                "linhas": linhas,
            }

    return {
        "versao": 1,
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
        "hash_conteudo": _hash_tabelas(tabelas),
        "tabelas": tabelas,
    }


async def _get_json(caminho: str) -> dict[str, Any] | None:
    url = f"{CMSVALLEY_API_URL}{caminho}"
    try:
        async with aiohttp.ClientSession() as sessao_http:
            async with sessao_http.get(
                url,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resposta:
                if resposta.status == 401:
                    logger.error("[api-db] token inválido ao GET %s", caminho)
                    return None
                if resposta.status >= 400:
                    texto = await resposta.text()
                    logger.error(
                        "[api-db] GET %s falhou (%s): %s",
                        caminho,
                        resposta.status,
                        texto[:300],
                    )
                    return None
                return await resposta.json(content_type=None)
    except Exception as erro:
        logger.error("[api-db] GET %s erro de rede: %s", caminho, erro)
        return None


async def _post_json(caminho: str, corpo: dict) -> dict[str, Any] | None:
    url = f"{CMSVALLEY_API_URL}{caminho}"
    try:
        async with aiohttp.ClientSession() as sessao_http:
            async with sessao_http.post(
                url,
                headers=_headers(),
                json=corpo,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resposta:
                if resposta.status == 401:
                    logger.error("[api-db] token inválido ao POST %s", caminho)
                    return None
                if resposta.status >= 400:
                    texto = await resposta.text()
                    logger.error(
                        "[api-db] POST %s falhou (%s): %s",
                        caminho,
                        resposta.status,
                        texto[:300],
                    )
                    return None
                return await resposta.json(content_type=None)
    except Exception as erro:
        logger.error("[api-db] POST %s erro de rede: %s", caminho, erro)
        return None


async def obter_meta_remoto() -> dict[str, Any] | None:
    """Consulta a versão e a integridade do cofre antes de sincronizar dados."""
    return await _get_json("/backup/db/meta")


async def obter_snapshot_remoto() -> dict[str, Any] | None:
    """Baixa o cofre mesclado para recuperar registros ausentes no banco local."""
    return await _get_json("/backup/db")


async def enviar_snapshot_para_api(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Merge aditivo no cofre da API."""
    return await _post_json("/backup/db/sync", snapshot)


def _tupla_pk(linha: dict, chaves_primarias: list[str]) -> tuple:
    return tuple(linha.get(nome) for nome in chaves_primarias)


async def restaurar_faltantes_no_banco(snapshot: dict[str, Any]) -> dict[str, int]:
    """
    Insere no Postgres local apenas linhas que ainda não existem (por PK).
    Nunca apaga nem atualiza registro já presente.
    """
    estatisticas = {
        "tabelas_tocadas": 0,
        "linhas_inseridas": 0,
        "linhas_ja_existiam": 0,
        "erros": 0,
    }
    tabelas_snapshot = snapshot.get("tabelas") or {}
    if not tabelas_snapshot:
        return estatisticas

    mapa_tabelas = {tabela.name: tabela for tabela in Base.metadata.sorted_tables}

    async with async_session() as sessao:
        for nome_tabela, bloco in tabelas_snapshot.items():
            if not isinstance(bloco, dict):
                continue
            tabela = mapa_tabelas.get(nome_tabela)
            if tabela is None:
                # Tabela do snapshot que o código atual ainda não mapeia — ignora
                continue

            chaves_primarias = list(
                bloco.get("chaves_primarias")
                or [coluna.name for coluna in tabela.primary_key.columns]
            )
            linhas_remotas = [
                linha
                for linha in (bloco.get("linhas") or [])
                if isinstance(linha, dict)
            ]
            if not linhas_remotas:
                continue

            # Conjunto de PKs já no banco local
            colunas_pk = [
                tabela.c[nome] for nome in chaves_primarias if nome in tabela.c
            ]
            pks_locais: set[tuple] = set()
            if colunas_pk:
                resultado_local = await sessao.execute(select(*colunas_pk))
                for registro in resultado_local.all():
                    pks_locais.add(tuple(registro))

            estatisticas["tabelas_tocadas"] += 1
            for linha in linhas_remotas:
                chave = _tupla_pk(linha, chaves_primarias)
                if chave in pks_locais:
                    estatisticas["linhas_ja_existiam"] += 1
                    continue

                # Só colunas que existem na tabela atual
                valores = {
                    coluna.name: linha.get(coluna.name)
                    for coluna in tabela.columns
                    if coluna.name in linha
                }
                if not valores:
                    continue
                try:
                    await sessao.execute(tabela.insert().values(**valores))
                    pks_locais.add(chave)
                    estatisticas["linhas_inseridas"] += 1
                except Exception as erro:
                    estatisticas["erros"] += 1
                    logger.warning(
                        "[api-db] insert em %s falhou (%s): %s",
                        nome_tabela,
                        chave,
                        erro,
                    )

            # Ajusta sequence de colunas serial/identity quando houver id numérico
            for coluna in tabela.primary_key.columns:
                if coluna.name not in tabela.c:
                    continue
                if (
                    str(coluna.type).upper().startswith("INTEGER")
                    or "SERIAL" in str(coluna.type).upper()
                ):
                    try:
                        await sessao.execute(
                            text(
                                f"SELECT setval(pg_get_serial_sequence(:tab, :col), "
                                f"COALESCE((SELECT MAX({coluna.name}) FROM "
                                f"{nome_tabela}), 1))"
                            ),
                            {"tab": nome_tabela, "col": coluna.name},
                        )
                    except Exception as erro_ao_ajustar_sequencia:
                        # Nem toda chave primaria e "serial" (contador
                        # automatico). Quando nao e, o setval acima falha e
                        # isso e esperado: nao existe contador para ajustar.
                        # Registro em nivel debug so para deixar rastro.
                        logging.debug(
                            "Sem contador automatico para ajustar em %s.%s: %s",
                            nome_tabela,
                            coluna.name,
                            erro_ao_ajustar_sequencia,
                        )

        await sessao.commit()

    return estatisticas


async def sincronizar_banco_com_api() -> dict[str, Any]:
    """
    Orquestra export → push (merge) → pull → restore faltantes.

    Retorno descritivo para logs do cog.
    """
    if not CMSVALLEY_API_URL:
        return {
            "ok": False,
            "motivo": "CMSVALLEY_API_URL não configurada",
        }

    snapshot_local = await exportar_snapshot_banco()
    meta_remota = await obter_meta_remoto()

    hash_local = snapshot_local.get("hash_conteudo")
    hash_remoto = (meta_remota or {}).get("hash_conteudo")

    resultado_sync = None
    # Sempre tenta merge se houver linhas locais — a API só adiciona o que falta
    contagem_local = sum(
        len((bloco or {}).get("linhas") or [])
        for bloco in (snapshot_local.get("tabelas") or {}).values()
    )
    if contagem_local > 0:
        if hash_local != hash_remoto:
            resultado_sync = await enviar_snapshot_para_api(snapshot_local)
        else:
            resultado_sync = {
                "estatisticas": {
                    "houve_mudanca": False,
                    "motivo_local": "hash idêntico ao remoto",
                },
                "meta": meta_remota,
            }
    else:
        resultado_sync = {
            "estatisticas": {
                "houve_mudanca": False,
                "motivo_local": "banco local vazio — só restore",
            },
            "meta": meta_remota,
        }

    snapshot_remoto = await obter_snapshot_remoto()
    if not snapshot_remoto or not (snapshot_remoto.get("tabelas") or {}):
        return {
            "ok": True,
            "motivo": "cofre remoto vazio ou inacessível; push feito se havia dados "
            "locais",
            "push": resultado_sync,
            "restore": None,
            "hash_local": hash_local,
            "hash_remoto": hash_remoto,
        }

    restore = await restaurar_faltantes_no_banco(snapshot_remoto)
    return {
        "ok": True,
        "motivo": "sincronização concluída (merge aditivo + restore faltantes)",
        "push": resultado_sync,
        "restore": restore,
        "hash_local": hash_local,
        "hash_remoto_apos": (resultado_sync or {}).get("meta", {}).get("hash_conteudo")
        or hash_remoto,
    }
