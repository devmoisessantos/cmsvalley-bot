"""Comandos admin do domínio de promoções."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import delete

from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.promocoes.promocoes_setup import garantir_painel_promocao
from src.promocoes.promocoes_views import view_persistente_promocao
from src.utils.mensagens import responder_sucesso
from src.utils.permissions import apenas_administrador


class PromocoesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="painel-promocao",
        description="Republica o painel de solicitar promoção (admin)",
    )
    @apenas_administrador()
    async def painel_promocao(self, interacao: discord.Interaction):
        """
        Apaga o registro do painel de promocao e o publica de novo.

        Serve para consertar o painel quando a mensagem original foi apagada do canal.
        O bot guarda numa tabela qual painel ja foi postado; enquanto esse registro
        existe, ele nao posta outro. Este comando remove o registro e obriga a
        republicacao.

        Mexe no banco e manda mensagem no canal. Responde so para quem usou o
        comando.
        """
        await interacao.response.defer(ephemeral=True)
        async with async_session() as sessao:
            await sessao.execute(
                delete(PainelPostado).where(PainelPostado.nome_painel == "promocao")
            )
            await sessao.commit()
        await garantir_painel_promocao(self.bot, interacao)
        await responder_sucesso(
            interacao,
            titulo="Painel de promoção",
            linhas=["Painel republicado no canal configurado."],
            delay=12,
        )


async def setup(bot: commands.Bot):
    bot.add_view(view_persistente_promocao())
    await bot.add_cog(PromocoesCog(bot))
