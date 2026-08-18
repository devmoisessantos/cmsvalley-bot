"""
Ouvinte que cronometra o plantao pela entrada e saida na call de voz.

O Discord avisa este arquivo toda vez que alguem entra, sai ou muda de canal de
voz. Ele confere duas coisas antes de contar tempo: se o canal e um canal de
plantao (`_canal_e_valido`) e se a pessoa esta em estado que conta tempo
(`_voice_conta_tempo`) — quem esta com o microfone desligado pelo servidor, por
exemplo, nao esta trabalhando.

Sem essas duas conferencias, entrar em qualquer call do servidor viraria hora
paga.
"""

from datetime import (
    datetime,
    timezone,
)

import discord
from discord.ext import commands
from sqlalchemy import select

from src.config import obter_todos_ids_canais_plantao
from src.database.conexao import async_session
from src.database.models import EstadoPlantao
from src.plantao.plantao_logger import registrar_evento_plantao
from src.plantao.plantao_service import (
    _finalizar_periodo_em_call,
    garantir_aware,
    pausar_cronometro_moeda,
    retomar_cronometro_moeda,
)


def _canal_e_valido(channel: discord.abc.GuildChannel | None) -> bool:
    if channel is None:
        return False
    return channel.id in obter_todos_ids_canais_plantao()


def _voice_conta_tempo(state: discord.VoiceState | None) -> bool:
    """Mesma regra de _membro_conta_tempo_moeda, a partir do VoiceState."""
    if state is None or state.channel is None:
        return False
    # Surdo (self ou server) → não conta. Mutado sozinho → conta.
    if state.self_deaf or state.deaf:
        return False
    return True


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
        """Sincroniza o plantão com entradas, saídas e pausas nas calls válidas.

        Grava no banco apenas mudanças que afetam a elegibilidade de tempo e moedas,
        além de registrar eventos de auditoria. Distinguir troca de canal de ficar
        surdo impede conceder moedas enquanto o membro não participa da chamada.
        """
        estava_em_call_valida = _canal_e_valido(before.channel)
        esta_em_call_valida = _canal_e_valido(after.channel)

        mudou_canal = before.channel != after.channel
        mudou_contagem = _voice_conta_tempo(before) != _voice_conta_tempo(after)

        # Sem mudança de call válida nem de estado que afeta moeda → ignora
        if not mudou_canal and not mudou_contagem:
            return
        if (
            not mudou_canal
            and estava_em_call_valida == esta_em_call_valida
            and not mudou_contagem
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
                estado.canal_atual_id = after.channel.id
                estado.ocioso_desde = None
                estado.lembrete_1_enviado = False
                estado.lembrete_2_enviado = False
                estado.lembrete_3_enviado = False

                if _voice_conta_tempo(after):
                    estado.segmento_iniciado_em = agora
                    estado.afk_mudo_surdo_desde = None
                    estado.afk_canal_referencia_id = None
                    estado.afk_aviso_enviado = False
                else:
                    # Entrou já surdo → não conta moeda
                    estado.segmento_iniciado_em = None
                    estado.afk_mudo_surdo_desde = agora
                    estado.afk_canal_referencia_id = after.channel.id
                    estado.afk_aviso_enviado = False

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
            if estava_em_call_valida and esta_em_call_valida and mudou_canal:
                # Fecha segmento do canal anterior só se estava contando
                if estado.segmento_iniciado_em is not None:
                    pausar_cronometro_moeda(estado, motivo="Trocou de call")

                canal_anterior_id = estado.canal_atual_id
                estado.canal_atual_id = after.channel.id

                if _voice_conta_tempo(after):
                    estado.segmento_iniciado_em = datetime.now(timezone.utc)
                    estado.afk_mudo_surdo_desde = None
                    estado.afk_canal_referencia_id = None
                    estado.afk_aviso_enviado = False
                else:
                    estado.segmento_iniciado_em = None
                    estado.afk_mudo_surdo_desde = datetime.now(timezone.utc)
                    estado.afk_canal_referencia_id = after.channel.id

                await session.commit()

                await registrar_evento_plantao(
                    membro.guild,
                    membro.id,
                    "TROCOU_CALL",
                    estado.id_fivem,
                    canal_id=canal_anterior_id,
                    detalhes=f"Foi para {after.channel.name}",
                )
                return

            # Caso 4: mesmo canal válido, mudou surdo/mudo → pausa ou retoma moeda
            if (
                estava_em_call_valida
                and esta_em_call_valida
                and not mudou_canal
                and mudou_contagem
            ):
                contava_antes = _voice_conta_tempo(before)
                conta_agora = _voice_conta_tempo(after)

                if contava_antes and not conta_agora:
                    # Virou surdo → pausa cronômetro (não gera moeda)
                    segundos = pausar_cronometro_moeda(
                        estado, motivo="Surdo — pausa moeda"
                    )
                    estado.afk_mudo_surdo_desde = datetime.now(timezone.utc)
                    estado.afk_canal_referencia_id = estado.canal_atual_id
                    estado.afk_aviso_enviado = False
                    await session.commit()
                    await registrar_evento_plantao(
                        membro.guild,
                        membro.id,
                        "MOEDA_PAUSADA",
                        estado.id_fivem,
                        canal_id=estado.canal_atual_id,
                        duracao_segundos=segundos,
                        detalhes="Surdo (ou mudo+surdo) — tempo não conta para moeda",
                    )
                elif not contava_antes and conta_agora:
                    # Tirou o surdo → retoma
                    retomar_cronometro_moeda(estado)
                    estado.afk_mudo_surdo_desde = None
                    estado.afk_canal_referencia_id = None
                    estado.afk_aviso_enviado = False
                    await session.commit()
                    await registrar_evento_plantao(
                        membro.guild,
                        membro.id,
                        "MOEDA_RETOMADA",
                        estado.id_fivem,
                        canal_id=estado.canal_atual_id,
                        detalhes="Não está mais surdo — tempo volta a contar",
                    )
                return


async def setup(bot: commands.Bot):
    """Registra o observador de voz que mantém o estado de plantão atualizado."""
    await bot.add_cog(PlantaoListener(bot))
