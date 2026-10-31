"""
Dominio dedicado para o envio do Card no canal Booster.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks
from sqlalchemy import select

from src.booster.booster_panel import montar_mensagem_booster
from src.config import (
    CANAIS,
    GUILD_ID,
    HORARIOS_MENSAGENS_BOOSTER,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.utils.formatacao import agora_brasilia

registrador = logging.getLogger(__name__)

NOME_PAINEL = "booster"


class BoosterTasks(commands.Cog):
    """
    Cog só de tarefa: sobe o loop ao carregar e para ao descarregar.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ultimo_slot_executado: str | None = None
        self.republicar_mensagem_booster.start()

    def cog_unload(self) -> None:
        self.republicar_mensagem_booster.cancel()

    @tasks.loop(minutes=1)
    async def republicar_mensagem_booster(self) -> None:
        """
        Acorda a cada minuto e só age nos horários de Brasília
        definidos em HORARIOS_MENSAGENS_BOOSTER.
        """
        try:
            agora = agora_brasilia()
            slot = (agora.hour, agora.minute)

            if slot not in HORARIOS_MENSAGENS_BOOSTER:
                return

            # Evita rodar duas vezes no mesmo minuto do mesmo dia
            chave_do_slot = (
                f"{agora.date().isoformat()}-{agora.hour:02d}:{agora.minute:02d}"
            )
            if self._ultimo_slot_executado == chave_do_slot:
                return
            self._ultimo_slot_executado = chave_do_slot

            await self._apagar_antiga_e_publicar()
        except Exception as erro_capturado:
            registrador.exception(
                "Falha ao republicar a mensagem de booster: %s",
                erro_capturado,
            )

    @republicar_mensagem_booster.before_loop
    async def _esperar_bot_pronto(self) -> None:
        await self.bot.wait_until_ready()

    async def _apagar_antiga_e_publicar(self) -> None:
        """
        Apaga a mensagem antiga (se houver) e publica uma nova no canal.
        """
        canal_id = CANAIS.get("CANAL_MENSAGEM_BOOSTER") or 0
        if not canal_id:
            registrador.debug("CANAL_MENSAGEM_BOOSTER não configurado; pulando.")
            return

        canal = self.bot.get_channel(canal_id)
        if canal is None:
            try:
                canal = await self.bot.fetch_channel(canal_id)
            except Exception as erro_canal:
                registrador.warning(
                    "Não encontrei o canal de booster %s: %s",
                    canal_id,
                    erro_canal,
                )
                return

        guilda = self.bot.get_guild(int(GUILD_ID))
        if guilda is None and hasattr(canal, "guild"):
            guilda = canal.guild

        registro = await self._buscar_registro()

        if registro is not None:
            await self._apagar_mensagem_antiga(
                canal,
                registro.message_id,
            )

        view = montar_mensagem_booster(guilda)
        mensagem_nova = await canal.send(view=view)

        await self._salvar_registro(
            canal_id=canal.id,
            message_id=mensagem_nova.id,
            registro_antigo=registro,
        )

        registrador.info(
            "Mensagem de booster publicada (message_id=%s).",
            mensagem_nova.id,
        )

    async def _buscar_registro(self):
        """Busca o registro do painel booster em paineis_postados."""
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(PainelPostado).where(PainelPostado.nome_painel == NOME_PAINEL)
            )
            return resultado.scalar_one_or_none()

    async def _apagar_mensagem_antiga(
        self,
        canal: discord.abc.Messageable,
        message_id: int,
    ) -> None:
        """
        Tenta apagar a mensagem antiga. Se já não existir, só registra.
        """
        try:
            mensagem_antiga = await canal.fetch_message(message_id)
            await mensagem_antiga.delete()
            registrador.info(
                "Mensagem antiga de booster apagada (message_id=%s).",
                message_id,
            )
        except discord.NotFound:
            registrador.info(
                "Mensagem antiga de booster %s já não existe.",
                message_id,
            )
        except discord.HTTPException as erro_http:
            registrador.warning(
                "Não consegui apagar a mensagem de booster %s: %s",
                message_id,
                erro_http,
            )

    async def _salvar_registro(
        self,
        canal_id: int,
        message_id: int,
        registro_antigo,
    ) -> None:
        """
        Atualiza ou cria o registro em paineis_postados com o novo id.
        """
        async with async_session() as sessao:
            try:
                if registro_antigo is not None:
                    registro_antigo.message_id = message_id
                    registro_antigo.canal_id = canal_id
                    sessao.add(registro_antigo)
                else:
                    novo_registro = PainelPostado(
                        nome_painel=NOME_PAINEL,
                        canal_id=canal_id,
                        message_id=message_id,
                    )
                    sessao.add(novo_registro)
                await sessao.commit()
            except Exception as erro_do_banco:
                await sessao.rollback()
                registrador.exception(
                    "Falha ao salvar o id da mensagem de booster: %s",
                    erro_do_banco,
                )
                raise


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BoosterTasks(bot))
