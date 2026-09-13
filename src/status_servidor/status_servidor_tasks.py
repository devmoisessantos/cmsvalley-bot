"""
Tarefa que atualiza o painel de status do servidor FiveM periodicamente.

A cada INTERVALO_ATUALIZACAO_SEGUNDOS busca o status e edita a mesma
mensagem no canal configurado. Se a mensagem ainda não existe, publica
uma nova e grava o ID no banco (PainelPostado).
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks
from sqlalchemy import select

from src.config import (
    CANAIS,
    GUILD_ID,
    STATUS_SERVIDOR,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.status_servidor.status_servidor_panel import montar_painel_status
from src.status_servidor.status_servidor_service import buscar_status_do_servidor

registrador = logging.getLogger(__name__)

NOME_PAINEL = "status_servidor"


class StatusServidorTasks(commands.Cog):
    """
    Cog só de tarefa: sobe o loop ao carregar e para ao descarregar.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.atualizar_painel_status.start()

    def cog_unload(self) -> None:
        self.atualizar_painel_status.cancel()

    @tasks.loop(seconds=STATUS_SERVIDOR["INTERVALO_ATUALIZACAO_SEGUNDOS"])
    async def atualizar_painel_status(self) -> None:
        """
        Busca status e atualiza (ou cria) a mensagem do painel.
        """
        try:
            await self._atualizar_ou_publicar()
        except Exception as erro_capturado:
            registrador.exception(
                "Falha ao atualizar o painel de status do servidor: %s",
                erro_capturado,
            )

    @atualizar_painel_status.before_loop
    async def _esperar_bot_pronto(self) -> None:
        await self.bot.wait_until_ready()

    async def _atualizar_ou_publicar(self) -> None:
        canal_id = CANAIS.get("CANAL_STATUS_SERVIDOR") or 0
        if not canal_id:
            registrador.debug(
                "CANAL_STATUS_SERVIDOR não configurado; pulando atualização."
            )
            return

        canal = self.bot.get_channel(canal_id)
        if canal is None:
            try:
                canal = await self.bot.fetch_channel(canal_id)
            except Exception as erro_canal:
                registrador.warning(
                    "Não encontrei o canal de status %s: %s",
                    canal_id,
                    erro_canal,
                )
                return

        dados = await buscar_status_do_servidor()
        view = montar_painel_status(dados)

        registro = await self._buscar_registro()

        if registro is not None:
            atualizado = await self._editar_mensagem_existente(
                canal,
                registro.message_id,
                view,
            )
            if atualizado:
                return
            # Mensagem sumiu: publica de novo e atualiza o registro.
            await self._publicar_nova_mensagem(canal, view, registro)
            return

        await self._publicar_nova_mensagem(canal, view, None)

    async def _buscar_registro(self):
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(PainelPostado).where(
                    PainelPostado.nome_painel == NOME_PAINEL
                )
            )
            return resultado.scalar_one_or_none()

    async def _editar_mensagem_existente(
        self,
        canal: discord.abc.Messageable,
        message_id: int,
        view: discord.ui.LayoutView,
    ) -> bool:
        try:
            mensagem = await canal.fetch_message(message_id)
            await mensagem.edit(view=view)
            return True
        except discord.NotFound:
            registrador.info(
                "Mensagem de status %s não existe mais; vou republicar.",
                message_id,
            )
            return False
        except discord.HTTPException as erro_http:
            registrador.warning(
                "Falha ao editar mensagem de status %s: %s",
                message_id,
                erro_http,
            )
            return False

    async def _publicar_nova_mensagem(
        self,
        canal: discord.abc.Messageable,
        view: discord.ui.LayoutView,
        registro_antigo,
    ) -> None:
        mensagem = await canal.send(view=view)

        async with async_session() as sessao:
            if registro_antigo is not None:
                registro_antigo.message_id = mensagem.id
                registro_antigo.canal_id = canal.id
                sessao.add(registro_antigo)
            else:
                novo = PainelPostado(
                    nome_painel=NOME_PAINEL,
                    canal_id=canal.id,
                    message_id=mensagem.id,
                )
                sessao.add(novo)
            await sessao.commit()

        registrador.info(
            "Painel de status publicado/republicado (message_id=%s).",
            mensagem.id,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatusServidorTasks(bot))
