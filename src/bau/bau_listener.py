"""Listener 24/7 do canal LOG_BAU."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from src.bau.bau_logger import (
    enviar_dm_excesso,
    log_item_desconhecido,
    log_parse_falhou,
    publicar_alerta_caso,
)
from src.bau.bau_service import (
    marcar_dm_resultado,
    parsear_conteudo,
    processar_log_parseado,
    salvar_message_alerta,
)
from src.config import CANAIS

logger = logging.getLogger(__name__)


class BauListener(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.loop.create_task(self._registrar_views_persistentes())

    async def _registrar_views_persistentes(self):
        """Re-registra botões dos casos abertos após restart."""
        await self.bot.wait_until_ready()
        try:
            from sqlalchemy import select

            from src.bau.bau_views import ViewCasoBau
            from src.config import GUILD_ID
            from src.database.connection import async_session
            from src.database.models import CasoBau

            async with async_session() as sessao:
                resultado = await sessao.execute(
                    select(CasoBau).where(
                        CasoBau.status.in_(
                            ("AGUARDANDO", "GRAVE", "PRAZO_ESTOURADO", "PUNIDO")
                        )
                    )
                )
                casos = list(resultado.scalars().all())

            guilda = self.bot.get_guild(int(GUILD_ID))
            for caso in casos:
                view = ViewCasoBau.montar_layout_alerta(caso, guild=guilda)
                self.bot.add_view(view, message_id=caso.canal_alerta_message_id)
            logger.info("Views de %s casos de baú re-registradas", len(casos))
        except Exception as erro:
            logger.warning("Não re-registrou views de baú: %s", erro)

    @commands.Cog.listener()
    async def on_message(self, mensagem: discord.Message):
        if mensagem.author.bot and mensagem.author.id == getattr(
            self.bot.user, "id", None
        ):
            # ignora as próprias mensagens do bot
            pass

        canal_id = CANAIS.get("LOG_BAU") or 0
        if not canal_id or mensagem.channel.id != canal_id:
            return

        # processa webhook / bots do servidor de logs também
        conteudo = mensagem.content or ""
        if not conteudo.strip() and mensagem.embeds:
            # alguns sistemas mandam em embed description
            for embed in mensagem.embeds:
                if embed.description:
                    conteudo += "\n" + embed.description
                if embed.title:
                    conteudo = (embed.title or "") + "\n" + conteudo

        guilda = mensagem.guild
        if guilda is None:
            return

        log = parsear_conteudo(conteudo)
        if log is None:
            if conteudo.strip():
                try:
                    await log_parse_falhou(guilda, conteudo)
                except Exception as erro:
                    logger.warning("falha ao logar parse: %s", erro)
            return

        try:
            eventos = await processar_log_parseado(log)
        except Exception as erro:
            logger.exception("erro processando log baú: %s", erro)
            return

        for evento in eventos:
            tipo = evento.get("tipo")
            try:
                if tipo == "item_desconhecido":
                    await log_item_desconhecido(
                        guilda, evento["nome"], evento["id_fivem"]
                    )
                elif tipo == "caso_resolvido_auto":
                    # Itens devolvidos → fecha caso e desativa botões no alerta
                    from src.config import (
                        LIMITES_BAU_CAMADA_1,
                        LIMITES_BAU_CAMADA_2,
                    )
                    from src.database.connection import async_session
                    from src.database.models import CasoBau

                    async with async_session() as sessao:
                        caso_resolvido = await sessao.get(CasoBau, evento["caso_id"])
                    if caso_resolvido is None:
                        continue
                    if caso_resolvido.canal_alerta_message_id:
                        await publicar_alerta_caso(
                            guilda,
                            caso_resolvido,
                            limite_1=LIMITES_BAU_CAMADA_1.get(
                                caso_resolvido.item_canonico, 0
                            ),
                            limite_2=LIMITES_BAU_CAMADA_2.get(
                                caso_resolvido.item_canonico
                            ),
                            atualizar_mensagem_id=caso_resolvido.canal_alerta_message_id,
                        )
                elif tipo in ("caso_novo", "caso_atualizado"):
                    caso = evento["caso"]
                    msg_id = (
                        caso.canal_alerta_message_id
                        if tipo == "caso_atualizado"
                        else None
                    )
                    mensagem_alerta = await publicar_alerta_caso(
                        guilda,
                        caso,
                        limite_1=evento["limite_1"],
                        limite_2=evento.get("limite_2"),
                        atualizar_mensagem_id=msg_id,
                    )
                    if mensagem_alerta is not None:
                        await salvar_message_alerta(caso.id, mensagem_alerta.id)

                    if tipo == "caso_novo" and caso.discord_id:
                        membro = guilda.get_member(caso.discord_id)
                        if membro is not None:
                            ok_dm = await enviar_dm_excesso(membro, caso)
                            await marcar_dm_resultado(caso.id, falhou=not ok_dm)
                            if not ok_dm and mensagem_alerta is not None:
                                # re-publica com flag dm_falhou
                                from src.database.connection import async_session
                                from src.database.models import CasoBau

                                async with async_session() as sessao:
                                    caso_atual = await sessao.get(CasoBau, caso.id)
                                if caso_atual:
                                    await publicar_alerta_caso(
                                        guilda,
                                        caso_atual,
                                        limite_1=evento["limite_1"],
                                        limite_2=evento.get("limite_2"),
                                        atualizar_mensagem_id=mensagem_alerta.id,
                                    )
                        else:
                            await marcar_dm_resultado(caso.id, falhou=True)
            except Exception as erro_evento:
                logger.exception("evento baú %s falhou: %s", tipo, erro_evento)


async def setup(bot: commands.Bot):
    await bot.add_cog(BauListener(bot))
