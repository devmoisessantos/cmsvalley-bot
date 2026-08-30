"""Comandos administrativos do compartilhamento de tela."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.manutencao.manutencao_paineis import recriar_painel
from src.screenshare.screenshare_service import checar_saude
from src.utils.mensagens import responder_erro, responder_info, responder_sucesso
from src.utils.permissions import apenas_administrador


class ScreenshareCogs(commands.Cog):
    """Grupo /screenshare para administracao."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    screenshare = app_commands.Group(
        name="screenshare",
        description="Administracao do compartilhamento de tela",
    )

    @screenshare.command(
        name="status",
        description="Consulta o health da API de compartilhamento",
    )
    @apenas_administrador()
    async def comando_status(self, interacao: discord.Interaction):
        ok, detalhe = await checar_saude()
        if not ok:
            await responder_erro(
                interacao,
                titulo="API indisponivel",
                linhas=[str(detalhe)],
            )
            return
        if isinstance(detalhe, dict):
            linhas = [
                f"`{chave}`: `{valor}`" for chave, valor in detalhe.items()
            ]
        else:
            linhas = [str(detalhe)]
        await responder_info(
            interacao,
            titulo="Status screenshare",
            linhas=linhas,
        )

    @screenshare.command(
        name="painel",
        description="Republica o painel de compartilhamento no canal",
    )
    @apenas_administrador()
    async def comando_painel(self, interacao: discord.Interaction):
        resultado = await recriar_painel(self.bot, "screenshare")
        if not resultado.ok:
            await responder_erro(
                interacao,
                titulo="Falha ao republicar painel",
                linhas=[resultado.mensagem],
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Painel republicado",
            linhas=[resultado.mensagem],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ScreenshareCogs(bot))
