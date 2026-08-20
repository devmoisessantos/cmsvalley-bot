"""
Comandos de barra do domínio wipe.

Grupo próprio /wipe (registro independente do moderacao) e handlers
reutilizados por /moderacao wipe quando o ModeracaoCog chama daqui.

  /wipe iniciar
  /wipe status
  /wipe diretoria
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.mensagens import responder_info
from src.utils.permissions import apenas_administrador
from src.wipe.wipe_backup_service import montar_nome_da_temporada
from src.wipe.wipe_membros_service import listar_preservados_e_expulsaveis
from src.wipe.wipe_panel import abrir_painel_de_confirmacao
from src.wipe.wipe_state import obter_estado_do_wipe

registrador = logging.getLogger(__name__)


async def executar_comando_wipe(interacao: discord.Interaction) -> None:
    """Abre o painel de confirmação do wipe de temporada."""
    await interacao.response.defer(ephemeral=True)
    await abrir_painel_de_confirmacao(interacao)


async def executar_comando_wipe_status(interacao: discord.Interaction) -> None:
    """Informa fase atual do wipe ou o último estado em memória."""
    estado = obter_estado_do_wipe()
    if estado is None:
        await responder_info(
            interacao,
            titulo="Status do wipe",
            linhas=[
                "Nenhum wipe registrado neste processo do bot.",
                f"Próxima temporada sugerida: `{montar_nome_da_temporada()}`",
            ],
        )
        return

    andamento = "sim" if estado.em_andamento else "não"
    await responder_info(
        interacao,
        titulo="Status do wipe",
        linhas=[
            f"Temporada: `{estado.temporada}`",
            f"Em andamento: **{andamento}**",
            f"Fase: `{estado.fase}`",
            f"Iniciador: {estado.iniciador_nome}",
            f"Expulsos: {estado.membros_expulsos} (falhas: {estado.membros_falha})",
            f"Backup: `{estado.caminho_backup or '—'}`",
        ],
    )


async def executar_comando_wipe_diretoria(interacao: discord.Interaction) -> None:
    """Lista quem seria preservado se o wipe rodasse agora."""
    if interacao.guild is None:
        await responder_info(
            interacao,
            titulo="Diretoria no wipe",
            linhas=["Use dentro do servidor."],
        )
        return

    preservados, expulsaveis = listar_preservados_e_expulsaveis(interacao.guild)
    nomes = [
        f"• {membro} (`{membro.id}`)" for membro in preservados if not membro.bot
    ]
    await responder_info(
        interacao,
        titulo="Preservados no wipe",
        linhas=[
            f"Preservados: **{len(preservados)}** (incluindo bots/dono se aplicável)",
            f"Seriam expulsos: **{len(expulsaveis)}**",
            "",
            *nomes[:30],
        ],
    )


class WipeCog(commands.Cog):
    """
    Grupo /wipe — registro próprio na árvore de comandos.

    Existe separado do /moderacao para o Discord sempre receber estes
    subcomandos mesmo se o grupo moderacao no deploy estiver desatualizado.
    """

    grupo_wipe = app_commands.Group(
        name="wipe",
        description="Wipe de temporada: expulsar membros e limpar canais",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_wipe.command(
        name="iniciar",
        description="Abre o assistente de wipe de temporada",
    )
    @apenas_administrador()
    async def iniciar(self, interacao: discord.Interaction) -> None:
        """Abre o assistente configurável do wipe."""
        await executar_comando_wipe(interacao)

    @grupo_wipe.command(
        name="status",
        description="Mostra se há wipe em andamento e o resumo do último",
    )
    @apenas_administrador()
    async def status(self, interacao: discord.Interaction) -> None:
        """Consulta o estado do wipe neste processo do bot."""
        await executar_comando_wipe_status(interacao)

    @grupo_wipe.command(
        name="diretoria",
        description="Lista quem seria preservado se o wipe rodasse agora",
    )
    @apenas_administrador()
    async def diretoria(self, interacao: discord.Interaction) -> None:
        """Lista preservados e quantidade de expulsáveis no momento."""
        await executar_comando_wipe_diretoria(interacao)


async def setup(bot: commands.Bot) -> None:
    """Registra o grupo /wipe na árvore de comandos do bot."""
    await bot.add_cog(WipeCog(bot))
    nomes = [comando.name for comando in WipeCog.grupo_wipe.commands]
    registrador.info("WipeCog registrado com subcomandos: %s", nomes)
