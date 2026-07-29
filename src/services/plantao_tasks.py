import discord
import logging
from discord.ext import tasks, commands
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from src.config import (
    GUILD_ID, LEMBRETE_1_MINUTOS, LEMBRETE_2_MINUTOS,
    DESLIGAMENTO_AUTOMATICO_MINUTOS, HOUSEKEEPING_LIMITE_HORAS,
)
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from src.services.plantao_service import garantir_aware, _finalizar_periodo_em_call


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
        logger.info("🚀 PlantaoTasks Cog inicializado, loop deve começar após bot.wait_until_ready()")

    def cog_unload(self):
        self.verificar_ociosos.cancel()


    @tasks.loop(minutes=1)
    async def verificar_ociosos(self):
        logger.info("🔄 verificar_ociosos TICK")  # 👈 se isso não aparecer no log a cada 1 min, o loop não está rodando

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
            logger.info(f"🔎 {len(estados)} estado(s) ocioso(s) encontrados")

            for estado in estados:
                inicio_ocioso = garantir_aware(estado.ocioso_desde)
                minutos = (datetime.now(timezone.utc) - inicio_ocioso).total_seconds() / 60
                membro = guild.get_member(estado.discord_id)
                logger.info(f"👤 {estado.discord_id}: {minutos:.2f} min ocioso, membro={membro}")

                if minutos >= DESLIGAMENTO_AUTOMATICO_MINUTOS:
                    estado.toggle_ligado = False
                    estado.ocioso_desde = None
                    estado.lembrete_1_enviado = False
                    estado.lembrete_2_enviado = False
                    await _notificar(
                        membro,
                        f"🔴 Seu plantão foi encerrado automaticamente: mais de "
                        f"`{DESLIGAMENTO_AUTOMATICO_MINUTOS} minutos` sem estar em uma call.",
                    )

                elif minutos >= LEMBRETE_2_MINUTOS and not estado.lembrete_2_enviado:
                    estado.lembrete_2_enviado = True
                    await _notificar(
                        membro,
                        f"⚠️ Já se passaram `{LEMBRETE_2_MINUTOS} minutos` sem você estar em call. "
                        "Conecte-se logo ou o plantão será encerrado automaticamente.",
                    )

                elif minutos >= LEMBRETE_1_MINUTOS and not estado.lembrete_1_enviado:
                    estado.lembrete_1_enviado = True
                    await _notificar(
                        membro,
                        f"📌 Já se passaram `{LEMBRETE_1_MINUTOS} minutos` sem você estar em call. "
                        "Não esqueça de se conectar!",
                    )
            await session.commit()

    @verificar_ociosos.error
    async def verificar_ociosos_error(self, error):
        logger.error(f"💥 Loop quebrou: {error}", exc_info=True)

    @verificar_ociosos.before_loop
    async def antes_de_comecar(self):
        logger.info("⏳ Aguardando bot.wait_until_ready()...")
        await self.bot.wait_until_ready()
        logger.info("✅ Bot pronto, loop vai começar a rodar")

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
        await session.commit()

async def setup(bot: commands.Bot):
    await bot.add_cog(PlantaoTasks(bot))