import discord
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


async def _notificar(membro: discord.Member | None, texto: str):
    if membro is None:
        return
    try:
        await membro.send(texto)
    except discord.Forbidden:
        pass  # DM fechada — não quebra o fluxo, só não notifica


class PlantaoTasks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.verificar_ociosos.start()

    def cog_unload(self):
        self.verificar_ociosos.cancel()

    @tasks.loop(minutes=1)
    async def verificar_ociosos(self):
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
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
                inicio_ocioso = garantir_aware(estado.ocioso_desde)
                minutos = (datetime.now(timezone.utc) - inicio_ocioso).total_seconds() / 60
                membro = guild.get_member(estado.discord_id)

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

    @verificar_ociosos.before_loop
    async def antes_de_comecar(self):
        await self.bot.wait_until_ready()


async def executar_housekeeping_plantao(bot: commands.Bot):
    """Roda uma vez ao iniciar o bot: fecha sessões de plantão abandonadas há mais de 12h."""
    guild = bot.get_guild(GUILD_ID)
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