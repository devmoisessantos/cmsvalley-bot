# src/punicoes/moderacao_cogs.py
"""
Grupo /moderacao — ferramentas rápidas de moderação e wipe de temporada.

  /moderacao limpar
  /moderacao apelido
  /moderacao wipe
  /moderacao wipe-status
  /moderacao wipe-diretoria

Não substitui o sistema de punições do domínio punicoes/.
O wipe de temporada vive em src/wipe/; este cog só registra os comandos
no grupo moderacao para o Discord listar tudo junto.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
    COR_SUCESSO,
    enviar_card,
)
from src.utils.permissions import apenas_administrador
from src.wipe.wipe_cogs import (
    executar_comando_wipe,
    executar_comando_wipe_diretoria,
    executar_comando_wipe_status,
)


class ModeracaoCog(commands.Cog):
    """Comandos do grupo /moderacao."""

    grupo_moderacao = app_commands.Group(
        name="moderacao",
        description="Ferramentas rápidas de moderação",
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
        """Remove mensagens recentes do canal atual e informa o total realmente apagado.

        Só aceita canais de texto e trata recusas do Discord para não confirmar uma
        limpeza que não aconteceu. A operação exclui conteúdo de forma permanente,
        por isso está protegida pela permissão administrativa.
        """
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
        except discord.HTTPException as erro:
            await enviar_card(
                interacao,
                titulo="Erro ao limpar",
                linhas=[f"O Discord recusou a operação: `{erro}`"],
                cor=COR_ERRO,
            )
            return

        await enviar_card(
            interacao,
            titulo="🧹 Canal limpo",
            linhas=[
                f"Mensagens apagadas: **{quantidade_apagada}**",
                f"Canal: {canal.mention}",
            ],
            cor=COR_SUCESSO,
            delay=12,
        )

    @grupo_moderacao.command(
        name="apelido",
        description="Altera o apelido de um membro neste servidor",
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
        """Altera ou remove o apelido do membro após validar o limite do Discord.

        Um valor ausente remove o apelido, enquanto um texto é limitado a trinta e dois
        caracteres antes de chamar a API. A resposta distingue falha de hierarquia de
        sucesso para não sugerir que a alteração foi aplicada quando o Discord recusou.
        """
        await interacao.response.defer(ephemeral=True)

        apelido_final = apelido.strip() if apelido else None
        if apelido_final is not None and len(apelido_final) > 32:
            await enviar_card(
                interacao,
                titulo="Apelido inválido",
                linhas=["O apelido no Discord não pode passar de 32 caracteres."],
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
        except discord.HTTPException as erro:
            await enviar_card(
                interacao,
                titulo="Erro ao alterar apelido",
                linhas=[f"O Discord recusou a operação: `{erro}`"],
                cor=COR_ERRO,
            )
            return

        if apelido_final:
            texto_resultado = f"Novo apelido de {membro.mention}: **{apelido_final}**"
        else:
            texto_resultado = f"Apelido de {membro.mention} removido."

        await enviar_card(
            interacao,
            titulo="✏️ Apelido atualizado",
            linhas=[texto_resultado],
            cor=COR_SUCESSO,
            delay=12,
        )

    @grupo_moderacao.command(
        name="wipe",
        description="Assistente de wipe: expulsar membros e limpar canais escolhidos",
    )
    @apenas_administrador()
    async def wipe(self, interacao: discord.Interaction):
        """Abre o assistente de wipe (não destrói nada até confirmar com WIPE)."""
        await executar_comando_wipe(interacao)

    @grupo_moderacao.command(
        name="wipe-status",
        description="Mostra se há wipe em andamento e o resumo do último",
    )
    @apenas_administrador()
    async def wipe_status(self, interacao: discord.Interaction):
        """Consulta o estado do wipe neste processo do bot."""
        await executar_comando_wipe_status(interacao)

    @grupo_moderacao.command(
        name="wipe-diretoria",
        description="Lista quem seria preservado se o wipe rodasse agora",
    )
    @apenas_administrador()
    async def wipe_diretoria(self, interacao: discord.Interaction):
        """Lista preservados e quantidade de expulsáveis no momento."""
        await executar_comando_wipe_diretoria(interacao)


async def setup(bot: commands.Bot):
    """Registra os comandos rápidos de moderação durante a inicialização do bot."""
    await bot.add_cog(ModeracaoCog(bot))
