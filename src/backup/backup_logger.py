"""Logger simples de backup: envia embeds para um canal de texto do servidor.

O nome do canal vem de LOG_CHANNEL_NAME no config (padrão: backup-logs).
Se o canal não existir ou o bot não puder escrever, cai no print do console.
"""

from __future__ import annotations

import datetime

import discord


class BackupLogger:
    """Envia logs detalhados para #backup-logs (ou o nome configurado)."""

    def __init__(self, nome_canal_log: str) -> None:
        self.nome_canal_log = nome_canal_log

    def _encontrar_canal(self, guild: discord.Guild) -> discord.TextChannel | None:
        return discord.utils.get(guild.text_channels, name=self.nome_canal_log)

    async def log(
        self,
        guild: discord.Guild,
        titulo: str,
        descricao: str,
        cor: discord.Color = discord.Color.blurple(),
        autor: str | None = None,
    ) -> None:
        canal = self._encontrar_canal(guild)
        embed = discord.Embed(
            title=titulo,
            description=(descricao or "Sem detalhes.")[:4000],
            color=cor,
            timestamp=datetime.datetime.utcnow(),
        )
        if autor:
            embed.set_footer(text=f"Executado por {autor}")

        if canal is not None:
            try:
                await canal.send(embed=embed)
            except discord.Forbidden:
                print(
                    f"[AVISO] Sem permissão para enviar no canal #{self.nome_canal_log}"
                )
        else:
            print(
                f"[AVISO] Canal de log '#{self.nome_canal_log}' "
                f"não encontrado em {guild.name}"
            )

        trecho = (descricao or "")[:200]
        print(f"[LOG] {titulo}: {trecho}")
