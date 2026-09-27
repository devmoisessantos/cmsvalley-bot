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


def _deserializar_valor(valor: Any, tipo_da_coluna: Any) -> Any:
    """
    Converte o valor que veio do JSON de volta para o tipo que o Postgres espera.

    O export grava datetime/date como string ISO. No import, o asyncpg rejeita
    string em coluna TIMESTAMP/DATE — precisa de datetime.datetime de verdade.
    """
    if valor is None:
        return None

    nome_do_tipo = type(tipo_da_coluna).__name__.upper()
    texto_do_tipo = str(tipo_da_coluna).upper()

    eh_datetime = (
        "DATETIME" in nome_do_tipo
        or "TIMESTAMP" in texto_do_tipo
        or nome_do_tipo == "DATETIME"
    )
    eh_date = nome_do_tipo == "DATE" or (
        texto_do_tipo == "DATE" and "TIMESTAMP" not in texto_do_tipo
    )

    if eh_datetime and isinstance(valor, str):
        texto = valor.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(texto)
        except ValueError:
            return valor

    if eh_date and isinstance(valor, str):
        try:
            # ISO completo às vezes vem em coluna Date; pega só a parte da data
            if "T" in valor:
                return date.fromisoformat(valor.split("T", 1)[0])
            return date.fromisoformat(valor)
        except ValueError:
            return valor

    if isinstance(valor, datetime) or isinstance(valor, date):
        return valor

    return valor


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


# Quantas linhas entram em cada INSERT em lote no restore aditivo.
# Lote grande demais estoura parâmetros do asyncpg; lote pequeno demais
# deixa o import de banco vazio impraticável.
TAMANHO_DO_LOTE_DE_INSERT = 100


def _mensagem_eh_unique(erro: BaseException) -> bool:
    """Detecta violação de unique/duplicate key em mensagens do Postgres."""
    mensagem_do_erro = str(erro).lower()
    return (
        "uniqueviolation" in mensagem_do_erro
        or "unique constraint" in mensagem_do_erro
        or "duplicate key" in mensagem_do_erro
    )


def _montar_valores_da_linha(tabela, linha: dict[str, Any]) -> dict[str, Any]:
    """
    Monta o dicionário de colunas para INSERT a partir de uma linha do snapshot.

    Só usa colunas que existem na tabela atual e reconverte datas/números
    serializados em JSON.
    """
    valores: dict[str, Any] = {}
    for coluna in tabela.columns:
        if coluna.name not in linha:
            continue
        valor_bruto = linha.get(coluna.name)
        valores[coluna.name] = _deserializar_valor(valor_bruto, coluna.type)
    return valores


async def _ajustar_sequences_da_tabela(sessao, tabela, nome_tabela: str) -> None:
    """
    Alinha o contador serial/identity com o MAX atual da coluna.

    Depois de importar ids antigos, o próximo INSERT automático precisa
    continuar de onde o snapshot parou, senão o Postgres gera PK duplicada.
    """
    for coluna in tabela.primary_key.columns:
        if coluna.name not in tabela.c:
            continue
        tipo_texto = str(coluna.type).upper()
        if not (
            tipo_texto.startswith("INTEGER")
            or "SERIAL" in tipo_texto
            or "BIGINT" in tipo_texto
        ):
            continue
        try:
            async with sessao.begin_nested():
                await sessao.execute(
                    text(
                        f"SELECT setval("
                        f"pg_get_serial_sequence(:tab, :col), "
                        f"COALESCE((SELECT MAX({coluna.name}) "
                        f"FROM {nome_tabela}), 1))"
                    ),
                    {"tab": nome_tabela, "col": coluna.name},
                )
        except Exception as erro_ao_ajustar_sequencia:
            # Nem toda PK tem sequence. Falha esperada em chaves manuais.
            logging.debug(
                "Sem contador automatico para ajustar em %s.%s: %s",
                nome_tabela,
                coluna.name,
                erro_ao_ajustar_sequencia,
            )


async def restaurar_faltantes_no_banco(snapshot: dict[str, Any]) -> dict[str, int]:
    """
    Insere no Postgres local apenas linhas que ainda não existem (por PK).
    Nunca apaga nem atualiza registro já presente.

    Estratégia (pensada para banco novo / Fadehost vazio):
      1. Lê as PKs já existentes de cada tabela.
      2. Filtra as linhas do snapshot que faltam.
      3. Insere em lotes (TAMANHO_DO_LOTE_DE_INSERT).
      4. Faz commit por tabela — progresso parcial não se perde se o
         processo cair no meio.
      5. Se um lote inteiro falhar, tenta linha a linha com savepoint
         (unique secundária conta como "já existia").
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

    for nome_tabela, bloco in tabelas_snapshot.items():
        if not isinstance(bloco, dict):
            continue
        tabela = mapa_tabelas.get(nome_tabela)
        if tabela is None:
            # Tabela do snapshot que o código atual ainda não mapeia — ignora
            logger.info(
                "[api-db] tabela %s no snapshot não existe no ORM — ignorada",
                nome_tabela,
            )
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

        try:
            async with async_session() as sessao:
                colunas_pk = [
                    tabela.c[nome]
                    for nome in chaves_primarias
                    if nome in tabela.c
                ]
                pks_locais: set[tuple] = set()
                if colunas_pk:
                    resultado_local = await sessao.execute(select(*colunas_pk))
                    for registro in resultado_local.all():
                        pks_locais.add(tuple(registro))

                estatisticas["tabelas_tocadas"] += 1
                linhas_para_inserir: list[dict[str, Any]] = []
                for linha in linhas_remotas:
                    chave = _tupla_pk(linha, chaves_primarias)
                    if chave in pks_locais:
                        estatisticas["linhas_ja_existiam"] += 1
                        continue
                    valores = _montar_valores_da_linha(tabela, linha)
                    if not valores:
                        continue
                    linhas_para_inserir.append(valores)

                if not linhas_para_inserir:
                    await sessao.commit()
                    logger.info(
                        "[api-db] %s: nada novo (%s já existiam)",
                        nome_tabela,
                        len(linhas_remotas),
                    )
                    continue

                inseridas_nesta_tabela = 0
                total_lotes = (
                    len(linhas_para_inserir) + TAMANHO_DO_LOTE_DE_INSERT - 1
                ) // TAMANHO_DO_LOTE_DE_INSERT

                passo = TAMANHO_DO_LOTE_DE_INSERT
                for indice_lote in range(0, len(linhas_para_inserir), passo):
                    lote = linhas_para_inserir[indice_lote : indice_lote + passo]
                    numero_do_lote = (
                        indice_lote // TAMANHO_DO_LOTE_DE_INSERT
                    ) + 1
                    try:
                        async with sessao.begin_nested():
                            await sessao.execute(tabela.insert(), lote)
                        inseridas_nesta_tabela += len(lote)
                        estatisticas["linhas_inseridas"] += len(lote)
                    except Exception as erro_do_lote:
                        # Lote inteiro falhou (unique misto, tipo, etc.).
                        # Cai para linha a linha para não perder o resto.
                        logger.warning(
                            "[api-db] lote %s/%s em %s falhou (%s) — "
                            "tentando linha a linha",
                            numero_do_lote,
                            total_lotes,
                            nome_tabela,
                            erro_do_lote,
                        )
                        for valores in lote:
                            try:
                                async with sessao.begin_nested():
                                    await sessao.execute(
                                        tabela.insert().values(**valores)
                                    )
                                inseridas_nesta_tabela += 1
                                estatisticas["linhas_inseridas"] += 1
                            except Exception as erro_linha:
                                if _mensagem_eh_unique(erro_linha):
                                    estatisticas["linhas_ja_existiam"] += 1
                                else:
                                    estatisticas["erros"] += 1
                                    logger.warning(
                                        "[api-db] insert em %s falhou: %s",
                                        nome_tabela,
                                        erro_linha,
                                    )

                await _ajustar_sequences_da_tabela(sessao, tabela, nome_tabela)
                await sessao.commit()
                logger.info(
                    "[api-db] %s: +%s inseridas (de %s no snapshot)",
                    nome_tabela,
                    inseridas_nesta_tabela,
                    len(linhas_remotas),
                )
        except Exception as erro_da_tabela:
            estatisticas["erros"] += 1
            logger.exception(
                "[api-db] falha ao restaurar a tabela %s: %s",
                nome_tabela,
                erro_da_tabela,
            )

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
