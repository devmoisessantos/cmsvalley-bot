"""
Comandos do Responsável HP para conferir produção e destaques.

Comandos
--------
- /avaliacao-membro — relatório de um membro (laudos, recrutamentos, etc.)
- /avaliacao-area — totais e ranking de uma área
- /avaliacao-destaques — quem se destacou em cada área
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.config import (
    CARGO_DOUTOR,
    CARGO_INSTRUTOR,
    CARGO_PSICOLOGO,
    CARGO_RECRUTADOR,
    CARGOS,
)
from src.promocoes.avaliacao_hp.avaliacao_hp_service import (
    formatar_destaques_em_linhas,
    formatar_relatorio_area_em_linhas,
    formatar_relatorio_membro_em_linhas,
    montar_relatorio_da_area,
    montar_relatorio_do_membro,
    sugerir_destaque_por_area,
)
from src.utils.mensagens import responder_erro, responder_info
from src.utils.permissions import membro_tem_cargo

logger = logging.getLogger(__name__)

# Quem pode abrir os relatórios de avaliação HP
CARGOS_QUE_PODEM_AVALIAR = (
    "Responsavel HP",
    "👑 | RESPONSÁVEL GERAL",
    "👑 |  DIRETOR GERAL",
    "👑 |  VICE DIRETOR GERAL",
)

OPCOES_AREA = [
    app_commands.Choice(name="Doutor", value="doutor"),
    app_commands.Choice(name="Psicólogo", value="psicologo"),
    app_commands.Choice(name="Recrutador", value="recrutador"),
    app_commands.Choice(name="Instrutor", value="instrutor"),
]

MAPA_AREA_PARA_CARGO = {
    "doutor": CARGO_DOUTOR,
    "psicologo": CARGO_PSICOLOGO,
    "recrutador": CARGO_RECRUTADOR,
    "instrutor": CARGO_INSTRUTOR,
}


def _pode_usar_avaliacao(membro: discord.Member) -> bool:
    """True se o membro é Responsável HP ou diretoria geral."""
    if membro.guild_permissions.administrator:
        return True
    for nome_cargo in CARGOS_QUE_PODEM_AVALIAR:
        if membro_tem_cargo(membro, nome_cargo):
            return True
        cargo_id = CARGOS.get(nome_cargo)
        if cargo_id is not None and any(
            cargo.id == int(cargo_id) for cargo in membro.roles
        ):
            return True
    return False


class AvaliacaoHpCog(commands.Cog):
    """Comandos de avaliação de produção para o Responsável HP."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="avaliacao-membro",
        description="Relatório de produção de um membro (Responsável HP)",
    )
    @app_commands.describe(membro="Membro que você quer conferir")
    async def avaliacao_membro(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
    ):
        """
        Mostra laudos, recrutamentos, chamadas, cursos e plantão do membro.

        Compara com a meta do cargo de referência quando existir em
        METAS_POR_CARGO no config.
        """
        quem_pediu = interacao.user
        if not isinstance(quem_pediu, discord.Member):
            await responder_erro(
                interacao,
                titulo="Somente no servidor",
                linhas=["Use o comando dentro do Discord do hospital."],
            )
            return
        if not _pode_usar_avaliacao(quem_pediu):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Só o Responsável HP e a diretoria geral podem "
                    "abrir este relatório."
                ],
            )
            return

        await interacao.response.defer(ephemeral=True)
        try:
            relatorio = await montar_relatorio_do_membro(membro)
            linhas = formatar_relatorio_membro_em_linhas(relatorio)
            await responder_info(
                interacao,
                titulo=f"Avaliação HP — {membro.display_name}",
                linhas=linhas,
                delay=120,
                com_marcador=False,
            )
        except Exception as erro_capturado:
            logger.exception(
                "Falha em /avaliacao-membro para %s: %s",
                membro.id,
                erro_capturado,
            )
            await responder_erro(
                interacao,
                titulo="Não consegui montar o relatório",
                linhas=[
                    "Houve um erro ao buscar a produção deste membro.",
                    "Tente de novo em instantes.",
                ],
            )

    @app_commands.command(
        name="avaliacao-area",
        description="Totais e ranking de uma área (Responsável HP)",
    )
    @app_commands.describe(area="Área médica a consultar")
    @app_commands.choices(area=OPCOES_AREA)
    async def avaliacao_area(
        self,
        interacao: discord.Interaction,
        area: app_commands.Choice[str],
    ):
        """Lista totais da área e o ranking pela meta principal."""
        quem_pediu = interacao.user
        if not isinstance(quem_pediu, discord.Member):
            await responder_erro(
                interacao,
                titulo="Somente no servidor",
                linhas=["Use o comando dentro do Discord do hospital."],
            )
            return
        if not _pode_usar_avaliacao(quem_pediu):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Só o Responsável HP e a diretoria geral podem "
                    "abrir este relatório."
                ],
            )
            return

        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Servidor não encontrado",
                linhas=["Este comando só funciona dentro da guilda."],
            )
            return

        chave_area = area.value
        nome_cargo = MAPA_AREA_PARA_CARGO.get(chave_area)
        if not nome_cargo:
            await responder_erro(
                interacao,
                titulo="Área inválida",
                linhas=[f"Área `{chave_area}` não está mapeada."],
            )
            return

        await interacao.response.defer(ephemeral=True)
        try:
            relatorio = await montar_relatorio_da_area(
                guilda,
                chave_area,
                nome_cargo,
            )
            linhas = formatar_relatorio_area_em_linhas(relatorio)
            await responder_info(
                interacao,
                titulo=f"Avaliação HP — área {nome_cargo}",
                linhas=linhas,
                delay=120,
                com_marcador=False,
            )
        except Exception as erro_capturado:
            logger.exception(
                "Falha em /avaliacao-area %s: %s",
                chave_area,
                erro_capturado,
            )
            await responder_erro(
                interacao,
                titulo="Não consegui montar o relatório da área",
                linhas=[
                    "Houve um erro ao buscar a produção desta área.",
                    "Tente de novo em instantes.",
                ],
            )

    @app_commands.command(
        name="avaliacao-destaques",
        description="Quem se destacou em cada área (Responsável HP)",
    )
    async def avaliacao_destaques(
        self,
        interacao: discord.Interaction,
    ):
        """
        Sugere o membro com maior score em cada área.

        Serve de apoio para indicar Responsável Doutor, Psicólogo,
        Recrutamento ou Instrutor.
        """
        quem_pediu = interacao.user
        if not isinstance(quem_pediu, discord.Member):
            await responder_erro(
                interacao,
                titulo="Somente no servidor",
                linhas=["Use o comando dentro do Discord do hospital."],
            )
            return
        if not _pode_usar_avaliacao(quem_pediu):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Só o Responsável HP e a diretoria geral podem "
                    "abrir este relatório."
                ],
            )
            return

        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Servidor não encontrado",
                linhas=["Este comando só funciona dentro da guilda."],
            )
            return

        await interacao.response.defer(ephemeral=True)
        try:
            sugestoes = await sugerir_destaque_por_area(guilda)
            linhas = formatar_destaques_em_linhas(sugestoes)
            await responder_info(
                interacao,
                titulo="Avaliação HP — destaques por área",
                linhas=linhas,
                delay=120,
                com_marcador=False,
            )
        except Exception as erro_capturado:
            logger.exception(
                "Falha em /avaliacao-destaques: %s",
                erro_capturado,
            )
            await responder_erro(
                interacao,
                titulo="Não consegui montar os destaques",
                linhas=[
                    "Houve um erro ao calcular os destaques por área.",
                    "Tente de novo em instantes.",
                ],
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AvaliacaoHpCog(bot))
