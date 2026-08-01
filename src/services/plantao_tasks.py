import discord
import logging
from discord.ext import tasks, commands
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from src.config import (
    GUILD_ID, LEMBRETE_1_MINUTOS, LEMBRETE_2_MINUTOS,
    DESLIGAMENTO_AUTOMATICO_MINUTOS, HOUSEKEEPING_LIMITE_HORAS,
    AFK_AVISO_MINUTOS, AFK_LIMITE_MINUTOS, PENALIDADE_AFK_MOEDAS,
)
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from src.services.plantao_service import (
    garantir_aware, 
    _finalizar_periodo_em_call, 
    _membro_mutado_e_surdo,
)
from src.services.plantao_logger import registrar_evento_plantao


logger = logging.getLogger(__name__)

async def _notificar(membro: discord.Member | None, texto: str):
    if membro is None:
        logger.warning("⚠️ _notificar chamado com membro=None — get_member falhou (cache/intents?)")
        return
    try:
        await membro.send(texto)
        logger.info(f"✅ DM enviada para {membro} `({membro.id})`")
    except discord.Forbidden:
        logger.warning(f"⚠️ DM bloqueada para {membro} `({membro.id})` — Forbidden")

class PlantaoTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.verificar_ociosos.start()
        self.verificar_afk.start() 
        logger.info("🚀 PlantaoTasks Cog inicializado, loop deve começar após bot.wait_until_ready()")

    def cog_unload(self):
        self.verificar_ociosos.cancel()
        self.verificar_afk.cancel()

    @tasks.loop(minutes=1)
    async def verificar_ociosos(self):

        guild = self.bot.get_guild(int(GUILD_ID))
        if guild is None:
            logger.error(f"❌ Guild {GUILD_ID} não encontrada")
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
                    minutos = (datetime.now(timezone.utc) - inicio_ocioso).total_seconds() / 60
                    membro = guild.get_member(estado.discord_id)
                    id_fivem_atual = estado.id_fivem
                    logger.info(f"👤 {estado.discord_id}: {minutos:.2f} min ocioso, membro={membro}")

                    if minutos >= DESLIGAMENTO_AUTOMATICO_MINUTOS:
                        saldo_atual = estado.saldo_moedas
                        estado.toggle_ligado = False
                        estado.ocioso_desde = None
                        estado.lembrete_1_enviado = False
                        estado.lembrete_2_enviado = False
                        await _notificar(
                            membro,
                            f"🔴 Seu plantão foi encerrado automaticamente: mais de "
                            f"`{DESLIGAMENTO_AUTOMATICO_MINUTOS} minutos` sem estar em uma call.",
                        )
                        await registrar_evento_plantao(
                            guild, estado.discord_id, 
                            "DESLIGAMENTO_AUTOMATICO",
                            id_fivem_atual,
                            duracao_segundos=int(minutos * 60),
                            campos_extra={"Saldo no Momento": f"{saldo_atual} moedas"},
                        )

                    elif minutos >= LEMBRETE_2_MINUTOS and not estado.lembrete_2_enviado:
                        estado.lembrete_2_enviado = True
                        minutos_atuais = round(minutos, 1)
                        await _notificar(
                            membro,
                            f"⚠️ Já se passaram `{LEMBRETE_2_MINUTOS} minutos` sem você estar em call. "
                            "Conecte-se logo ou o plantão será encerrado automaticamente.",
                        )
                        await registrar_evento_plantao(
                            guild, 
                            estado.discord_id, 
                            "LEMBRETE_15",
                            id_fivem_atual,
                            campos_extra={"Tempo Ocioso": f"{minutos_atuais} min"},
                        )

                    elif minutos >= LEMBRETE_1_MINUTOS and not estado.lembrete_1_enviado:
                        estado.lembrete_1_enviado = True
                        minutos_atuais = round(minutos, 1)
                        await _notificar(
                            membro,
                            f"📌 Já se passaram `{LEMBRETE_1_MINUTOS} minutos` sem você estar em call. "
                            "Não esqueça de se conectar!",
                        )
                        await registrar_evento_plantao(
                            guild, 
                            estado.discord_id, 
                            "LEMBRETE_10",
                            id_fivem_atual,
                            campos_extra={"Tempo Ocioso": f"{minutos_atuais} min"},
                        )

                except Exception:
                    logger.exception(f"💥 Falha ao processar ociosidade de {estado.discord_id}, pulando pra o próximo")
                    continue

            await session.commit()

    @verificar_ociosos.error
    async def verificar_ociosos_error(self, error):
        logger.error(f"💥 Loop quebrou: {error}", exc_info=True)

    @verificar_ociosos.before_loop
    async def antes_de_comecar(self):
        logger.info("⏳ Aguardando bot.wait_until_ready()...")
        await self.bot.wait_until_ready()
        logger.info("✅ Bot pronto, loop vai começar a rodar")


    @tasks.loop(minutes=1)
    async def verificar_afk(self):
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
            logger.info(f"🔇 verificar_afk TICK — {len(estados)} em call ativa")

            for estado in estados:
                try:
                    membro = guild.get_member(estado.discord_id)
                    if membro is None:
                        continue

                    condicao_afk = _membro_mutado_e_surdo(membro)
                    canal_atual = estado.canal_atual_id

                    if not condicao_afk:
                        # Não está mais mudo+surdo — reseta o rastreamento
                        if estado.afk_mudo_surdo_desde is not None:
                            estado.afk_mudo_surdo_desde = None
                            estado.afk_canal_referencia_id = None
                            estado.afk_aviso_enviado = False
                        continue

                    # Está mudo+surdo agora — verifica se é continuação ou início novo
                    if estado.afk_mudo_surdo_desde is None or estado.afk_canal_referencia_id != canal_atual:
                        estado.afk_mudo_surdo_desde = agora
                        estado.afk_canal_referencia_id = canal_atual
                        estado.afk_aviso_enviado = False
                        continue

                    minutos_afk = (agora - garantir_aware(estado.afk_mudo_surdo_desde)).total_seconds() / 60

                    if minutos_afk >= AFK_LIMITE_MINUTOS:
                        await self._desconectar_por_afk(guild, estado, membro, session)

                    elif minutos_afk >= AFK_AVISO_MINUTOS and not estado.afk_aviso_enviado:
                        estado.afk_aviso_enviado = True
                        await _notificar(
                            membro,
                            f"🔇 Você está mudo e surdo há quase {AFK_LIMITE_MINUTOS // 60}h no mesmo canal. "
                            "Se você não estiver mais ativo, será desconectado automaticamente em breve "
                            f"e perderá {PENALIDADE_AFK_MOEDAS} moedas de penalidade.",
                        )
                        await registrar_evento_plantao(
                            guild, estado.discord_id, "AFK_AVISO", estado.id_fivem,
                            campos_extra={"Minutos Mudo+Surdo": f"{round(minutos_afk, 1)}"},
                        )

                except Exception:
                    logger.exception(f"💥 Falha ao processar AFK de {estado.discord_id}, pulando")
                    continue

            await session.commit()

    async def _desconectar_por_afk(self, guild, estado, membro, session):
        """Fecha o segmento de call (credita o que já era devido), desconecta fisicamente
        da call de voz, e aplica a penalidade de moedas."""
        await _finalizar_periodo_em_call(
            estado, guild,
            evento="CALL_ENCERRADA_POR_AFK",
            motivo=f"Mudo+surdo por {AFK_LIMITE_MINUTOS} min no mesmo canal, sem atividade",
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
            logger.warning(f"⚠️ Sem permissão 'Mover Membros' para desconectar {membro.id} por AFK")
        except discord.HTTPException:
            logger.warning(f"⚠️ Falha ao desconectar {membro.id} por AFK (HTTPException)")

        await _notificar(
            membro,
            f"🔇 Você foi desconectado automaticamente da call por inatividade "
            f"(mudo e surdo por {AFK_LIMITE_MINUTOS} minutos no mesmo canal). "
            f"Penalidade aplicada: -{PENALIDADE_AFK_MOEDAS} moedas.",
        )

        await registrar_evento_plantao(
            guild, estado.discord_id, "PENALIDADE_AFK", id_fivem_atual,
            campos_extra={
                "Moedas Removidas": str(PENALIDADE_AFK_MOEDAS),
                "Saldo Após Penalidade": f"{saldo_apos_penalidade} moedas",
            },
        )

    @verificar_afk.error
    async def verificar_afk_error(self, error):
        logger.error(f"💥 Loop verificar_afk quebrou: {error}", exc_info=True)

    @verificar_afk.before_loop
    async def antes_de_comecar_afk(self):
        await self.bot.wait_until_ready()


async def executar_housekeeping_plantao(bot: commands.Bot):
    """Roda uma vez ao iniciar o bot: fecha sessões de plantão abandonadas há mais de 12h."""
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
                continue  # sessão recente, não mexe

            if estado.em_call_valida:
                await _finalizar_periodo_em_call(estado)

            estado.toggle_ligado = False
            estado.ocioso_desde = None
            estado.lembrete_1_enviado = False
            estado.lembrete_2_enviado = False

            membro = guild.get_member(estado.discord_id)
            await _notificar(
                membro,
                "🔧 Seu plantão foi encerrado automaticamente pelo sistema "
                f"(sessão ficou aberta por mais de {HOUSEKEEPING_LIMITE_HORAS}h sem atividade).",
            )
            await registrar_evento_plantao(
                guild, 
                estado.discord_id, 
                "HOUSEKEEPING",
                estado.id_fivem
            )
        await session.commit()


async def setup(bot: commands.Bot):
    await bot.add_cog(PlantaoTasks(bot))