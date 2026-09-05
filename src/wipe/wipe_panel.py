"""
Confirmação simples antes de /wipe limpar-cargos.

Components V2: um card com resumo e botões Confirmar / Cancelar.
"""

from __future__ import annotations

import logging

import discord

from src.config import (
    CARGO_BASE_APOS_WIPE,
    CARGOS_PRESERVADOS_NO_WIPE,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)
from src.wipe.wipe_membros_service import listar_preservados_e_comuns
from src.wipe.wipe_service import executar_limpar_cargos
from src.wipe.wipe_state import wipe_esta_em_andamento

registrador = logging.getLogger(__name__)

TIMEOUT_CONFIRMACAO_SEGUNDOS = 300


class PainelConfirmacaoLimparCargos(discord.ui.LayoutView):
    """Card de confirmação antes de limpar cargos e prefixos."""

    def __init__(
        self,
        interacao: discord.Interaction,
        quantidade_preservados: int,
        quantidade_comuns: int,
    ):
        super().__init__(timeout=TIMEOUT_CONFIRMACAO_SEGUNDOS)
        self.interacao_original = interacao
        self.usuario_id = interacao.user.id
        self.ja_confirmou = False

        texto_lista = "\n".join(
            f"`•` {nome}" for nome in CARGOS_PRESERVADOS_NO_WIPE
        )
        corpo = (
            f"Preservados (mantêm cargo + `{CARGO_BASE_APOS_WIPE}`): "
            f"**{quantidade_preservados}**\n"
            f"Comuns (perdem todos os cargos): **{quantidade_comuns}**\n"
            f"Prefixo do nick: removido de **todo mundo**.\n\n"
            f"**Cargos que ficam com a diretoria/área:**\n{texto_lista}"
        )

        botao_confirmar = discord.ui.Button(
            label="Confirmar limpeza",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:confirmar_limpar",
        )
        botao_cancelar = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:cancelar_limpar",
        )
        botao_confirmar.callback = self._ao_confirmar
        botao_cancelar.callback = self._ao_cancelar

        linha = discord.ui.ActionRow(botao_confirmar, botao_cancelar)
        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Limpar cargos e prefixos"),
            discord.ui.TextDisplay(corpo),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            linha,
            accent_color=discord.Color.dark_red(),
        )
        self.add_item(self.container)

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        """Só quem abriu o painel pode clicar."""
        if interacao.user.id != self.usuario_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu este painel pode confirmar."],
            )
            return False
        return True

    async def _ao_cancelar(self, interacao: discord.Interaction) -> None:
        """Fecha sem alterar nada."""
        self.ja_confirmou = True
        self.stop()
        await responder_aviso(
            interacao,
            titulo="Limpeza cancelada",
            linhas=["Nenhum cargo ou nick foi alterado."],
        )

    async def _ao_confirmar(self, interacao: discord.Interaction) -> None:
        """Roda a limpeza de cargos e prefixos."""
        if wipe_esta_em_andamento():
            await responder_erro(
                interacao,
                titulo="Wipe em andamento",
                linhas=["Já existe uma operação de wipe em andamento. Aguarde."],
            )
            return

        if interacao.guild is None or not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Servidor necessário",
                linhas=["Use este comando dentro do servidor."],
            )
            return

        self.ja_confirmou = True
        self.stop()
        await interacao.response.defer(ephemeral=True)

        try:
            estado = await executar_limpar_cargos(interacao.guild, interacao.user)
            await responder_sucesso(
                interacao,
                titulo="Limpeza concluída",
                linhas=[
                    f"Temporada: `{estado.temporada}`",
                    f"Preservados: **{estado.membros_preservados}**",
                    f"Limpos: **{estado.membros_limpos}**",
                    f"Falhas: **{estado.membros_falha}**",
                    "Relatório completo no canal de logs do wipe.",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] confirmação limpar-cargos: %s", erro)
            await responder_erro(
                interacao,
                titulo="Limpeza falhou",
                linhas=[str(erro)],
            )


async def abrir_painel_limpar_cargos(interacao: discord.Interaction) -> None:
    """Monta o resumo e mostra o card de confirmação."""
    if interacao.guild is None:
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

    preservados, comuns = listar_preservados_e_comuns(interacao.guild)
    painel = PainelConfirmacaoLimparCargos(
        interacao,
        quantidade_preservados=len(preservados),
        quantidade_comuns=len(comuns),
    )
    await interacao.followup.send(view=painel, ephemeral=True)
