# src/gate/evento_gate_services.py
import discord
from datetime import datetime
from sqlalchemy import select, desc

from src.database.connection import async_session
from src.database.models import EventosGate, Presenca, agora


# adicionar em src/gate/evento_gate_services.py
async def buscar_evento_por_id(evento_id: int) -> EventosGate | None:
    async with async_session() as session:
        return await session.get(EventosGate, evento_id)

    
async def criar_evento(
    tipo: str,
    titulo: str,
    data_evento: str,
    horario: str,
    limite_participantes: int,
    adversario: str | None,
    criado_por: int,
    responsavel_id: int,
) -> EventosGate:
    async with async_session() as session:
        evento = EventosGate(
            tipo=tipo,
            titulo=titulo,
            data_evento=data_evento,
            horario=horario,
            limite_participantes=limite_participantes,
            adversario=adversario,
            status="aberto",
            criado_por=criado_por,
            responsavel_id=responsavel_id,
        )
        session.add(evento)
        await session.commit()
        await session.refresh(evento)
        return evento


async def listar_eventos_abertos() -> list[EventosGate]:
    async with async_session() as session:
        result = await session.execute(
            select(EventosGate)
            .where(EventosGate.status == "aberto")
            .order_by(EventosGate.created_at)
        )
        return list(result.scalars().all())


async def encerrar_evento(evento_id: int) -> EventosGate | None:
    async with async_session() as session:
        evento_db = await session.get(EventosGate, evento_id)
        if not evento_db or evento_db.status == "encerrado":
            return None
        evento_db.status = "encerrado"
        evento_db.closed_at = agora()
        await session.commit()
        await session.refresh(evento_db)
        return evento_db


async def salvar_log_message_id(evento_id: int, message_id: int):
    async with async_session() as session:
        evento_db = await session.get(EventosGate, evento_id)
        evento_db.log_message_id = message_id
        await session.commit()


async def confirmar_presenca(evento_id: int, discord_id: int, id_fivem: int) -> Presenca:
    async with async_session() as session:
        presenca = Presenca(
            evento_id=evento_id,
            discord_id=discord_id,
            id_fivem=id_fivem,
            confirmado=True,
        )
        session.add(presenca)
        await session.commit()
        return presenca


async def cancelar_presenca(evento_id: int, discord_id: int) -> bool:
    async with async_session() as session:
        result = await session.execute(
            select(Presenca).where(
                Presenca.evento_id == evento_id,
                Presenca.discord_id == discord_id,
            )
        )
        presenca = result.scalar_one_or_none()
        if not presenca:
            return False
        await session.delete(presenca)
        await session.commit()
        return True


async def listar_presencas(evento_id: int) -> list[Presenca]:
    async with async_session() as session:
        result = await session.execute(
            select(Presenca).where(Presenca.evento_id == evento_id)
        )
        return list(result.scalars().all())