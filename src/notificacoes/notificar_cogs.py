"""Comandos de barra para notificar membros por DM.

/notificar membro
/notificar aviso-rapido
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.mensagens import (
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    enviar_card,
)
from src.utils.notificacao import (
    COR_AVISO,
    COR_INFO as COR_DM_INFO,
    COR_PUNICAO,
    COR_SUCESSO as COR_DM_SUCESSO,
    enviar_dm_card,
)
from src.utils.permissions import apenas_administrador

CORES_DISPONIVEIS = {
    "info": COR_DM_INFO,
    "sucesso": COR_DM_SUCESSO,
    "aviso": COR_AVISO,
    "erro": COR_PUNICAO,
}


class NotificarCog(commands.Cog):
    """Notificações manuais por DM para membros do servidor."""

    grupo_notificar = app_commands.Group(
        name="notificar",
        description="Enviar notificações por DM a membros (somente Administradores)",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_notificar.command(
        name="membro",
        description="Envia um card de notificação na DM de um membro",
    )
    @app_commands.describe(
        membro="Quem vai receber a DM",
        titulo="Título do card",
        mensagem="Corpo da mensagem (use | para quebrar linha)",
        cor="Cor do card",
    )
    @app_commands.choices(
        cor=[
            app_commands.Choice(name="Info (azul)", value="info"),
            app_commands.Choice(name="Sucesso (verde)", value="sucesso"),
            app_commands.Choice(name="Aviso (laranja)", value="aviso"),
            app_commands.Choice(name="Erro / alerta (vermelho)", value="erro"),
        ]
    )
    @apenas_administrador()
    async def notificar_membro(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
        titulo: str,
        mensagem: str,
        cor: app_commands.Choice[str] | None = None,
    ):
        await interacao.response.defer(ephemeral=True, thinking=True)

        chave_cor = cor.value if cor else "info"
        cor_escolhida = CORES_DISPONIVEIS.get(chave_cor, COR_DM_INFO)
        linhas = [parte.strip() for parte in mensagem.split("|") if parte.strip()]
        if not linhas:
            linhas = [mensagem]

        enviou = await enviar_dm_card(
            membro,
            titulo=titulo[:200],
            linhas=linhas,
            cor=cor_escolhida,
            guilda=interacao.guild,
        )

        if enviou:
            await enviar_card(
                interacao,
                titulo="✅ DM enviada",
                linhas=[
                    f"Destino: {membro.mention}",
                    f"Título: **{titulo[:80]}**",
                ],
                cor=COR_SUCESSO,
                delay=20,
            )
        else:
            await enviar_card(
                interacao,
                titulo="❌ Falha ao enviar DM",
                linhas=[
                    f"Destino: {membro.mention}",
                    "O membro pode ter DMs fechadas ou o bot sem permissão.",
                ],
                cor=COR_ERRO,
                delay=25,
            )

    @grupo_notificar.command(
        name="aviso-rapido",
        description="Aviso curto na DM (título fixo de aviso do sistema)",
    )
    @app_commands.describe(
        membro="Quem vai receber",
        texto="Texto do aviso",
    )
    @apenas_administrador()
    async def aviso_rapido(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
        texto: str,
    ):
        await interacao.response.defer(ephemeral=True, thinking=True)
        enviou = await enviar_dm_card(
            membro,
            titulo="Aviso do CMS Valley",
            linhas=[texto[:1500]],
            cor=COR_AVISO,
            guilda=interacao.guild,
        )
        await enviar_card(
            interacao,
            titulo="✅ Aviso enviado" if enviou else "❌ Falha no aviso",
            linhas=[f"Destino: {membro.mention}"],
            cor=COR_SUCESSO if enviou else COR_ERRO,
            delay=15,
        )

    @grupo_notificar.command(
        name="ajuda",
        description="Explica os comandos de notificação",
    )
    @apenas_administrador()
    async def ajuda(self, interacao: discord.Interaction):
        await enviar_card(
            interacao,
            titulo="📨 Ajuda · Notificar",
            linhas=[
                "`/notificar membro` — card personalizado na DM.",
                "`/notificar aviso-rapido` — aviso curto com título padrão.",
                "Toda DM é registrada em **LOG_NOTIFICACOES_DM**.",
                "Use `|` em `mensagem` para quebrar linhas no card.",
            ],
            cor=COR_INFO,
            delay=40,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(NotificarCog(bot))
