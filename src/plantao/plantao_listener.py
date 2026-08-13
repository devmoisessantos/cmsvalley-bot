from datetime import (
    datetime,
    timezone,
)

import discord
from discord.ext import commands
from sqlalchemy import select

from src.config import obter_todos_ids_canais_plantao
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from src.plantao.plantao_logger import registrar_evento_plantao
from src.plantao.plantao_service import (
    _finalizar_periodo_em_call,
    garantir_aware,
)


def _canal_e_valido(channel: discord.VoiceChannel | None) -> bool:
    if channel is None:
        return False
    return (
        channel.id in obter_todos_ids_canais_plantao()
    )  # 👈 usa a função em vez de set(CANAIS_PLANTAO.values())


class PlantaoListener(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        membro: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        estava_em_call_valida = _canal_e_valido(before.channel)
        esta_em_call_valida = _canal_e_valido(after.channel)

        if (
            estava_em_call_valida == esta_em_call_valida
            and before.channel == after.channel
        ):
            return

        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == membro.id)
            )
            estado = resultado.scalar_one_or_none()

            if estado is None or not estado.toggle_ligado:
                return

            # Caso 1: ENTROU numa call válida
            if esta_em_call_valida and not estava_em_call_valida:
                agora = datetime.now(timezone.utc)
                estava_ocioso_desde = estado.ocioso_desde

                estado.em_call_valida = True
                estado.call_entrada_em = agora
                estado.segmento_iniciado_em = agora
                estado.canal_atual_id = after.channel.id
                estado.ocioso_desde = None
                estado.lembrete_1_enviado = False
                estado.lembrete_2_enviado = False
                await session.commit()

                if estava_ocioso_desde is not None:
                    duracao = int(
                        (agora - garantir_aware(estava_ocioso_desde)).total_seconds()
                    )
                    await registrar_evento_plantao(
                        membro.guild,
                        membro.id,
                        "OCIOSO_ENCERRADO",
                        estado.id_fivem,
                        duracao_segundos=duracao,
                        detalhes="Encerrado ao entrar em call",
                    )

                await registrar_evento_plantao(
                    membro.guild,
                    membro.id,
                    "ENTROU_CALL",
                    estado.id_fivem,
                    canal_id=after.channel.id,
                )
                return

            # Caso 2: SAIU de uma call válida (toggle continua ligado)
            if estava_em_call_valida and not esta_em_call_valida:
                await _finalizar_periodo_em_call(estado, membro.guild)
                await session.commit()
                return

            # Caso 3: TROCOU entre duas calls válidas
            if estava_em_call_valida and esta_em_call_valida:
                inicio_segmento = (
                    garantir_aware(estado.segmento_iniciado_em)
                    if estado.segmento_iniciado_em
                    else garantir_aware(estado.call_entrada_em)
                )
                duracao_segmento = int(
                    (datetime.now(timezone.utc) - inicio_segmento).total_seconds()
                )
                canal_anterior_id = estado.canal_atual_id

                estado.canal_atual_id = after.channel.id
                estado.segmento_iniciado_em = datetime.now(timezone.utc)
                await session.commit()

                await registrar_evento_plantao(
                    membro.guild,
                    membro.id,
                    "TROCOU_CALL",
                    estado.id_fivem,
                    canal_id=canal_anterior_id,
                    duracao_segundos=duracao_segmento,
                    detalhes=f"Foi para {after.channel.name}",
                )
                return


async def setup(bot: commands.Bot):
    await bot.add_cog(PlantaoListener(bot))
