"""Comandos admin do domínio de cursos."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import delete

from src.cursos.cursos_setup import garantir_painel_cursos
from src.cursos.cursos_views import view_persistente_cursos
from src.database.connection import async_session
from src.database.models import PainelPostado
from src.utils.mensagens import responder_sucesso
from src.utils.permissions import apenas_administrador


class CursosCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="painel-cursos",
        description="Republica o painel de solicitar cursos (admin)",
    )
    @apenas_administrador()
    async def painel_cursos(self, interacao: discord.Interaction):
        await interacao.response.defer(ephemeral=True)
        async with async_session() as sessao:
            await sessao.execute(
                delete(PainelPostado).where(PainelPostado.nome_painel == "cursos")
            )
            await sessao.commit()
        await garantir_painel_cursos(self.bot, interacao)
        await responder_sucesso(
            interacao,
            titulo="Painel de cursos",
            linhas=["Painel republicado no canal configurado."],
            delay=12,
        )


async def setup(bot: commands.Bot):
    bot.add_view(view_persistente_cursos())
    await bot.add_cog(CursosCog(bot))
