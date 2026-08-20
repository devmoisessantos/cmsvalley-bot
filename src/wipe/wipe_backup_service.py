"""
Backup obrigatório antes de qualquer destruição no wipe.

Reaproveita o gerenciador de backup já existente no domínio backup/,
e grava também um carimbo da temporada no JSON.
"""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)
from zoneinfo import ZoneInfo

from discord import Guild

from src.backup.backup_gerenciador_service import BackupManager
from src.config import TIMEZONE_LOCAL

registrador = logging.getLogger(__name__)


def montar_nome_da_temporada(agora: datetime | None = None) -> str:
    """
    Nome automático da temporada pela data local do servidor.

    Formato: AAAA-MM-DD (dia em que o wipe rodou).
    """
    momento = agora or datetime.now(timezone.utc)
    local = momento.astimezone(ZoneInfo(TIMEZONE_LOCAL))
    return local.strftime("%Y-%m-%d")


def criar_e_salvar_backup_do_wipe(
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
        raise RuntimeError("Backup do wipe não retornou caminho de arquivo.")

    cargos = backup.get("roles") or []
    canais = backup.get("channels") or []
    membros = backup.get("members") or []
    if not cargos and not canais:
        raise RuntimeError(
            "Backup do wipe veio sem cargos e sem canais — abortando por segurança."
        )

    registrador.info(
        "[wipe] backup salvo em %s (cargos=%s canais=%s membros=%s temporada=%s)",
        caminho,
        len(cargos),
        len(canais),
        len(membros),
        temporada,
    )
    return backup, caminho
