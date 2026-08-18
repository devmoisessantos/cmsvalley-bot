# src/cogs/utilidade.py
"""
Grupo /utilidade — comandos leves de consulta do dia a dia.

  /utilidade ping
  /utilidade avatar
  /utilidade usuario
  /utilidade servidor
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.mensagens import (
    COR_INFO,
    COR_SUCESSO,
    enviar_card,
)


class UtilidadeCog(commands.Cog):
    """Comandos do grupo /utilidade."""

    grupo_utilidade = app_commands.Group(
        name="utilidade",
        description="Comandos úteis do dia a dia",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_utilidade.command(
        name="ping",
        description="Mostra a latência do bot com o Discord",
    )
    async def ping(self, interacao: discord.Interaction):
        """Exibe a latência aproximada para diagnosticar lentidão do bot."""
        latencia_em_ms = round(self.bot.latency * 1000)

        await enviar_card(
            interacao,
            titulo="🏓 Pong",
            linhas=[f"Latência: **{latencia_em_ms} ms**"],
            cor=COR_SUCESSO,
            delay=10,
        )

    @grupo_utilidade.command(
        name="avatar",
        description="Mostra o avatar de um membro",
    )
    @app_commands.describe(membro="Membro (deixe vazio para ver o seu)")
    async def avatar(
        self,
        interacao: discord.Interaction,
        membro: discord.Member | None = None,
    ):
        """Mostra um link para o avatar do membro indicado ou do próprio autor.

        Usa o usuário da interação como padrão para que o comando funcione sem
        parâmetro, mas aceita outro membro para facilitar consultas rápidas.
        """
        membro_alvo = membro or interacao.user
        url_do_avatar = membro_alvo.display_avatar.url

        await enviar_card(
            interacao,
            titulo=f"🖼️ Avatar · {membro_alvo.display_name}",
            linhas=[
                f"Membro: {membro_alvo.mention}",
                f"[Abrir imagem]({url_do_avatar})",
            ],
            cor=COR_INFO,
            delay=20,
        )

    @grupo_utilidade.command(
        name="usuario",
        description="Mostra informações básicas de um membro",
    )
    @app_commands.describe(membro="Membro (deixe vazio para ver o seu)")
    async def usuario(
        self,
        interacao: discord.Interaction,
        membro: discord.Member | None = None,
    ):
        """Reúne informações públicas do membro escolhido em um card temporário.

        Assume o autor como alvo quando nenhum membro é passado e acrescenta
        dados específicos da guilda somente quando a interação ocorre nela,
        evitando acessar atributos ausentes em mensagens diretas.
        """
        membro_alvo = membro or interacao.user
        guilda = interacao.guild

        criado_em = membro_alvo.created_at
        entrou_em = membro_alvo.joined_at

        linhas = [
            f"Mencão: {membro_alvo.mention}",
            f"ID: `{membro_alvo.id}`",
            f"Tag: `{membro_alvo}`",
            f"Conta criada: <t:{int(criado_em.timestamp())}:R>",
        ]

        if entrou_em is not None:
            linhas.append(f"Entrou no servidor: <t:{int(entrou_em.timestamp())}:R>")

        if guilda is not None:
            quantidade_de_cargos = len(
                [cargo for cargo in membro_alvo.roles if not cargo.is_default()]
            )
            linhas.append(f"Cargos (sem @everyone): **{quantidade_de_cargos}**")
            linhas.append(f"Cargo mais alto: **{membro_alvo.top_role.name}**")

        await enviar_card(
            interacao,
            titulo=f"👤 {membro_alvo.display_name}",
            linhas=linhas,
            cor=COR_INFO,
            delay=25,
        )

    @grupo_utilidade.command(
        name="servidor",
        description="Mostra informações básicas deste servidor",
    )
    async def servidor(self, interacao: discord.Interaction):
        """Exibe dados básicos da guilda e explica quando o comando foi usado em DM.

        A checagem da guilda evita tentar ler propriedades de servidor em um
        contexto direto, onde não há membros, cargos ou canais para resumir.
        """
        guilda = interacao.guild
        if guilda is None:
            await enviar_card(
                interacao,
                titulo="Servidor",
                linhas=["Este comando só funciona dentro de um servidor."],
                cor=COR_INFO,
            )
            return

        criado_em = guilda.created_at
        dono = guilda.owner

        linhas = [
            f"Nome: **{guilda.name}**",
            f"ID: `{guilda.id}`",
            f"Membros: **{guilda.member_count}**",
            f"Cargos: **{len(guilda.roles)}**",
            f"Canais de texto: **{len(guilda.text_channels)}**",
            f"Canais de voz: **{len(guilda.voice_channels)}**",
            f"Criado: <t:{int(criado_em.timestamp())}:R>",
        ]

        if dono is not None:
            linhas.append(f"Dono: {dono.mention}")

        await enviar_card(
            interacao,
            titulo="🏠 Servidor",
            linhas=linhas,
            cor=COR_INFO,
            delay=25,
        )


async def setup(bot: commands.Bot):
    """Registra o grupo de consultas leves de utilidade."""
    await bot.add_cog(UtilidadeCog(bot))
