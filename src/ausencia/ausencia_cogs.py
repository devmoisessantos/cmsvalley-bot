# src/ausencia/ausencia_cogs.py
"""Comandos e registro de views da ausência."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from src.ausencia.ausencia_panel import (
    CUSTOM_ID_APROVAR,
    CUSTOM_ID_APROVAR_RETORNO,
    CUSTOM_ID_REPROVAR,
    CUSTOM_ID_REPROVAR_RETORNO,
    PainelAusenciaLayout,
    processar_decisao_ausencia,
    processar_decisao_retorno,
)
from src.ausencia.ausencia_setup import garantir_painel_ausencia
from src.config import CANAIS
from src.database.connection import async_session
from src.database.models import PainelPostado
from src.utils.mensagens import responder_sucesso
from src.utils.permissions import is_authorized


class AusenciaCogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Painel persistente (botão fixo)
        self.bot.add_view(PainelAusenciaLayout())

    @app_commands.command(
        name="painel-ausencia",
        description="[Admin] Publica o painel de solicitar ausência",
    )
    @app_commands.default_permissions(administrator=True)
    async def painel_ausencia(self, interacao: discord.Interaction):
        if not is_authorized(interacao.user):
            await interacao.response.send_message("Sem permissão.", ephemeral=True)
            return
        await interacao.response.defer(ephemeral=True)

        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(PainelPostado).where(PainelPostado.nome_painel == "ausencia")
            )
            existente = resultado.scalar_one_or_none()
            if existente is not None:
                await sessao.delete(existente)
                await sessao.commit()

        await garantir_painel_ausencia(self.bot, interacao)
        canal_id = CANAIS.get("CANAL_REGISTRAR_AUSENCIA") or 0
        await responder_sucesso(
            interacao,
            titulo="Painel de ausência",
            linhas=[
                f"Publicado (ou tentado) em <#{canal_id}>."
                if canal_id
                else "Configure `CANAL_REGISTRAR_AUSENCIA` no config.py."
            ],
            delay=15,
        )

    @commands.Cog.listener()
    async def on_interaction(self, interacao: discord.Interaction):
        """Botões de aprovar/recusar (ausência e retorno) após restart."""
        if interacao.type is not discord.InteractionType.component:
            return
        data = interacao.data or {}
        custom_id = str(data.get("custom_id") or "")

        # Retorno precisa vir antes de "ausencia:aprovar:" (prefixo comum)
        if custom_id.startswith(CUSTOM_ID_APROVAR_RETORNO):
            try:
                pedido_id = int(custom_id[len(CUSTOM_ID_APROVAR_RETORNO) :])
            except ValueError:
                return
            if pedido_id <= 0 or interacao.response.is_done():
                return
            await processar_decisao_retorno(interacao, pedido_id, aprovada=True)
        elif custom_id.startswith(CUSTOM_ID_REPROVAR_RETORNO):
            try:
                pedido_id = int(custom_id[len(CUSTOM_ID_REPROVAR_RETORNO) :])
            except ValueError:
                return
            if pedido_id <= 0 or interacao.response.is_done():
                return
            await processar_decisao_retorno(interacao, pedido_id, aprovada=False)
        elif custom_id.startswith(CUSTOM_ID_APROVAR):
            try:
                pedido_id = int(custom_id[len(CUSTOM_ID_APROVAR) :])
            except ValueError:
                return
            if pedido_id <= 0 or interacao.response.is_done():
                return
            await processar_decisao_ausencia(interacao, pedido_id, aprovada=True)
        elif custom_id.startswith(CUSTOM_ID_REPROVAR):
            try:
                pedido_id = int(custom_id[len(CUSTOM_ID_REPROVAR) :])
            except ValueError:
                return
            if pedido_id <= 0 or interacao.response.is_done():
                return
            await processar_decisao_ausencia(interacao, pedido_id, aprovada=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(AusenciaCogs(bot))
