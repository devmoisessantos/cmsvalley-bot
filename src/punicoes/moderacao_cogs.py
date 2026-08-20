# src/punicoes/moderacao_cogs.py
"""
Grupo /moderacao — ferramentas rápidas de moderação e wipe de temporada.

  /moderacao limpar
  /moderacao apelido
  /moderacao wipe
  /moderacao wipe-status
  /moderacao wipe-diretoria

Não substitui o sistema de punições do domínio punicoes/.

O wipe vive em src/wipe/. Os handlers são importados DENTRO de cada
comando (import preguiçoso) para que uma falha no domínio wipe NÃO
derrube o cog inteiro nem impeça limpar/apelido de subir no Discord.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
    COR_SUCESSO,
    enviar_card,
    responder_erro,
)
from src.utils.permissions import apenas_administrador

registrador = logging.getLogger(__name__)


class ModeracaoCog(commands.Cog):
    """Comandos do grupo /moderacao."""

    grupo_moderacao = app_commands.Group(
        name="moderacao",
        description="Ferramentas rápidas de moderação e wipe de temporada",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_moderacao.command(
        name="limpar",
        description="Apaga as últimas mensagens deste canal (até 100)",
    )
    @app_commands.describe(quantidade="Quantas mensagens apagar (1 a 100)")
    @apenas_administrador()
    async def limpar(
        self,
        interacao: discord.Interaction,
        quantidade: app_commands.Range[int, 1, 100],
    ):
        """Remove mensagens recentes do canal atual e informa o total apagado."""
        canal = interacao.channel
        if canal is None or not isinstance(canal, discord.TextChannel):
            await enviar_card(
                interacao,
                titulo="Limpar mensagens",
                linhas=["Só é possível limpar em canais de texto."],
                cor=COR_AVISO,
            )
            return

        await interacao.response.defer(ephemeral=True)

        try:
            mensagens_apagadas = await canal.purge(limit=quantidade)
            quantidade_apagada = len(mensagens_apagadas)
        except discord.Forbidden:
            await enviar_card(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "O bot não tem permissão para apagar mensagens neste canal.",
                ],
                cor=COR_ERRO,
            )
            return
        except discord.HTTPException as erro_http:
            await enviar_card(
                interacao,
                titulo="Erro ao limpar",
                linhas=[f"O Discord recusou a operação: `{erro_http}`"],
                cor=COR_ERRO,
            )
            return

        await enviar_card(
            interacao,
            titulo="Limpeza concluída",
            linhas=[f"Apaguei **{quantidade_apagada}** mensagem(ns)."],
            cor=COR_SUCESSO,
            delay=12,
        )

    @grupo_moderacao.command(
        name="apelido",
        description="Altera ou remove o apelido de um membro",
    )
    @app_commands.describe(
        membro="Membro que terá o apelido alterado",
        apelido="Novo apelido (deixe vazio para remover)",
    )
    @apenas_administrador()
    async def apelido(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
        apelido: str | None = None,
    ):
        """Altera o apelido do membro ou remove se nenhum texto for passado."""
        apelido_final = apelido.strip() if apelido else None
        if apelido_final is not None and len(apelido_final) > 32:
            await enviar_card(
                interacao,
                titulo="Apelido inválido",
                linhas=["O apelido do Discord aceita no máximo 32 caracteres."],
                cor=COR_AVISO,
            )
            return

        try:
            await membro.edit(
                nick=apelido_final,
                reason=f"Alterado por {interacao.user} via /moderacao apelido",
            )
        except discord.Forbidden:
            await enviar_card(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "O bot não consegue alterar o apelido deste membro.",
                    "Verifique a hierarquia de cargos do bot.",
                ],
                cor=COR_ERRO,
            )
            return
        except discord.HTTPException as erro_http:
            await enviar_card(
                interacao,
                titulo="Erro ao alterar apelido",
                linhas=[f"O Discord recusou a operação: `{erro_http}`"],
                cor=COR_ERRO,
            )
            return

        if apelido_final:
            texto_resultado = f"Novo apelido de {membro.mention}: **{apelido_final}**"
        else:
            texto_resultado = f"Apelido de {membro.mention} removido."

        await enviar_card(
            interacao,
            titulo="Apelido atualizado",
            linhas=[texto_resultado],
            cor=COR_SUCESSO,
            delay=12,
        )

    @grupo_moderacao.command(
        name="wipe",
        description="Assistente de wipe: expulsar membros e limpar canais",
    )
    @apenas_administrador()
    async def wipe(self, interacao: discord.Interaction):
        """Abre o assistente de wipe (não destrói nada até confirmar com WIPE)."""
        try:
            from src.wipe.wipe_cogs import executar_comando_wipe
        except Exception as erro_importacao:
            registrador.exception(
                "Falha ao importar o domínio wipe: %s", erro_importacao
            )
            await responder_erro(
                interacao,
                titulo="Wipe indisponível",
                linhas=[
                    "Não consegui carregar o módulo de wipe.",
                    f"Detalhe técnico: `{erro_importacao}`",
                    "Avise a equipe de desenvolvimento com o log do bot.",
                ],
            )
            return
        await executar_comando_wipe(interacao)

    @grupo_moderacao.command(
        name="wipe-status",
        description="Mostra se há wipe em andamento e o resumo do último",
    )
    @apenas_administrador()
    async def wipe_status(self, interacao: discord.Interaction):
        """Consulta o estado do wipe neste processo do bot."""
        try:
            from src.wipe.wipe_cogs import executar_comando_wipe_status
        except Exception as erro_importacao:
            registrador.exception("Falha ao importar wipe-status: %s", erro_importacao)
            await responder_erro(
                interacao,
                titulo="Wipe indisponível",
                linhas=[f"Não consegui carregar o módulo: `{erro_importacao}`"],
            )
            return
        await executar_comando_wipe_status(interacao)

    @grupo_moderacao.command(
        name="wipe-diretoria",
        description="Lista quem seria preservado se o wipe rodasse agora",
    )
    @apenas_administrador()
    async def wipe_diretoria(self, interacao: discord.Interaction):
        """Lista preservados e quantidade de expulsáveis no momento."""
        try:
            from src.wipe.wipe_cogs import executar_comando_wipe_diretoria
        except Exception as erro_importacao:
            registrador.exception(
                "Falha ao importar wipe-diretoria: %s", erro_importacao
            )
            await responder_erro(
                interacao,
                titulo="Wipe indisponível",
                linhas=[f"Não consegui carregar o módulo: `{erro_importacao}`"],
            )
            return
        await executar_comando_wipe_diretoria(interacao)


async def setup(bot: commands.Bot):
    """Registra os comandos rápidos de moderação durante a inicialização do bot."""
    await bot.add_cog(ModeracaoCog(bot))
    registrador.info(
        "ModeracaoCog registrado com subcomandos: %s",
        [comando.name for comando in ModeracaoCog.grupo_moderacao.commands],
    )
