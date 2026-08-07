# src/backup/backup_logger.py
"""
Logger do sistema de backup.

Envia logs no canal configurado com Components V2.
Aceita nome do canal ("backup-logs") ou ID numérico no .env.
Se o canal não existir ou o bot não puder escrever, registra no console.
"""

from __future__ import annotations

import discord

from src.config import LOG_BACKUP_CHANNEL
from src.utils.log_container import LogContainerView
from src.utils.mensagens import COR_INFO


class BackupLogger:
    """Envia logs detalhados para o canal de backup do servidor."""

    def __init__(self, canal_log: str) -> None:
        # Pode ser nome ("backup-logs") ou ID ("1523367...")
        self.canal_log = (canal_log or "").strip()

    def _encontrar_canal(self, guilda: discord.Guild) -> discord.TextChannel | None:
        if not self.canal_log:
            return None

        # ID numérico → busca direta
        if self.canal_log.isdigit():
            canal = guilda.get_channel(int(self.canal_log))
            if isinstance(canal, discord.TextChannel):
                return canal
            return None

        # Nome do canal
        return discord.utils.get(guilda.text_channels, name=self.canal_log)

    def mencao_do_canal(self, guilda: discord.Guild) -> str:
        """Texto amigável para cards: menção <#id> ou #nome."""
        self.canal_log = guilda.get_channel(LOG_BACKUP_CHANNEL)
        if self.canal_log:
            return f"<#{self.canal_log}>"

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
                f"[AVISO] Canal de log '{self.canal_log}' "
                f"não encontrado em {guild.name}"
            )

        trecho_no_console = (descricao or "")[:200]
        print(f"[LOG] {titulo}: {trecho_no_console}")
