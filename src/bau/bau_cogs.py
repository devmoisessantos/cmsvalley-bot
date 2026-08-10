"""Comandos administrativos do baú."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.bau.bau_service import chave_ciclo_atual, liberar_limite_manual
from src.config import LIMITES_BAU_CAMADA_1
from src.utils.mensagens import responder_erro, responder_info, responder_sucesso
from src.utils.permissions import is_authorized


class BauCog(commands.Cog):
    grupo_bau = app_commands.Group(
        name="bau",
        description="Monitoramento e exceções do baú do hospital",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_bau.command(
        name="liberar",
        description="Zera contador e fecha casos de um item (autorização pontual)",
    )
    @app_commands.describe(
        id_fivem="Passaporte FiveM",
        item="Item canônico (ex: celular, roupas, repairkit)",
    )
    @is_authorized()
    async def liberar(
        self,
        interacao: discord.Interaction,
        id_fivem: str,
        item: str,
    ):
        item_limpo = item.strip().lower()
        if item_limpo not in LIMITES_BAU_CAMADA_1:
            await responder_erro(
                interacao,
                titulo="Item inválido",
                linhas=[
                    f"`{item}` não está nos limites configurados.",
                    "Itens: " + ", ".join(sorted(LIMITES_BAU_CAMADA_1.keys())),
                ],
            )
            return
        mensagem = await liberar_limite_manual(
            id_fivem=id_fivem.strip(),
            item_canonico=item_limpo,
            executor_id=interacao.user.id,
        )
        await responder_sucesso(interacao, titulo="Limite liberado", linhas=[mensagem])

    @grupo_bau.command(
        name="ciclo",
        description="Mostra a chave do ciclo de contagem atual",
    )
    async def ciclo(self, interacao: discord.Interaction):
        await responder_info(
            interacao,
            titulo="Ciclo atual do baú",
            linhas=[
                f"Chave: `{chave_ciclo_atual()}`",
                "Resets locais: 00:00, 11:00 e 17:00.",
                "Casos abertos **não** são apagados no reset.",
            ],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BauCog(bot))
