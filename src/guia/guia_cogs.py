# src/guia/guia_cogs.py
"""
Comandos de barra do domínio guia.

Permite à staff republicar os painéis de boas-vindas e tutoriais
sem reiniciar o bot (usa a mesma view já registrada no on_ready).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from src.config import CANAIS
from src.database.connection import async_session
from src.database.models import PainelPostado
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
)
from src.utils.permissions import apenas_administrador


class Guia(commands.Cog):
    """Comandos administrativos dos painéis do Guia do Estagiário."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    grupo_guia = app_commands.Group(
        name="guia",
        description="Painéis do Guia do Estagiário (boas-vindas e tutoriais)",
    )

    async def _publicar_painel(
        self,
        interacao: discord.Interaction,
        *,
        nome_do_painel: str,
        chave_do_canal: str,
        atributo_da_view: str,
        rotulo_amigavel: str,
    ) -> None:
        """
        Publica (ou republica) um painel do guia no canal configurado.

        Reutiliza a view já registrada no bot — não cria instância nova.
        Atualiza ou cria o registro em PainelPostado.
        """
        await interacao.response.defer(ephemeral=True)

        canal_id = CANAIS.get(chave_do_canal) or 0
        canal_esta_configurado = canal_id > 0
        if not canal_esta_configurado:
            await responder_erro(
                interacao,
                titulo="Canal não configurado",
                linhas=[
                    f"A chave `{chave_do_canal}` não está definida em CANAIS.",
                ],
            )
            return

        canal = interacao.guild.get_channel(canal_id) if interacao.guild else None
        if canal is None:
            canal = self.bot.get_channel(canal_id)

        if canal is None:
            await responder_erro(
                interacao,
                titulo="Canal não encontrado",
                linhas=[
                    f"Não foi possível achar o canal `{chave_do_canal}` ({canal_id}).",
                ],
            )
            return

        view_do_painel = getattr(self.bot, atributo_da_view, None)
        view_esta_registrada = view_do_painel is not None
        if not view_esta_registrada:
            await responder_erro(
                interacao,
                titulo="View ainda não pronta",
                linhas=[
                    "O bot ainda não terminou de carregar as views.",
                    "Aguarde o on_ready e tente de novo.",
                ],
            )
            return

        mensagem = await canal.send(view=view_do_painel)

        async with async_session() as sessao:
            resultado_da_consulta = await sessao.execute(
                select(PainelPostado).where(PainelPostado.nome_painel == nome_do_painel)
            )
            registro = resultado_da_consulta.scalar_one_or_none()

            if registro is not None:
                registro.canal_id = canal.id
                registro.message_id = mensagem.id
            else:
                sessao.add(
                    PainelPostado(
                        nome_painel=nome_do_painel,
                        canal_id=canal.id,
                        message_id=mensagem.id,
                    )
                )

            await sessao.commit()

        await responder_sucesso(
            interacao,
            titulo=f"Painel {rotulo_amigavel} publicado",
            linhas=[
                f"Canal: <#{canal.id}>",
                f"Mensagem: `{mensagem.id}`",
            ],
        )

    @grupo_guia.command(
        name="painel-boas-vindas",
        description="Publica o painel de boas-vindas no canal configurado",
    )
    @apenas_administrador()
    async def painel_boas_vindas(self, interacao: discord.Interaction):
        await self._publicar_painel(
            interacao,
            nome_do_painel="boas_vindas",
            chave_do_canal="PAINEL_BOAS_VINDAS",
            atributo_da_view="painel_boas_vindas_view",
            rotulo_amigavel="Boas-Vindas",
        )

    @grupo_guia.command(
        name="painel-tutoriais",
        description="Publica o painel de tutoriais no canal configurado",
    )
    @apenas_administrador()
    async def painel_tutoriais(self, interacao: discord.Interaction):
        await self._publicar_painel(
            interacao,
            nome_do_painel="tutoriais",
            chave_do_canal="PAINEL_TUTORIAIS",
            atributo_da_view="painel_tutoriais_view",
            rotulo_amigavel="Tutoriais",
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Guia(bot))
