"""Comandos de barra do domínio membros.

/cargos painel  — abre o painel interativo de adicionar/remover cargos
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.membros.cargos_panel import GerenciarCargosView
from src.membros.cargos_service import determinar_escopos
from src.utils.mensagens import (
    responder_erro,
    responder_view,
)


class MembrosCog(commands.Cog):
    """Comandos de gerenciamento de membros e cargos."""

    grupo_cargos = app_commands.Group(
        name="cargos",
        description="Gerenciamento de cargos de membros",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_cargos.command(
        name="painel",
        description="Abre o painel para adicionar ou remover cargos de um membro",
    )
    async def painel_cargos(self, interacao: discord.Interaction):
        """Abre um painel privado somente para quem possui escopos de gerenciamento.

        Calcula as permissões antes de criar a interface para não expor ações
        de cargos a membros sem autorização. O painel é efêmero, preservando a
        privacidade das alterações administrativas.
        """
        membro_executor = interacao.user
        escopos_do_executor = determinar_escopos(membro_executor)

        if not escopos_do_executor:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Você não possui permissão para usar este comando."],
            )
            return

        view_do_painel = GerenciarCargosView(membro_executor)
        await responder_view(
            interacao,
            view_do_painel,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    """Adiciona ao bot os comandos de gerenciamento de membros."""
    await bot.add_cog(MembrosCog(bot))
