# src/cogs/busca.py
"""
Grupo /busca — localizar membros e cargos no servidor.

  /busca membro
  /busca cargos
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.mensagens import (
    COR_AVISO,
    COR_INFO,
    enviar_card,
)


class BuscaCog(commands.Cog):
    """Comandos do grupo /busca."""

    grupo_busca = app_commands.Group(
        name="busca",
        description="Buscar membros e cargos no servidor",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_busca.command(
        name="membro",
        description="Busca um membro pelo ID ou por trecho do nome",
    )
    @app_commands.describe(
        consulta="ID numérico ou parte do nome/apelido",
    )
    async def membro(self, interacao: discord.Interaction, consulta: str):
        """Localiza até dez membros por ID, nome, apelido ou nome de exibição.

        Dá prioridade à correspondência exata de ID e só então percorre os
        membros do servidor sem distinguir maiúsculas. O limite evita gerar uma
        resposta longa demais no Discord quando a consulta é muito genérica.
        """
        guilda = interacao.guild
        if guilda is None:
            await enviar_card(
                interacao,
                titulo="Busca",
                linhas=["Este comando só funciona dentro de um servidor."],
                cor=COR_AVISO,
            )
            return

        consulta_limpa = consulta.strip()
        encontrados: list[discord.Member] = []

        # Se for só número, tenta pelo ID primeiro
        if consulta_limpa.isdigit():
            membro_por_id = guilda.get_member(int(consulta_limpa))
            if membro_por_id is not None:
                encontrados.append(membro_por_id)

        # Busca por nome/apelido (case-insensitive)
        if not encontrados:
            texto_busca = consulta_limpa.lower()
            for membro in guilda.members:
                nome_global = str(membro).lower()
                apelido = (membro.nick or "").lower()
                nome_exibicao = membro.display_name.lower()

                bateu_nome = texto_busca in nome_global
                bateu_apelido = texto_busca in apelido
                bateu_exibicao = texto_busca in nome_exibicao

                if bateu_nome or bateu_apelido or bateu_exibicao:
                    encontrados.append(membro)

                if len(encontrados) >= 10:
                    break

        if not encontrados:
            await enviar_card(
                interacao,
                titulo="🔍 Nenhum resultado",
                linhas=[f"Nada encontrado para `{consulta_limpa}`."],
                cor=COR_AVISO,
                delay=12,
            )
            return

        linhas = []
        for membro in encontrados[:10]:
            linhas.append(f"{membro.mention} · `{membro.id}` · {membro.display_name}")

        if len(encontrados) > 10:
            linhas.append("… mostrando só os 10 primeiros.")

        await enviar_card(
            interacao,
            titulo=f"🔍 Resultados ({len(encontrados)})",
            linhas=linhas,
            cor=COR_INFO,
            delay=25,
        )

    @grupo_busca.command(
        name="cargos",
        description="Lista os cargos de um membro",
    )
    @app_commands.describe(membro="Membro para listar os cargos")
    async def cargos(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
    ):
        """Apresenta os cargos do membro na mesma ordem da hierarquia do servidor.

        Exclui ``@everyone`` e limita a resposta aos primeiros 25 cargos para
        que a consulta continue legível, avisando quando ainda há itens fora da
        lista exibida.
        """
        cargos_do_membro = [cargo for cargo in membro.roles if not cargo.is_default()]
        # Do mais alto para o mais baixo na hierarquia
        cargos_ordenados = sorted(
            cargos_do_membro,
            key=lambda cargo: cargo.position,
            reverse=True,
        )

        if not cargos_ordenados:
            await enviar_card(
                interacao,
                titulo=f"Cargos · {membro.display_name}",
                linhas=["Este membro só tem o cargo @everyone."],
                cor=COR_AVISO,
                delay=12,
            )
            return

        linhas = [
            f"`{indice + 1}.` {cargo.mention} (`{cargo.id}`)"
            for indice, cargo in enumerate(cargos_ordenados[:25])
        ]

        if len(cargos_ordenados) > 25:
            linhas.append(f"… e mais {len(cargos_ordenados) - 25} cargo(s).")

        await enviar_card(
            interacao,
            titulo=f"🎭 Cargos · {membro.display_name}",
            linhas=linhas,
            cor=COR_INFO,
            delay=25,
        )


async def setup(bot: commands.Bot):
    """Adiciona ao bot os comandos de consulta de membros e cargos."""
    await bot.add_cog(BuscaCog(bot))
