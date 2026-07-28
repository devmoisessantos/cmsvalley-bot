import discord
from discord.ext import commands
from datetime import datetime
from sqlalchemy import select

from src.config import obter_todos_ids_canais_plantao
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from src.plantao.plantao_service import _finalizar_periodo_em_call



def _canal_e_valido(channel: discord.VoiceChannel | None) -> bool:
    if channel is None:
        return False
    return channel.id in obter_todos_ids_canais_plantao()


class PlantaoListener(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(self, membro: discord.Member,
                                     before: discord.VoiceState, after: discord.VoiceState):
        estava_em_call_valida = _canal_e_valido(before.channel)
        esta_em_call_valida = _canal_e_valido(after.channel)

        # Nada relevante mudou (ex: só mutou o microfone) — ignora sem nem tocar no banco
        if estava_em_call_valida == esta_em_call_valida and before.channel == after.channel:
            return

        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == membro.id)
            )
            estado = resultado.scalar_one_or_none()

            if estado is None or not estado.toggle_ligado:
                return  # não está de plantão, eventos de voz não importam pra ele

            # Caso 1: ENTROU numa call válida (não estava em nenhuma antes)
            if esta_em_call_valida and not estava_em_call_valida:
                estado.em_call_valida = True
                estado.call_entrada_em = datetime.utcnow()
                estado.canal_atual_id = after.channel.id
                await session.commit()
                return

            # Caso 2: SAIU de uma call válida (fechou o app, desconectou, etc.)
            if estava_em_call_valida and not esta_em_call_valida:
                await _finalizar_periodo_em_call(estado)
                await session.commit()
                return

            # Caso 3: TROCOU entre duas calls válidas — continua contando, só atualiza o canal
            if estava_em_call_valida and esta_em_call_valida:
                estado.canal_atual_id = after.channel.id
                await session.commit()
                return


async def setup(bot: commands.Bot):
    await bot.add_cog(PlantaoListener(bot))