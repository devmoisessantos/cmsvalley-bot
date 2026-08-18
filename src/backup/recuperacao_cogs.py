"""Comandos de recuperação de dados a partir dos canais de LOG."""

from __future__ import annotations

import logging
from collections.abc import (
    Awaitable,
    Callable,
)

import discord
from discord import app_commands
from discord.ext import commands

from src.backup.recuperacao_logs_service import (
    id_canal_log,
    id_canal_log_plantao,
    importar_log_aprovacoes_do_canal,
    importar_log_cargos_do_canal,
    importar_log_chamadas_do_canal,
    importar_log_laudos_do_canal,
    importar_log_plantao_do_canal,
    importar_log_promocoes_do_canal,
    importar_log_punicoes_do_canal,
    importar_log_recrutamentos_do_canal,
    importar_log_reprovacoes_do_canal,
    importar_log_whitelist_do_canal,
)
from src.utils.mensagens import (
    responder_erro,
    responder_info,
    responder_sucesso,
)
from src.utils.permissions import is_authorized

logger = logging.getLogger(__name__)


class RecuperacaoLogsCog(commands.Cog):
    grupo = app_commands.Group(
        name="recuperar",
        description="Recuperar dados a partir dos canais de LOG (admin)",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _rodar_importacao(
        self,
        interacao: discord.Interaction,
        *,
        titulo: str,
        chave_canal: str,
        canal_id: int | None,
        importador: Callable[..., Awaitable[dict]],
        limite: int | None,
        so_bot: bool,
    ) -> None:
        await interacao.response.defer(ephemeral=True)

        if not canal_id:
            await responder_erro(
                interacao,
                titulo="Canal não configurado",
                linhas=[f"`CANAIS['{chave_canal}']` não está definido no config."],
            )
            return

        canal = interacao.guild.get_channel(canal_id) if interacao.guild else None
        if canal is None:
            await responder_erro(
                interacao,
                titulo="Canal não encontrado",
                linhas=[f"ID `{canal_id}` não existe nesta guilda."],
            )
            return

        await responder_sucesso(
            interacao,
            titulo=f"{titulo} — iniciada",
            linhas=[
                f"Lendo <#{canal_id}>…",
                "Pode levar vários minutos. Não rode de novo até terminar.",
            ],
            delay=20,
        )

        apenas_bot = self.bot.user.id if so_bot and self.bot.user else None
        try:
            resultado = await importador(
                canal,
                limite=limite,
                apenas_bot_id=apenas_bot,
            )
        except Exception as erro:
            logger.exception("%s: %s", titulo, erro)
            await responder_erro(
                interacao,
                titulo="Falha na operação",
                linhas=[
                    f"Falha na importação: `{erro}`",
                ],
            )
            return

        resumo = (
            f"**{titulo} concluída**\n"
            f"• Mensagens lidas: **{resultado['lidas']}**\n"
            f"• Criadas: **{resultado['importadas']}**\n"
            f"• Atualizadas: **{resultado.get('atualizadas', 0)}**\n"
            f"• Já existiam: **{resultado['ja_existiam']}**\n"
            f"• Ignoradas (sem parse): **{resultado['ignoradas']}**\n"
            f"• Erros: **{resultado['erros']}**"
        )
        try:
            await responder_info(
                interacao,
                titulo="Importação concluída",
                linhas=[
                    resumo,
                ],
            )
        except discord.HTTPException:
            if interacao.channel:
                await interacao.channel.send(f"{interacao.user.mention}\n{resumo}")

    @grupo.command(name="plantao", description="LOG_PLANTAO → log_plantao")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só do bot")
    @is_authorized()
    async def recuperar_plantao(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        """
        Inicia a reconstrução de plantões a partir do canal histórico configurado.

        Encaminha o limite opcional e o filtro `so_bot` ao importador compartilhado,
        que grava no banco apenas os registros recuperáveis e devolve um resumo no
        Discord. Centralizar a execução evita que cada comando trate canal, erros e
        contagens de forma diferente.
        """
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_PLANTAO",
            chave_canal="LOG_PLANTAO",
            canal_id=id_canal_log_plantao(),
            importador=importar_log_plantao_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(name="recrutamentos", description="LOG_RECRUTAMENTOS → inícios")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só do bot")
    @is_authorized()
    async def recuperar_recrutamentos(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        """
        Reconstrói inícios de recrutamento usando as mensagens do canal de logs.

        O limite reduz uma recuperação extensa e `so_bot` pode restringir a leitura
        às mensagens automáticas. A operação delegada atualiza o banco e informa seu
        resultado no Discord, com as mesmas proteções usadas nos demais históricos.
        """
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_RECRUTAMENTOS",
            chave_canal="LOG_RECRUTAMENTOS",
            canal_id=id_canal_log("LOG_RECRUTAMENTOS"),
            importador=importar_log_recrutamentos_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(
        name="aprovacoes", description="LOG_APROVACOES → APROVADO + cargo + nota"
    )
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só do bot")
    @is_authorized()
    async def recuperar_aprovacoes(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        """
        Recria aprovações com seus cargos e notas a partir do histórico do Discord.

        Repasse o limite e a escolha de filtrar mensagens do bot ao fluxo comum,
        que verifica o canal, grava dados recuperados no banco e comunica erros ou
        contagens. Isso preserva a trilha de aprovação sem exigir edição manual.
        """
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_APROVACOES",
            chave_canal="LOG_APROVACOES",
            canal_id=id_canal_log("LOG_APROVACOES"),
            importador=importar_log_aprovacoes_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(name="reprovacoes", description="LOG_REPROVACOES → REPROVADO + nota")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só do bot")
    @is_authorized()
    async def recuperar_reprovacoes(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        """
        Restaura reprovações e suas notas lidas do canal de auditoria.

        O limite opcional controla a quantidade de mensagens e `so_bot` evita
        interpretar mensagens humanas quando desejado. A importação altera o banco
        de forma centralizada e apresenta um balanço para a pessoa autorizada.
        """
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_REPROVACOES",
            chave_canal="LOG_REPROVACOES",
            canal_id=id_canal_log("LOG_REPROVACOES"),
            importador=importar_log_reprovacoes_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(name="punicoes", description="LOG_PUNICOES → punicoes")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só do bot")
    @is_authorized()
    async def recuperar_punicoes(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        """
        Recupera punições registradas no canal histórico para o banco de dados.

        Permite limitar a busca e optar por mensagens apenas do bot, encaminhando
        ambas as escolhas ao importador comum. O fluxo valida o canal e relata
        registros criados, já existentes e erros para evitar importações às cegas.
        """
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_PUNICOES",
            chave_canal="LOG_PUNICOES",
            canal_id=id_canal_log("LOG_PUNICOES"),
            importador=importar_log_punicoes_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(name="chamadas", description="LOG_CHAMADAS → chamadas")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só do bot")
    @is_authorized()
    async def recuperar_chamadas(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        """
        Reconstrói chamadas usando as evidências disponíveis no canal de logs.

        Encaminha as opções de quantidade e autoria ao processamento compartilhado,
        que persiste os resultados no banco. A resposta no Discord distingue
        mensagens lidas, ignoradas e importadas, importante em uma recuperação longa.
        """
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_CHAMADAS",
            chave_canal="LOG_CHAMADAS",
            canal_id=id_canal_log("LOG_CHAMADAS"),
            importador=importar_log_chamadas_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(name="whitelist", description="LOG_WHITELIST → usuarios")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só do bot")
    @is_authorized()
    async def recuperar_whitelist(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        """
        Restaura dados de whitelist a partir das mensagens registradas no servidor.

        O limite opcional e o filtro de autoria são passados ao importador padrão,
        que valida o canal e atualiza o banco. A execução retorna um resumo para que
        a equipe confira se a base de usuários foi recomposta como esperado.
        """
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_WHITELIST",
            chave_canal="LOG_WHITELIST",
            canal_id=id_canal_log("LOG_WHITELIST"),
            importador=importar_log_whitelist_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(name="cargos", description="LOG_CARGOS → historico_cargos")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só do bot")
    @is_authorized()
    async def recuperar_cargos(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        """
        Reconstitui o histórico de cargos sem ler diretamente o estado atual.

        Usa mensagens de auditoria como fonte e repassa o limite e o filtro de bot
        ao mecanismo comum. Ele grava no banco o que puder interpretar e demonstra
        as contagens ao administrador para reduzir o risco de dados silenciosamente
        perdidos.
        """
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_CARGOS",
            chave_canal="LOG_CARGOS",
            canal_id=id_canal_log("LOG_CARGOS"),
            importador=importar_log_cargos_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(name="laudos", description="LOG_LAUDO → consultas + laudos")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só do bot")
    @is_authorized()
    async def recuperar_laudos(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        """
        Importa consultas e laudos que ainda existem no canal de registros.

        Permite reduzir a varredura por meio do limite e restringir sua origem ao
        bot. A rotina compartilhada escreve os dados interpretados no banco e
        apresenta um relatório, protegendo a equipe contra recuperações sem retorno.
        """
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_LAUDO",
            chave_canal="LOG_LAUDO",
            canal_id=id_canal_log("LOG_LAUDO"),
            importador=importar_log_laudos_do_canal,
            limite=limite,
            so_bot=so_bot,
        )

    @grupo.command(name="promocoes", description="LOG_PROMOVIDOS → historico_promocoes")
    @app_commands.describe(limite="Máximo de mensagens", so_bot="Só do bot")
    @is_authorized()
    async def recuperar_promocoes(
        self,
        interacao: discord.Interaction,
        limite: int | None = None,
        so_bot: bool = True,
    ):
        """
        Recupera o histórico de promoções publicado no canal de auditoria.

        Direciona as opções de escopo ao importador comum, que verifica a origem,
        persiste dados no banco e exibe as contagens. Assim, a reconstrução mantém
        comportamento seguro e uniforme com os outros tipos de registro.
        """
        await self._rodar_importacao(
            interacao,
            titulo="Recuperação LOG_PROMOVIDOS",
            chave_canal="LOG_PROMOVIDOS",
            canal_id=id_canal_log("LOG_PROMOVIDOS"),
            importador=importar_log_promocoes_do_canal,
            limite=limite,
            so_bot=so_bot,
        )


async def setup(bot: commands.Bot):
    """Registra os comandos administrativos de recuperação de históricos."""
    await bot.add_cog(RecuperacaoLogsCog(bot))
