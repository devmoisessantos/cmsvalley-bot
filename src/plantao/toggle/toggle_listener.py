"""
Ouvinte do canal LOG_TOGGLE_MEDICO.

Toda mensagem nesse canal (webhook, bot da cidade ou texto) é parseada.
Eventos válidos passam pelo serviço de toggle, que decide se desliga o
plantão ou agenda o aviso de call.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from src.config import CANAIS
from src.plantao.toggle.toggle_parse import parsear_mensagem_toggle
from src.plantao.toggle.toggle_service import processar_eventos_toggle

registrador = logging.getLogger(__name__)


class ToggleListener(commands.Cog):
    """Escuta a log de toggle médico e complementa o plantão."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, mensagem: discord.Message) -> None:
        """
        Reage só ao canal configurado. Ignora mensagens do próprio bot
        para não processar eco de logs que nós mesmos postarmos.
        """
        canal_id = CANAIS.get("LOG_TOGGLE_MEDICO") or 0
        if canal_id <= 0:
            return
        if mensagem.channel.id != canal_id:
            return
        if mensagem.author.id == self.bot.user.id:
            return

        conteudo = mensagem.content or ""
        if not conteudo.strip() and mensagem.embeds:
            for embed in mensagem.embeds:
                if embed.description:
                    conteudo += "\n" + embed.description
                if embed.title:
                    conteudo = (embed.title or "") + "\n" + conteudo

        eventos = parsear_mensagem_toggle(conteudo)
        if not eventos:
            if conteudo.strip():
                registrador.debug(
                    "Toggle: mensagem sem eventos reconhecidos (msg %s)",
                    mensagem.id,
                )
            return

        guilda = mensagem.guild
        if guilda is None:
            return

        try:
            resultados = await processar_eventos_toggle(
                self.bot,
                guilda,
                eventos,
            )
            registrador.info(
                "Toggle: msg %s → %s evento(s) → %s",
                mensagem.id,
                len(eventos),
                resultados,
            )
        except Exception as erro:
            registrador.exception(
                "Toggle: erro ao processar msg %s: %s",
                mensagem.id,
                erro,
            )


async def setup(bot: commands.Bot) -> None:
    """Registra o ouvinte do toggle médico no bot."""
    await bot.add_cog(ToggleListener(bot))
