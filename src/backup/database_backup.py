# src/backup/database_backup.py
"""
Backup full do PostgreSQL usado pelo bot.

Estratégia:
  1. Tenta `pg_dump` (formato custom .dump) — ideal para restore completo.
  2. Se pg_dump não existir no ambiente, exporta todas as tabelas
     mapeadas no SQLAlchemy para um JSON (recuperação de dados do bot).

Em ambos os casos:
  - Compara o hash do arquivo novo com o backup de banco mais recente.
  - Se for idêntico, apaga o arquivo novo e não faz nada (silencioso).
  - Se mudou (ou é o primeiro), mantém o arquivo e limpa os antigos.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import (
    datetime,
    timezone,
)
from typing import Any
from urllib.parse import (
    unquote,
    urlparse,
)

from src.config import (
    BACKUP_DIR,
    DATABASE_URL,
    MAX_BACKUPS_PER_GUILD,
)


def _pasta_backup_banco() -> str:
    caminho = os.path.join(BACKUP_DIR, "database")
    os.makedirs(caminho, exist_ok=True)
    return caminho


def _parse_database_url(url: str | None) -> dict[str, str] | None:
    """
    Extrai host, porta, usuário, senha e nome do banco de DATABASE_URL.
    Aceita formas: postgresql://… e postgresql+asyncpg://…
    """
    if not url:
        return None
    url_normalizada = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    url_normalizada = url_normalizada.replace("postgres+asyncpg://", "postgresql://", 1)
    if url_normalizada.startswith("postgres://"):
        url_normalizada = "postgresql://" + url_normalizada[len("postgres://") :]

    analisada = urlparse(url_normalizada)
    if not analisada.hostname or not analisada.path:
        return None

    nome_banco = analisada.path.lstrip("/").split("?")[0]
    if not nome_banco:
        return None

    return {
        "host": analisada.hostname or "localhost",
        "port": str(analisada.port or 5432),
        "user": unquote(analisada.username or ""),
        "password": unquote(analisada.password or ""),
        "database": nome_banco,
    }


def _hash_arquivo(caminho: str) -> str | None:
    if not os.path.isfile(caminho):
        return None
    calculador = hashlib.sha256()
    with open(caminho, "rb") as arquivo:
        while True:
            bloco = arquivo.read(1024 * 1024)
            if not bloco:
                break
            calculador.update(bloco)
    return calculador.hexdigest()


def _listar_backups_banco() -> list[str]:
    pasta = _pasta_backup_banco()
    nomes = [
        nome
        for nome in os.listdir(pasta)
        if nome.startswith("db_") and (nome.endswith(".dump") or nome.endswith(".json"))
    ]
    return sorted(nomes, reverse=True)


def _limpar_backups_banco_antigos() -> None:
    pasta = _pasta_backup_banco()
    arquivos = _listar_backups_banco()
    for nome_antigo in arquivos[MAX_BACKUPS_PER_GUILD:]:
        try:
            os.remove(os.path.join(pasta, nome_antigo))
        except OSError:
            pass


def _backup_banco_mais_recente() -> str | None:
    arquivos = _listar_backups_banco()
    return arquivos[0] if arquivos else None


def _tentar_pg_dump(dados_conexao: dict[str, str], caminho_destino: str) -> bool:
    """
    Executa pg_dump em formato custom (-Fc).
    Retorna True se o arquivo foi gerado com sucesso.
    """
    executavel = shutil.which("pg_dump")
    if not executavel:
        return False

    ambiente = os.environ.copy()
    if dados_conexao.get("password"):
        ambiente["PGPASSWORD"] = dados_conexao["password"]

    comando = [
        executavel,
        "-h",
        dados_conexao["host"],
        "-p",
        dados_conexao["port"],
        "-U",
        dados_conexao["user"],
        "-d",
        dados_conexao["database"],
        "-Fc",
        "--no-owner",
        "--no-acl",
        "-f",
        caminho_destino,
    ]

    try:
        resultado = subprocess.run(
            comando,
            env=ambiente,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as erro:
        print(f"[backup-db] pg_dump falhou: {erro}")
        return False

    if resultado.returncode != 0:
        print(f"[backup-db] pg_dump erro: {resultado.stderr.strip()}")
        if os.path.isfile(caminho_destino):
            try:
                os.remove(caminho_destino)
            except OSError:
                pass
        return False

    return os.path.isfile(caminho_destino) and os.path.getsize(caminho_destino) > 0


async def _exportar_tabelas_json(caminho_destino: str) -> bool:
    """
    Fallback sem pg_dump: lê todas as tabelas do metadata SQLAlchemy
    e grava um JSON único (dados do bot, não o cluster Postgres inteiro).
    """
    try:
        from sqlalchemy import (
            select,
            text,
        )

        from src.database.connection import async_session
        from src.database.models import Base
    except Exception as erro:
        print(f"[backup-db] Import do ORM falhou: {erro}")
        return False

    payload: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "formato": "json-orm",
        "tabelas": {},
    }

    try:
        async with async_session() as sessao:
            for tabela in Base.metadata.sorted_tables:
                nome_tabela = tabela.name
                resultado = await sessao.execute(select(tabela))
                linhas = []
                for linha in resultado.mappings().all():
                    registro = {}
                    for chave, valor in dict(linha).items():
                        if isinstance(valor, datetime):
                            registro[chave] = valor.isoformat()
                        elif isinstance(valor, (bytes, bytearray)):
                            registro[chave] = valor.hex()
                        else:
                            # JSON-serializável ou string de fallback
                            try:
                                json.dumps(valor)
                                registro[chave] = valor
                            except TypeError:
                                registro[chave] = str(valor)
                    linhas.append(registro)
                payload["tabelas"][nome_tabela] = linhas

            # Marca versão do schema de forma leve
            try:
                contagem = await sessao.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
                payload["quantidade_tabelas_public"] = int(contagem.scalar() or 0)
            except Exception:
                pass
    except Exception as erro:
        print(f"[backup-db] Export JSON falhou: {erro}")
        return False

    try:
        with open(caminho_destino, "w", encoding="utf-8") as arquivo:
            json.dump(payload, arquivo, ensure_ascii=False, indent=2)
    except OSError as erro:
        print(f"[backup-db] Não foi possível gravar JSON: {erro}")
        return False

    return os.path.isfile(caminho_destino) and os.path.getsize(caminho_destino) > 0


async def fazer_backup_banco_se_mudou() -> dict[str, Any]:
    """
    Gera backup full do banco só se o conteúdo mudou em relação ao último arquivo.

    Retorno:
      {
        "fez_backup": bool,
        "motivo": str,
        "caminho": str | None,
        "metodo": "pg_dump" | "json-orm" | None,
      }
    """
    dados_conexao = _parse_database_url(DATABASE_URL)
    if dados_conexao is None:
        return {
            "fez_backup": False,
            "motivo": "DATABASE_URL inválida ou ausente",
            "caminho": None,
            "metodo": None,
        }

    pasta = _pasta_backup_banco()
    carimbo = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    caminho_dump = os.path.join(pasta, f"db_{carimbo}.dump")
    caminho_json = os.path.join(pasta, f"db_{carimbo}.json")

    metodo: str | None = None
    caminho_novo: str | None = None

    if _tentar_pg_dump(dados_conexao, caminho_dump):
        metodo = "pg_dump"
        caminho_novo = caminho_dump
    else:
        ok_json = await _exportar_tabelas_json(caminho_json)
        if not ok_json:
            return {
                "fez_backup": False,
                "motivo": "pg_dump indisponível e export JSON falhou",
                "caminho": None,
                "metodo": None,
            }
        metodo = "json-orm"
        caminho_novo = caminho_json

    hash_novo = _hash_arquivo(caminho_novo)
    nome_anterior = _backup_banco_mais_recente()
    # O mais recente listado pode ser o arquivo que acabamos de criar —
    # pega o segundo se o primeiro for o atual.
    arquivos = _listar_backups_banco()
    hash_anterior = None
    for nome in arquivos:
        caminho_candidato = os.path.join(pasta, nome)
        if os.path.abspath(caminho_candidato) == os.path.abspath(caminho_novo):
            continue
        # Só compara com o mesmo tipo de arquivo (.dump com .dump, .json com .json)
        if metodo == "pg_dump" and not nome.endswith(".dump"):
            continue
        if metodo == "json-orm" and not nome.endswith(".json"):
            continue
        hash_anterior = _hash_arquivo(caminho_candidato)
        break

    if hash_anterior is not None and hash_novo == hash_anterior:
        try:
            os.remove(caminho_novo)
        except OSError:
            pass
        return {
            "fez_backup": False,
            "motivo": "sem alteração no banco (hash idêntico ao último backup)",
            "caminho": None,
            "metodo": metodo,
        }

    _limpar_backups_banco_antigos()
    return {
        "fez_backup": True,
        "motivo": "banco alterado ou primeiro backup",
        "caminho": caminho_novo,
        "metodo": metodo,
    }
