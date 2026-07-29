import discord
from datetime import datetime, timezone

from src.config import SEGUNDOS_PARA_MOEDA
from src.config import obter_todos_ids_canais_plantao
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from sqlalchemy import select


async def _obter_ou_criar_estado(session, discord_id: int) -> EstadoPlantao:
    resultado = await session.execute(
        select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
    )
    estado = resultado.scalar_one_or_none()

    if estado is None:
        estado = EstadoPlantao(discord_id=discord_id)
        session.add(estado)

    return estado


def _membro_esta_em_call_valida(membro: discord.Member) -> discord.VoiceChannel | None:
    """Se o membro está atualmente numa call configurada como válida, retorna o canal. Senão, None."""
    if membro.voice is None or membro.voice.channel is None:
        return None

    if membro.voice.channel.id in obter_todos_ids_canais_plantao():
        return membro.voice.channel

    return None


async def ligar_servico(membro: discord.Member) -> str:
    """Liga o toggle do médico. Se ele já estiver numa call válida, a contagem já começa."""
    async with async_session() as session:
        estado = await _obter_ou_criar_estado(session, membro.id)

        if estado.toggle_ligado:
            await session.commit()
            return "❌ Você já está em serviço."

        estado.toggle_ligado = True
        estado.lembrete_1_enviado = False
        estado.lembrete_2_enviado = False

        canal_atual = _membro_esta_em_call_valida(membro)
        if canal_atual is not None:
            estado.em_call_valida = True
            estado.call_entrada_em = datetime.now(timezone.utc)
            estado.canal_atual_id = canal_atual.id
            estado.ocioso_desde = None  # já entrou contando, não está ocioso
        else:
            estado.ocioso_desde = datetime.now(timezone.utc)  # ligou mas ainda fora de call

        await session.commit()

    return "✅ Você entrou em serviço! Conecte-se a uma das calls disponíveis para começar a contar tempo."

async def desligar_servico(membro: discord.Member) -> str:
    """Desliga o toggle, encerrando qualquer contagem de tempo em andamento."""
    async with async_session() as session:
        estado = await _obter_ou_criar_estado(session, membro.id)

        if not estado.toggle_ligado:
            await session.commit()
            return "❌ Você não está em serviço."

        if estado.em_call_valida:
            await _finalizar_periodo_em_call(estado)

        estado.toggle_ligado = False
        estado.ocioso_desde = None
        estado.lembrete_1_enviado = False
        estado.lembrete_2_enviado = False
        await session.commit()

    return "✅ Você saiu de serviço. Cronômetro encerrado."


def garantir_aware(dt: datetime) -> datetime:
    """Se o datetime veio sem timezone (naive), assume que já era UTC e anexa isso."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _finalizar_periodo_em_call(estado: EstadoPlantao):
    if estado.call_entrada_em is None:
        return

    entrada = garantir_aware(estado.call_entrada_em)
    decorrido = (datetime.now(timezone.utc) - entrada).total_seconds()
    estado.segundos_acumulados += int(decorrido)

    while estado.segundos_acumulados >= SEGUNDOS_PARA_MOEDA:
        estado.segundos_acumulados -= SEGUNDOS_PARA_MOEDA
        estado.saldo_moedas += 1

    estado.em_call_valida = False
    estado.call_entrada_em = None
    estado.canal_atual_id = None

    # Ao sair da call, se o toggle continua ligado, começa a contar ociosidade de novo
    estado.ocioso_desde = datetime.now(timezone.utc)
    estado.lembrete_1_enviado = False
    estado.lembrete_2_enviado = False