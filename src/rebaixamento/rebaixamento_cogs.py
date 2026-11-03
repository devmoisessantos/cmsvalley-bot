"""Comando /rebaixar — painel efêmero de rebaixamento hospitalar."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.rebaixamento.rebaixamento_service import membro_pode_rebaixar
from src.rebaixamento.rebaixamento_views import (
    PainelBuscaRebaixarLayout,
    abrir_fluxo_rebaixamento,
)
from src.utils.mensagens import (
    mensagem_sem_permissao,
    responder_erro,
    responder_view,
)

registrador = logging.getLogger(__name__)


class RebaixamentoCog(commands.Cog):
    """Slash commands do domínio de rebaixamento."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="rebaixar",
        description="Rebaixa um membro da hierarquia hospitalar (Diretor++)",
    )
    @app_commands.describe(
        membro="Membro a rebaixar (opcional — se omitir, abre busca)",
    )
    async def rebaixar(
        self,
        interacao: discord.Interaction,
        membro: discord.Member | None = None,
    ) -> None:
        """
        Abre o fluxo de rebaixamento.

        Com membro: vai direto para escolha de cargo.
        Sem membro: painel de busca (UserSelect / Discord ID / FiveM).
        """
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto",
                linhas=["Use este comando dentro do servidor."],
            )
            return
        if not membro_pode_rebaixar(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("rebaixar membros (Diretor++)")],
            )
            return

        if membro is not None:
            await abrir_fluxo_rebaixamento(interacao, membro)
            return

        await responder_view(
            interacao,
            PainelBuscaRebaixarLayout(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    """Registra o cog de rebaixamento."""
    await bot.add_cog(RebaixamentoCog(bot))
    registrador.info("Cog de rebaixamento carregado.")
