"""
Comandos do domínio wipe — registrados no grupo /moderacao existente.

Os handlers ficam aqui; o ModeracaoCog só reexporta os comandos no grupo
para não haver dois Group(name='moderacao') competindo no tree.
"""

from __future__ import annotations

import logging

import discord

from src.utils.mensagens import responder_info
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
