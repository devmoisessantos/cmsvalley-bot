"""Comandos de recuperação de dados a partir dos canais de LOG."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.backup.recuperacao_logs_service import (
    id_canal_log_plantao,
    importar_log_plantao_do_canal,
)
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
)
from src.utils.permissions import is_authorized

logger = logging.getLogger(__name__)


class RecuperacaoLogsCog(commands.Cog):
    """Importa histórico dos canais de log para o banco (pós-perda de dados)."""

    grupo = app_commands.Group(
        name="recuperar",
        description="Recuperar dados a partir dos canais de LOG (admin)",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo.command(
        name="plantao",
        description="Importa mensagens do LOG_PLANTAO para a tabela log_plantao",
    )
    @app_commands.describe(
        limite="Máximo de mensagens a ler (vazio = todas que a API permitir)",
        so_bot="Se True, só mensagens do próprio bot",
    )
    @is_authorized()
    async def recuperar_plantao(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        await interacao.response.defer(ephemeral=True)

        canal_id = id_canal_log_plantao()
        if not canal_id:
            await responder_erro(
                interacao,
                titulo="Canal não configurado",
                linhas=["`CANAIS['LOG_PLANTAO']` não está definido no config."],
            )
            return

        canal = interacao.guild.get_channel(canal_id) if interacao.guild else None
        if canal is None:
            await responder_erro(
                interacao,
                titulo="Canal não encontrado",
                linhas=[f"ID `{canal_id}` não existe nesta guilda."],
            )
            return

        await responder_sucesso(
            interacao,
            titulo="Recuperação iniciada",
            linhas=[
                f"Lendo <#{canal_id}>…",
                "Isso pode levar vários minutos se o histórico for grande.",
                "Não rode o comando de novo até terminar.",
            ],
            delay=20,
        )

        apenas_bot = self.bot.user.id if so_bot and self.bot.user else None

        try:
            resultado = await importar_log_plantao_do_canal(
                canal,
                limite=limite,
                apenas_bot_id=apenas_bot,
            )
        except Exception as erro:
            logger.exception("recuperar plantao: %s", erro)
            await interacao.followup.send(
                f"❌ Falha na importação: `{erro}`",
                ephemeral=True,
            )
            return

        await interacao.followup.send(
            content=(
                "**Recuperação LOG_PLANTAO concluída**\n"
                f"• Mensagens lidas: **{resultado['lidas']}**\n"
                f"• Importadas: **{resultado['importadas']}**\n"
                f"• Já existiam: **{resultado['ja_existiam']}**\n"
                f"• Ignoradas (sem parse): **{resultado['ignoradas']}**\n"
                f"• Erros: **{resultado['erros']}**\n\n"
                "Horas de plantão passam a contar no ranking / promoção "
                "a partir desses registros."
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RecuperacaoLogsCog(bot))
