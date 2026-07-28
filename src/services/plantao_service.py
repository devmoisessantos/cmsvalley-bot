import discord
from datetime import datetime

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

        canal_atual = _membro_esta_em_call_valida(membro)
        if canal_atual is not None:
            estado.em_call_valida = True
            estado.call_entrada_em = datetime.utcnow()
            estado.canal_atual_id = canal_atual.id

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
        await session.commit()

    return "✅ Você saiu de serviço. Cronômetro encerrado."


async def _finalizar_periodo_em_call(estado: EstadoPlantao):
    """Calcula o tempo decorrido desde que entrou na call, soma ao acumulado, e credita moedas se atingir o limite.
    Não faz commit — quem chama essa função é responsável por commitar a sessão."""
    if estado.call_entrada_em is None:
        return

    decorrido = (datetime.utcnow() - estado.call_entrada_em).total_seconds()
    estado.segundos_acumulados += int(decorrido)

    while estado.segundos_acumulados >= SEGUNDOS_PARA_MOEDA:
        estado.segundos_acumulados -= SEGUNDOS_PARA_MOEDA
        estado.saldo_moedas += 1

    estado.em_call_valida = False
    estado.call_entrada_em = None
    estado.canal_atual_id = None