"""
Comandos de barra do domínio wipe.

  /wipe backup        — snapshot Discord + backup do banco + esvaziar tabelas
  /wipe limpar-cargos — remove cargos e prefixos (com exceção da diretoria)
  /wipe status        — fase atual ou último resultado
  /wipe diretoria     — quem seria preservado agora
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.mensagens import (
    responder_erro,
    responder_info,
    responder_sucesso,
)
from src.utils.permissions import apenas_administrador
from src.wipe.wipe_backup_service import montar_nome_da_temporada
from src.wipe.wipe_membros_service import (
    listar_preservados_e_comuns,
    nomes_cargos_preservados_do_membro,
)
from src.wipe.wipe_panel import abrir_painel_limpar_cargos
from src.wipe.wipe_service import executar_backup_e_esvaziar_banco
from src.wipe.wipe_state import (
    obter_estado_do_wipe,
    wipe_esta_em_andamento,
)

registrador = logging.getLogger(__name__)


async def executar_comando_wipe(interacao: discord.Interaction) -> None:
    """
    Atalho usado por /moderacao wipe.

    Abre a mesma confirmação de /wipe limpar-cargos.
    """
    await interacao.response.defer(ephemeral=True)
    await abrir_painel_limpar_cargos(interacao)


async def executar_comando_wipe_status(interacao: discord.Interaction) -> None:
    """Atalho de status para /moderacao wipe-status."""
    estado = obter_estado_do_wipe()
    if estado is None:
        await responder_info(
            interacao,
            titulo="Status do wipe",
            linhas=[
                "Nenhuma operação registrada neste processo do bot.",
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
            f"Preservados: {estado.membros_preservados}",
            f"Limpos: {estado.membros_limpos} (falhas: {estado.membros_falha})",
            f"Tabelas esvaziadas: {estado.tabelas_esvaziadas}",
            f"Backup Discord: `{estado.caminho_backup_discord or '—'}`",
            f"Backup banco: `{estado.caminho_backup_banco or '—'}`",
        ],
    )


async def executar_comando_wipe_diretoria(interacao: discord.Interaction) -> None:
    """Atalho de listagem para /moderacao wipe-diretoria."""
    if interacao.guild is None:
        await responder_info(
            interacao,
            titulo="Diretoria no wipe",
            linhas=["Use dentro do servidor."],
        )
        return

    preservados, comuns = listar_preservados_e_comuns(interacao.guild)
    nomes = []
    for membro in preservados:
        cargos = nomes_cargos_preservados_do_membro(membro)
        nomes.append(f"• {membro} (`{membro.id}`) — {', '.join(cargos)}")

    await responder_info(
        interacao,
        titulo="Preservados no limpar-cargos",
        linhas=[
            f"Preservados: **{len(preservados)}**",
            f"Seriam limpos: **{len(comuns)}**",
            "",
            *nomes[:40],
        ],
    )


class WipeCog(commands.Cog):
    """Grupo /wipe — backup e limpeza de cargos da temporada."""

    grupo_wipe = app_commands.Group(
        name="wipe",
        description="Wipe de temporada: backup, banco e limpeza de cargos",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_wipe.command(
        name="backup",
        description="Snapshot Discord + backup do banco + esvaziar tabelas",
    )
    @apenas_administrador()
    async def backup(self, interacao: discord.Interaction) -> None:
        """Roda backup completo e, se o banco salvou, esvazia as tabelas."""
        if interacao.guild is None or not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Servidor necessário",
                linhas=["Use dentro do servidor."],
            )
            return

        if wipe_esta_em_andamento():
            await responder_erro(
                interacao,
                titulo="Wipe em andamento",
                linhas=["Já existe uma operação de wipe em andamento. Aguarde."],
            )
            return

        await interacao.response.defer(ephemeral=True)

        try:
            estado = await executar_backup_e_esvaziar_banco(
                interacao.guild, interacao.user
            )
            await responder_sucesso(
                interacao,
                titulo="Backup do wipe concluído",
                linhas=[
                    f"Temporada: `{estado.temporada}`",
                    f"Discord: `{estado.caminho_backup_discord or '—'}`",
                    f"Banco: `{estado.caminho_backup_banco or '—'}`",
                    f"Tabelas esvaziadas: **{estado.tabelas_esvaziadas}**",
                    "Relatório completo no canal de logs do wipe.",
                    "Próximo passo: `/wipe limpar-cargos`",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] comando backup: %s", erro)
            await responder_erro(
                interacao,
                titulo="Backup falhou",
                linhas=[str(erro)],
            )

    @grupo_wipe.command(
        name="limpar-cargos",
        description="Remove cargos e prefixos (mantém diretoria + HP S・Valley)",
    )
    @apenas_administrador()
    async def limpar_cargos(self, interacao: discord.Interaction) -> None:
        """Abre o card de confirmação antes de limpar cargos e nicks."""
        await interacao.response.defer(ephemeral=True)
        await abrir_painel_limpar_cargos(interacao)

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
        description="Lista quem manteria cargos se limpar-cargos rodasse agora",
    )
    @apenas_administrador()
    async def diretoria(self, interacao: discord.Interaction) -> None:
        """Lista preservados e quantidade de comuns no momento."""
        await executar_comando_wipe_diretoria(interacao)


async def setup(bot: commands.Bot) -> None:
    """Registra o grupo /wipe na árvore de comandos do bot."""
    await bot.add_cog(WipeCog(bot))
    nomes = [comando.name for comando in WipeCog.grupo_wipe.commands]
    registrador.info("WipeCog registrado com subcomandos: %s", nomes)
