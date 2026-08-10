"""Comandos de barra do domínio laudos."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.laudos.laudos_service import (
    buscar_consulta_aberta,
    cancelar_consulta_aberta,
    contar_laudos_psicologo,
    membro_e_psicologo,
)
from src.utils.formatacao import formatar_data_hora_local
from src.utils.mensagens import responder_erro, responder_info, responder_sucesso
from src.utils.permissions import apenas_administrador


class LaudosCog(commands.Cog):
    """Consultas e laudos psicológicos."""

    grupo_laudos = app_commands.Group(
        name="laudos",
        description="Consultas e laudos psicológicos",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_laudos.command(
        name="consulta",
        description="Mostra sua consulta aberta (se houver)",
    )
    async def laudos_consulta(self, interacao: discord.Interaction):
        if not isinstance(interacao.user, discord.Member) or not membro_e_psicologo(
            interacao.user
        ):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas psicólogos podem usar este comando."],
            )
            return

        consulta = await buscar_consulta_aberta(interacao.user.id)
        if consulta is None:
            await responder_info(
                interacao,
                titulo="Nenhuma consulta aberta",
                linhas=["Inicie pelo painel **Iniciar Consulta**."],
            )
            return

        await responder_info(
            interacao,
            titulo=f"Consulta aberta #{consulta.id}",
            linhas=[
                f"**Paciente:** <@{consulta.discord_id_paciente}>",
                f"**Passaporte paciente:** `{consulta.id_fivem_paciente or '—'}`",
                f"**Seu passaporte:** `{consulta.id_fivem_psicologo or '—'}`",
                f"**Início:** `{formatar_data_hora_local(consulta.iniciada_em)}`",
            ],
            delay=30,
        )

    @grupo_laudos.command(
        name="cancelar",
        description="Cancela sua consulta aberta",
    )
    async def laudos_cancelar(self, interacao: discord.Interaction):
        if not isinstance(interacao.user, discord.Member) or not membro_e_psicologo(
            interacao.user
        ):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas psicólogos podem usar este comando."],
            )
            return
        ok, mensagem = await cancelar_consulta_aberta(interacao.user.id)
        if ok:
            await responder_sucesso(interacao, titulo="Consulta cancelada", linhas=[mensagem])
        else:
            await responder_erro(interacao, titulo="Não cancelado", linhas=[mensagem])

    @grupo_laudos.command(
        name="meus",
        description="Quantidade de laudos que você já emitiu",
    )
    async def laudos_meus(self, interacao: discord.Interaction):
        if not isinstance(interacao.user, discord.Member) or not membro_e_psicologo(
            interacao.user
        ):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas psicólogos podem usar este comando."],
            )
            return
        total = await contar_laudos_psicologo(interacao.user.id)
        await responder_info(
            interacao,
            titulo="Seus laudos",
            linhas=[f"Total emitidos: **{total}**"],
        )

    @grupo_laudos.command(
        name="total",
        description="[Admin] Total de laudos de um psicólogo",
    )
    @app_commands.describe(membro="Psicólogo")
    @apenas_administrador()
    async def laudos_total(self, interacao: discord.Interaction, membro: discord.Member):
        total = await contar_laudos_psicologo(membro.id)
        await responder_info(
            interacao,
            titulo=f"Laudos · {membro.display_name}",
            linhas=[f"{membro.mention} emitiu **{total}** laudo(s)."],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(LaudosCog(bot))
