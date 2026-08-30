"""
Tarefas que rodam sozinhas de tempo em tempo, cuidando dos plantoes.

O que elas fazem
----------------
- Avisam quem esta parado na call ha muito tempo (os lembretes de ociosidade).
- Fecham plantao de quem sumiu sem bater ponto de saida.
- `executar_housekeeping_plantao` faz a limpeza: estados orfaos, sessoes que
  ficaram abertas de um dia para o outro.

Sem essa faxina, um plantao esquecido ficaria contando hora para sempre.

`_resetar_lembretes_ociosidade` zera os avisos quando a pessoa volta a se mexer,
senao ela receberia o mesmo aviso repetido.
"""

import logging
from datetime import (
    datetime,
    timedelta,
    timezone,
)

import discord
from discord.ext import (
    commands,
    tasks,
)
from sqlalchemy import select
from sqlalchemy.exc import (
    DBAPIError,
    OperationalError,
)

from src.config import (
    AFK_AVISO_MINUTOS,
    AFK_LIMITE_MINUTOS,
    DESLIGAMENTO_AUTOMATICO_MINUTOS,
    GUILD_ID,
    HOUSEKEEPING_LIMITE_HORAS,
    LEMBRETE_1_MINUTOS,
    LEMBRETE_2_MINUTOS,
    LEMBRETE_3_MINUTOS,
    PENALIDADE_AFK_MOEDAS,
)
from src.database.conexao import (
    async_session,
    reiniciar_pool_se_preciso,
)
from src.database.models import EstadoPlantao
from src.plantao.plantao_logger import registrar_evento_plantao
from src.plantao.plantao_service import (
    _finalizar_periodo_em_call,
    _membro_conta_tempo_moeda,
    _membro_surdo,
    garantir_aware,
    pausar_cronometro_moeda,
    retomar_cronometro_moeda,
)
from src.utils.notificacao import (
    notificar_dm_plantao_afk_aviso,
    notificar_dm_plantao_afk_desconectado,
    notificar_dm_plantao_desligado_automatico,
    notificar_dm_plantao_housekeeping,
    notificar_dm_plantao_lembrete_ocioso,
)

logger = logging.getLogger(__name__)


def _resetar_lembretes_ociosidade(estado: EstadoPlantao) -> None:
    estado.lembrete_1_enviado = False
    estado.lembrete_2_enviado = False
    estado.lembrete_3_enviado = False


class PlantaoTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.verificar_ociosos.start()
        self.verificar_afk.start()
        logger.info(
            "PlantaoTasks inicializado — ociosidade checada a cada 1 minuto "
            "(avisos %s/%s/%s min, desliga em %s min)",
            LEMBRETE_1_MINUTOS,
            LEMBRETE_2_MINUTOS,
            LEMBRETE_3_MINUTOS,
            DESLIGAMENTO_AUTOMATICO_MINUTOS,
        )

    def cog_unload(self):
        """Cancela os loops para impedir tarefas duplicadas ao descarregar o cog."""
        self.verificar_ociosos.cancel()
        self.verificar_afk.cancel()

    # ------------------------------------------------------------------
    # Ociosidade (fora de call)
    # ------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def verificar_ociosos(self):
        """Avisa e desliga estados ociosos conforme os limites configurados.

        Percorre no banco somente quem mantém o toggle ligado fora de call, envia cada
        lembrete uma vez e persiste o desligamento automático. Isso evita que um
        plantão esquecido continue ativo indefinidamente e distorça a operação.
        """
        guild = self.bot.get_guild(int(GUILD_ID))
        if guild is None:
            logger.error("Guild %s não encontrada", GUILD_ID)
            return

        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(
                    EstadoPlantao.toggle_ligado.is_(True),
                    EstadoPlantao.ocioso_desde.is_not(None),
                )
            )
            estados = resultado.scalars().all()

            for estado in estados:
                try:
                    inicio_ocioso = garantir_aware(estado.ocioso_desde)
                    minutos = (
                        datetime.now(timezone.utc) - inicio_ocioso
                    ).total_seconds() / 60
                    membro = guild.get_member(estado.discord_id)
                    id_fivem_atual = estado.id_fivem
                    logger.info(
                        "%s: %.2f min ocioso, membro=%s",
                        estado.discord_id,
                        minutos,
                        membro,
                    )

                    if minutos >= DESLIGAMENTO_AUTOMATICO_MINUTOS:
                        saldo_atual = estado.saldo_moedas
                        estado.toggle_ligado = False
                        estado.ocioso_desde = None
                        _resetar_lembretes_ociosidade(estado)
                        await notificar_dm_plantao_desligado_automatico(
                            membro,
                            minutos=DESLIGAMENTO_AUTOMATICO_MINUTOS,
                            guilda=guild,
                        )
                        await registrar_evento_plantao(
                            guild,
                            estado.discord_id,
                            "DESLIGAMENTO_AUTOMATICO",
                            id_fivem_atual,
                            duracao_segundos=int(minutos * 60),
                            campos_extra={"Saldo no Momento": f"{saldo_atual} moedas"},
                        )

                    elif (
                        minutos >= LEMBRETE_3_MINUTOS and not estado.lembrete_3_enviado
                    ):
                        estado.lembrete_3_enviado = True
                        await notificar_dm_plantao_lembrete_ocioso(
                            membro,
                            minutos=LEMBRETE_3_MINUTOS,
                            nivel=3,
                            guilda=guild,
                        )

                    elif (
                        minutos >= LEMBRETE_2_MINUTOS and not estado.lembrete_2_enviado
                    ):
                        estado.lembrete_2_enviado = True
                        await notificar_dm_plantao_lembrete_ocioso(
                            membro,
                            minutos=LEMBRETE_2_MINUTOS,
                            nivel=2,
                            guilda=guild,
                        )

                    elif (
                        minutos >= LEMBRETE_1_MINUTOS and not estado.lembrete_1_enviado
                    ):
                        estado.lembrete_1_enviado = True
                        await notificar_dm_plantao_lembrete_ocioso(
                            membro,
                            minutos=LEMBRETE_1_MINUTOS,
                            nivel=1,
                            guilda=guild,
                        )

                except Exception:
                    logger.exception(
                        "Falha ao processar ociosidade de %s",
                        estado.discord_id,
                    )
                    continue

            await session.commit()

    @verificar_ociosos.error
    async def verificar_ociosos_error(self, error):
        """
        Registra falhas do loop de ociosidade e tenta recuperar conexões encerradas.
        """
        logger.error("Loop ociosos quebrou: %s", error, exc_info=True)
        await self._recuperar_pool_se_conexao_morta(error)

    @verificar_ociosos.before_loop
    async def antes_de_comecar(self):
        """Espera o Discord ficar pronto antes da primeira verificação de ociosidade."""
        await self.bot.wait_until_ready()
        logger.info("Bot pronto — loop de ociosidade ativo")

    # ------------------------------------------------------------------
    # AFK (mudo + surdo)
    # ------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def verificar_afk(self):
        """Pausa moedas e desconecta membros surdos pelo tempo configurado.

        Distingue ficar apenas mutado de ficar surdo, pois apenas o segundo caso deixa
        de contar tempo. Persiste os marcos de AFK para que avisos e penalidades não
        sejam repetidos a cada execução do loop.
        """
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return

        agora = datetime.now(timezone.utc)

        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(
                    EstadoPlantao.toggle_ligado.is_(True),
                    EstadoPlantao.em_call_valida.is_(True),
                )
            )
            estados = resultado.scalars().all()
            logger.info("verificar_afk — %s em call ativa", len(estados))

            for estado in estados:
                try:
                    membro = guild.get_member(estado.discord_id)
                    if membro is None:
                        continue

                    # Surdo (com ou sem mudo) → não gera moeda + rastreio AFK
                    # Mutado sozinho → continua gerando moeda
                    esta_surdo = _membro_surdo(membro)
                    canal_atual = estado.canal_atual_id

                    if not esta_surdo:
                        # Voltou a ouvir → retoma cronômetro se ainda em call
                        if estado.afk_mudo_surdo_desde is not None:
                            estado.afk_mudo_surdo_desde = None
                            estado.afk_canal_referencia_id = None
                            estado.afk_aviso_enviado = False
                        if _membro_conta_tempo_moeda(membro):
                            retomar_cronometro_moeda(estado)
                        continue

                    # Está surdo: garante que o cronômetro de moeda está pausado
                    # e grava o trecho no log (sem isso o tempo some do ciclo).
                    if estado.segmento_iniciado_em is not None:
                        segundos_fechados = pausar_cronometro_moeda(
                            estado,
                            motivo="Surdo detectado no loop AFK",
                        )
                        if segundos_fechados > 0:
                            await registrar_evento_plantao(
                                guild,
                                estado.discord_id,
                                "MOEDA_PAUSADA",
                                estado.id_fivem,
                                canal_id=estado.canal_atual_id,
                                duracao_segundos=segundos_fechados,
                                detalhes=(
                                    "Surdo detectado no loop AFK — "
                                    "tempo não conta para moeda"
                                ),
                            )

                    if (
                        estado.afk_mudo_surdo_desde is None
                        or estado.afk_canal_referencia_id != canal_atual
                    ):
                        estado.afk_mudo_surdo_desde = agora
                        estado.afk_canal_referencia_id = canal_atual
                        estado.afk_aviso_enviado = False
                        continue

                    minutos_afk = (
                        agora - garantir_aware(estado.afk_mudo_surdo_desde)
                    ).total_seconds() / 60

                    if minutos_afk >= AFK_LIMITE_MINUTOS:
                        await self._desconectar_por_afk(guild, estado, membro, session)

                    elif (
                        minutos_afk >= AFK_AVISO_MINUTOS
                        and not estado.afk_aviso_enviado
                    ):
                        estado.afk_aviso_enviado = True
                        await notificar_dm_plantao_afk_aviso(
                            membro,
                            limite_minutos=AFK_LIMITE_MINUTOS,
                            penalidade_moedas=PENALIDADE_AFK_MOEDAS,
                            guilda=guild,
                        )

                except Exception:
                    logger.exception("Falha ao processar AFK de %s", estado.discord_id)
                    continue

            await session.commit()

    async def _desconectar_por_afk(self, guild, estado, membro, session):
        """Fecha segmento, desconecta da call e aplica penalidade de moedas."""
        await _finalizar_periodo_em_call(
            estado,
            guild,
            evento="CALL_ENCERRADA_POR_AFK",
            motivo=(
                f"Surdo por {AFK_LIMITE_MINUTOS} min no mesmo canal "
                "(tempo surdo não contou para moeda)"
            ),
        )

        estado.saldo_moedas = max(0, estado.saldo_moedas - PENALIDADE_AFK_MOEDAS)
        estado.afk_mudo_surdo_desde = None
        estado.afk_canal_referencia_id = None
        estado.afk_aviso_enviado = False
        saldo_apos_penalidade = estado.saldo_moedas
        id_fivem_atual = estado.id_fivem

        try:
            await membro.move_to(None)
        except discord.Forbidden:
            logger.warning("Sem permissão para desconectar %s por AFK", membro.id)
        except discord.HTTPException:
            logger.warning("Falha HTTP ao desconectar %s por AFK", membro.id)

        await notificar_dm_plantao_afk_desconectado(
            membro,
            limite_minutos=AFK_LIMITE_MINUTOS,
            penalidade_moedas=PENALIDADE_AFK_MOEDAS,
            guilda=guild,
        )

        await registrar_evento_plantao(
            guild,
            estado.discord_id,
            "PENALIDADE_AFK",
            id_fivem_atual,
            campos_extra={
                "Moedas Removidas": str(PENALIDADE_AFK_MOEDAS),
                "Saldo Após Penalidade": f"{saldo_apos_penalidade} moedas",
            },
        )

    @verificar_afk.error
    async def verificar_afk_error(self, error):
        """Registra falhas do monitor AFK e tenta restaurar o pool quando necessário."""
        logger.error("Loop AFK quebrou: %s", error, exc_info=True)
        await self._recuperar_pool_se_conexao_morta(error)

    async def _recuperar_pool_se_conexao_morta(self, error: BaseException) -> None:
        """Reinicia o pool se a falha for conexão fechada pelo servidor."""
        texto = str(error)
        e_conexao = isinstance(error, (DBAPIError, OperationalError)) or (
            "ConnectionDoesNotExist" in texto
            or "connection was closed" in texto.lower()
            or "ConnectionDoesNotExistError" in texto
        )
        if not e_conexao:
            return
        try:
            await reiniciar_pool_se_preciso()
            logger.warning(
                "Pool PostgreSQL reiniciado após conexão fechada no meio da operação"
            )
        except Exception as erro_pool:
            logger.error("Falha ao reiniciar pool: %s", erro_pool)

    @verificar_afk.before_loop
    async def antes_de_comecar_afk(self):
        """Aguarda a conexão do bot antes de iniciar o monitor de AFK."""
        await self.bot.wait_until_ready()


async def executar_housekeeping_plantao(bot: commands.Bot):
    """Fecha sessões de plantão abandonadas há mais de HOUSEKEEPING_LIMITE_HORAS."""
    guild = bot.get_guild(int(GUILD_ID))
    if guild is None:
        return

    limite = datetime.now(timezone.utc) - timedelta(hours=HOUSEKEEPING_LIMITE_HORAS)

    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.toggle_ligado.is_(True))
        )
        estados = resultado.scalars().all()

        for estado in estados:
            ultima = garantir_aware(estado.ultima_atualizacao)
            if ultima >= limite:
                continue

            if estado.em_call_valida:
                await _finalizar_periodo_em_call(
                    estado,
                    guild,
                    evento="HOUSEKEEPING_CALL_ENCERRADA",
                    motivo=(
                        f"Plantao aberto ha mais de "
                        f"{HOUSEKEEPING_LIMITE_HORAS}h sem atualizacao"
                    ),
                )

            estado.toggle_ligado = False
            estado.ocioso_desde = None
            _resetar_lembretes_ociosidade(estado)

            membro = guild.get_member(estado.discord_id)
            await notificar_dm_plantao_housekeeping(
                membro,
                horas_limite=HOUSEKEEPING_LIMITE_HORAS,
                guilda=guild,
            )
            await registrar_evento_plantao(
                guild,
                estado.discord_id,
                "HOUSEKEEPING",
                estado.id_fivem,
            )
        await session.commit()


async def setup(bot: commands.Bot):
    """Registra e inicia as tarefas periódicas que protegem o estado de plantão."""
    await bot.add_cog(PlantaoTasks(bot))
