# src/demissao/demissao_cogs.py
"""Comandos e registro de views da demissão."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.config import CANAIS
from src.demissao.demissao_panel import (
    CUSTOM_ID_APROVAR,
    CUSTOM_ID_REPROVAR,
    PainelDemissaoLayout,
    processar_decisao_demissao,
)
from src.demissao.demissao_setup import garantir_painel_demissao
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
)
from src.utils.permissions import is_authorized


class DemissaoCogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Painel persistente (botão fixo)
        self.bot.add_view(PainelDemissaoLayout())

    @app_commands.command(
        name="painel-demissao",
        description="[Admin] Publica o painel de solicitar demissão",
    )
    @app_commands.default_permissions(administrator=True)
    async def painel_demissao(self, interacao: discord.Interaction):
        """Força a publicação de uma nova referência para o painel de desligamento.

        Confere a autorização e remove do banco a referência anterior antes de
        chamar o publicador idempotente. Isso evita que uma mensagem antiga seja
        mantida como painel ativo depois de uma republicação administrativa.
        """
        if not is_authorized(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Sem permissão.",
                ],
            )
            return
        await interacao.response.defer(ephemeral=True)
        # Força republicação apagando registro? garantir_ é idempotente —
        # se já existe, só informa.
        from sqlalchemy import select

        from src.database.conexao import async_session
        from src.database.models import PainelPostado

        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(PainelPostado).where(PainelPostado.nome_painel == "demissao")
            )
            existente = resultado.scalar_one_or_none()
            if existente is not None:
                await sessao.delete(existente)
                await sessao.commit()

        await garantir_painel_demissao(self.bot, interacao)
        canal_id = CANAIS.get("CANAL_PAINEL_DEMISSAO") or 0
        await responder_sucesso(
            interacao,
            titulo="Painel de demissão",
            linhas=[
                f"Publicado (ou tentado) em <#{canal_id}>."
                if canal_id
                else "Configure `CANAL_PAINEL_DEMISSAO` no config.py."
            ],
            delay=15,
        )

    @commands.Cog.listener()
    async def on_interaction(self, interacao: discord.Interaction):
        """Botões de aprovar/recusar após restart (custom_id dinâmico)."""
        if interacao.type is not discord.InteractionType.component:
            return
        data = interacao.data or {}
        custom_id = str(data.get("custom_id") or "")
        if custom_id.startswith(CUSTOM_ID_APROVAR):
            try:
                pedido_id = int(custom_id[len(CUSTOM_ID_APROVAR) :])
            except ValueError:
                return
            if pedido_id <= 0 or interacao.response.is_done():
                return
            await processar_decisao_demissao(interacao, pedido_id, aprovada=True)
        elif custom_id.startswith(CUSTOM_ID_REPROVAR):
            try:
                pedido_id = int(custom_id[len(CUSTOM_ID_REPROVAR) :])
            except ValueError:
                return
            if pedido_id <= 0 or interacao.response.is_done():
                return
            await processar_decisao_demissao(interacao, pedido_id, aprovada=False)


async def setup(bot: commands.Bot):
    """Registra os comandos e a visualização persistente de desligamento."""
    await bot.add_cog(DemissaoCogs(bot))
