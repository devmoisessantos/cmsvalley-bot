# src/backup/backup_logger.py
"""
Logger do sistema de backup.

Envia logs no canal CANAIS["LOG_BACKUP"] com Components V2.
Se o canal não existir ou o bot não puder escrever, registra no console.
"""

from __future__ import annotations

import discord

from src.config import CANAIS
from src.utils.log_container import LogContainerView
from src.utils.mensagens import COR_INFO


class BackupLogger:
    """Envia logs detalhados para o canal de backup do servidor."""

    def __init__(self) -> None:
        self.id_do_canal_log = CANAIS["LOG_BACKUP"]

    def _encontrar_canal(self, guilda: discord.Guild) -> discord.TextChannel | None:
        canal = guilda.get_channel(self.id_do_canal_log)
        if isinstance(canal, discord.TextChannel):
            return canal
        return None

    def mencao_do_canal(self) -> str:
        """Menção clicável do canal de log de backup."""
        return f"<#{self.id_do_canal_log}>"

    async def log(
        self,
        guild: discord.Guild,
        titulo: str,
        descricao: str,
        cor: discord.Color = COR_INFO,
        autor: str | None = None,
    ) -> None:
        """
        Envia um log visual no canal de backup.

        O parâmetro `guild` mantém o nome antigo para não quebrar quem já chama.
        """
        canal = self._encontrar_canal(guild)
        texto_da_descricao = (descricao or "Sem detalhes.")[:4000]

        if autor:
            texto_das_linhas = f"{texto_da_descricao}\n\n-# Executado por {autor}"
        else:
            texto_das_linhas = texto_da_descricao

        if canal is not None:
            view_do_log = LogContainerView(
                titulo=titulo,
                linhas=texto_das_linhas,
                guild=guild,
                cor=cor,
            )
            try:
                await canal.send(view=view_do_log)
            except discord.Forbidden:
                print(
                    f"[AVISO] Sem permissão para enviar no canal "
                    f"{self.mencao_do_canal()}"
                )
        else:
            print(
                f"[AVISO] Canal de log {self.mencao_do_canal()} "
                f"não encontrado em {guild.name}"
            )

        trecho_no_console = (descricao or "")[:200]
        print(f"[LOG] {titulo}: {trecho_no_console}")
