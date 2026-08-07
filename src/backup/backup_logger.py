# src/backup/backup_logger.py
"""
Logger do sistema de backup.

Envia logs no canal configurado (padrão: backup-logs) com Components V2.
Se o canal não existir ou o bot não puder escrever, registra no console.
"""

from __future__ import annotations

import discord

from src.utils.log_container import LogContainerView
from src.utils.mensagens import COR_INFO


class BackupLogger:
    """Envia logs detalhados para o canal de backup do servidor."""

    def __init__(self, nome_canal_log: str) -> None:
        self.nome_canal_log = nome_canal_log

    def _encontrar_canal(self, guilda: discord.Guild) -> discord.TextChannel | None:
        return discord.utils.get(guilda.text_channels, name=self.nome_canal_log)

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
                    f"[AVISO] Sem permissão para enviar no canal #{self.nome_canal_log}"
                )
        else:
            print(
                f"[AVISO] Canal de log '#{self.nome_canal_log}' "
                f"não encontrado em {guild.name}"
            )

        trecho_no_console = (descricao or "")[:200]
        print(f"[LOG] {titulo}: {trecho_no_console}")
