"""Comandos de recuperação de dados a partir dos canais de LOG."""

from __future__ import annotations

import logging
from collections.abc import (
    Awaitable,
    Callable,
)

import discord
from discord import app_commands
from discord.ext import commands

from src.backup.recuperacao_logs_service import (
    id_canal_log,
    id_canal_log_plantao,
    importar_log_aprovacoes_do_canal,
    importar_log_chamadas_do_canal,
    importar_log_plantao_do_canal,
    importar_log_punicoes_do_canal,
    importar_log_recrutamentos_do_canal,
    importar_log_reprovacoes_do_canal,
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

    async def _rodar_importacao(
        self,
        interacao: discord.Interaction,
        *,
        titulo: str,
        chave_canal: str,
        canal_id: int | None,
        importador: Callable[..., Awaitable[dict]],
        limite: int | None,
        so_bot: bool,
    ) -> None:
        await interacao.response.defer(ephemeral=True)

        if not canal_id:
            await responder_erro(
                interacao,
                titulo="Canal não configurado",
                linhas=[f"`CANAIS['{chave_canal}']` não está definido no config."],
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
            titulo=f"{titulo} — iniciada",
            linhas=[
                f"Lendo <#{canal_id}>…",
                "Pode levar vários minutos. Não rode de novo até terminar.",
            ],
            delay=20,
        )

        apenas_bot = self.bot.user.id if so_bot and self.bot.user else None

        try:
            resultado = await importador(
                canal,
                limite=limite,
                apenas_bot_id=apenas_bot,
            )
        except Exception as erro:
            logger.exception("%s: %s", titulo, erro)
            await interacao.followup.send(
                f"❌ Falha na importação: `{erro}`",
                ephemeral=True,
            )
            return

        atualizadas = resultado.get("atualizadas", 0)
        resumo = (
            f"**{titulo} concluída**\n"
            f"• Mensagens lidas: **{resultado['lidas']}**\n"
            f"• Criadas: **{resultado['importadas']}**\n"
            f"• Atualizadas: **{atualizadas}**\n"
            f"• Já existiam: **{resultado['ja_existiam']}**\n"
            f"• Ignoradas (sem parse): **{resultado['ignoradas']}**\n"
            f"• Erros: **{resultado['erros']}**"
        )
        try:
            await interacao.followup.send(content=resumo, ephemeral=True)
        except discord.HTTPException:
            if interacao.channel:
                await interacao.channel.send(f"{interacao.user.mention}\n{resumo}")

    @grupo.command(name="plantao", description="LOG_PLANTAO → log_plantao")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só mensagens do bot")
    @is_authorized()
    async def recuperar_plantao(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_PLANTAO",
            chave_canal="LOG_PLANTAO",
            canal_id=id_canal_log_plantao(),
            importador=importar_log_plantao_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(
        name="recrutamentos",
        description="LOG_RECRUTAMENTOS → inícios (ESTUDANDO + FID)",
    )
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só mensagens do bot")
    @is_authorized()
    async def recuperar_recrutamentos(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_RECRUTAMENTOS",
            chave_canal="LOG_RECRUTAMENTOS",
            canal_id=id_canal_log("LOG_RECRUTAMENTOS"),
            importador=importar_log_recrutamentos_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(
        name="aprovacoes",
        description="LOG_APROVACOES → APROVADO + ENFERMEIRO/PARAMEDICO + nota",
    )
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só mensagens do bot")
    @is_authorized()
    async def recuperar_aprovacoes(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_APROVACOES",
            chave_canal="LOG_APROVACOES",
            canal_id=id_canal_log("LOG_APROVACOES"),
            importador=importar_log_aprovacoes_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(
        name="reprovacoes",
        description="LOG_REPROVACOES → REPROVADO + nota",
    )
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só mensagens do bot")
    @is_authorized()
    async def recuperar_reprovacoes(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_REPROVACOES",
            chave_canal="LOG_REPROVACOES",
            canal_id=id_canal_log("LOG_REPROVACOES"),
            importador=importar_log_reprovacoes_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(name="punicoes", description="LOG_PUNICOES → punicoes")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só mensagens do bot")
    @is_authorized()
    async def recuperar_punicoes(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_PUNICOES",
            chave_canal="LOG_PUNICOES",
            canal_id=id_canal_log("LOG_PUNICOES"),
            importador=importar_log_punicoes_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(name="chamadas", description="LOG_CHAMADAS → chamadas")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só mensagens do bot")
    @is_authorized()
    async def recuperar_chamadas(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_CHAMADAS",
            chave_canal="LOG_CHAMADAS",
            canal_id=id_canal_log("LOG_CHAMADAS"),
            importador=importar_log_chamadas_do_canal,
            limite=limite,
            so_bot=so_bot,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RecuperacaoLogsCog(bot))
