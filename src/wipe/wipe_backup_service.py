"""
Backup obrigatório do wipe: snapshot do Discord + backup forçado do banco.
"""

from __future__ import annotations

import logging
import os
from datetime import (
    datetime,
    timezone,
)
from typing import Any
from zoneinfo import ZoneInfo

from discord import Guild

from src.backup.backup_do_banco_service import (
    _exportar_tabelas_json,
    _limpar_backups_banco_antigos,
    _parse_database_url,
    _pasta_backup_banco,
    _tentar_pg_dump,
)
from src.backup.backup_gerenciador_service import BackupManager
from src.config import (
    DATABASE_URL,
    TIMEZONE_LOCAL,
)

registrador = logging.getLogger(__name__)


def montar_nome_da_temporada(agora: datetime | None = None) -> str:
    """
    Nome automático da temporada pela data local do servidor.

    Formato: AAAA-MM-DD (dia em que o wipe rodou).
    """
    momento = agora or datetime.now(timezone.utc)
    local = momento.astimezone(ZoneInfo(TIMEZONE_LOCAL))
    return local.strftime("%Y-%m-%d")


def criar_e_salvar_backup_do_discord(
    guilda: Guild,
    iniciador_nome: str,
    temporada: str,
) -> tuple[dict, str]:
    """
    Gera o snapshot completo do servidor e grava em disco.

    Devolve (dicionário do backup, caminho do arquivo).
    Levanta RuntimeError se o arquivo não puder ser validado.
    """
    gerenciador = BackupManager()
    backup = gerenciador.criar_backup(guilda, criado_por=iniciador_nome)
    backup["temporada"] = temporada
    backup["tipo"] = "wipe_temporada"
    caminho = gerenciador.salvar_backup(backup)

    if not caminho:
        raise RuntimeError("Backup do Discord não retornou caminho de arquivo.")

    cargos = backup.get("roles") or []
    canais = backup.get("channels") or []
    membros = backup.get("members") or []
    if not cargos and not canais:
        raise RuntimeError(
            "Backup do Discord veio sem cargos e sem canais — abortando."
        )

    registrador.info(
        "[wipe] backup Discord em %s (cargos=%s canais=%s membros=%s temporada=%s)",
        caminho,
        len(cargos),
        len(canais),
        len(membros),
        temporada,
    )
    return backup, caminho


# Nome antigo: código que ainda importa este símbolo continua funcionando.
criar_e_salvar_backup_do_wipe = criar_e_salvar_backup_do_discord


async def criar_backup_forcado_do_banco() -> dict[str, Any]:
    """
    Sempre grava um backup novo do banco (não compara hash).

    Usado no /wipe backup: o wipe precisa do arquivo mesmo se o
    conteúdo for igual ao último backup automático.
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
    caminho_dump = os.path.join(pasta, f"db_wipe_{carimbo}.dump")
    caminho_json = os.path.join(pasta, f"db_wipe_{carimbo}.json")

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

    _limpar_backups_banco_antigos()
    registrador.info(
        "[wipe] backup forçado do banco em %s (metodo=%s)",
        caminho_novo,
        metodo,
    )
    return {
        "fez_backup": True,
        "motivo": "backup forçado do wipe",
        "caminho": caminho_novo,
        "metodo": metodo,
    }
