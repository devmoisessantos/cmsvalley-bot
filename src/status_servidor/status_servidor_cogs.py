"""
Comandos administrativos do painel de status do servidor FiveM.

/status publicar — força a publicação ou atualização imediata do painel
no canal configurado em CANAL_STATUS_SERVIDOR.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from src.config import CANAIS
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.status_servidor.status_servidor_panel import montar_painel_status
from src.status_servidor.status_servidor_service import buscar_status_do_servidor
from src.utils.mensagens import responder_erro, responder_sucesso
from src.utils.permissions import esta_autorizado

registrador = logging.getLogger(__name__)

NOME_PAINEL = "status_servidor"


class StatusServidorCogs(commands.Cog):
    """
    Comandos de barra do domínio status_servidor.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="status",
        description="Publica ou atualiza o painel de status do servidor FiveM.",
    )
    @app_commands.describe(
        acao="O que fazer com o painel de status.",
    )
    @app_commands.choices(
        acao=[
            app_commands.Choice(name="publicar", value="publicar"),
        ]
    )
    @esta_autorizado()
    async def comando_status(
        self,
        interacao: discord.Interaction,
        acao: app_commands.Choice[str],
    ) -> None:
        """
        Publica o painel de status no canal configurado (ou atualiza se
        já existir).
        """
        if acao.value != "publicar":
            await responder_erro(
                interacao,
                titulo="Ação inválida",
                linhas=["Ação não reconhecida."],
            )
            return

        canal_id = CANAIS.get("CANAL_STATUS_SERVIDOR") or 0
        if not canal_id:
            await responder_erro(
                interacao,
                titulo="Canal não configurado",
                linhas=[
                    "O canal de status ainda não está em config.py.",
                    "Preencha CANAIS['CANAL_STATUS_SERVIDOR'] e reinicie.",
                ],
            )
            return

        canal = interacao.client.get_channel(canal_id)
        if canal is None:
            try:
                canal = await interacao.client.fetch_channel(canal_id)
            except Exception:
                await responder_erro(
                    interacao,
                    titulo="Canal não encontrado",
                    linhas=["Não encontrei o canal de status configurado."],
                )
                return

        await interacao.response.defer(ephemeral=True)

        try:
            dados = await buscar_status_do_servidor()
            view = montar_painel_status(dados)

            async with async_session() as sessao:
                resultado = await sessao.execute(
                    select(PainelPostado).where(
                        PainelPostado.nome_painel == NOME_PAINEL
                    )
                )
                registro = resultado.scalar_one_or_none()

                if registro is not None:
                    try:
                        mensagem = await canal.fetch_message(registro.message_id)
                        await mensagem.edit(view=view)
                        await responder_sucesso(
                            interacao,
                            titulo="Painel atualizado",
                            linhas=["Painel de status atualizado com sucesso."],
                        )
                        return
                    except discord.NotFound:
                        pass

                mensagem = await canal.send(view=view)

                if registro is not None:
                    registro.message_id = mensagem.id
                    registro.canal_id = canal.id
                    sessao.add(registro)
                else:
                    novo = PainelPostado(
                        nome_painel=NOME_PAINEL,
                        canal_id=canal.id,
                        message_id=mensagem.id,
                    )
                    sessao.add(novo)

                await sessao.commit()

            await responder_sucesso(
                interacao,
                titulo="Painel publicado",
                linhas=["Painel de status publicado no canal configurado."],
            )
        except Exception as erro_capturado:
            registrador.exception(
                "Falha no comando /status publicar: %s",
                erro_capturado,
            )
            await responder_erro(
                interacao,
                titulo="Falha ao publicar",
                linhas=["Não consegui publicar o painel de status agora."],
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatusServidorCogs(bot))
