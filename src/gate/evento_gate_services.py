# src/gate/evento_gate_services.py
from datetime import datetime

import discord
from sqlalchemy import select

from src.config import CARGOS_CRIACAO_EVENTO_GATE
from src.database.connection import async_session
from src.database.models import (
    EventosGate,
    Presenca,
    agora,
)


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


async def confirmar_presenca(
    evento_id: int, discord_id: int, id_fivem: int
) -> Presenca:
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


# ---------------------------------------------------------------------------
# VALIDAÇÃO DOS CAMPOS DO MODAL
# ---------------------------------------------------------------------------


def validar_data(valor: str) -> tuple[bool, str]:
    """Aceita apenas DD/MM/AAAA, com data real
    (rejeita 32/13/2026, 29/02 em ano não bissexto, etc).
    Retorna (valido, mensagem_de_erro)."""
    valor = valor.strip()
    try:
        data = datetime.strptime(valor, "%d/%m/%Y")
    except ValueError:
        return False, "❌ Data inválida. Use o formato `DD/MM/AAAA` (ex: `15/06/2026`)."

    hoje = datetime.now().date()
    if data.date() < hoje:
        return False, f"❌ A data `{valor}` já passou. Informe uma data futura."

    return True, ""


def validar_horario(valor: str) -> tuple[bool, str]:
    """Aceita apenas HH:MM em formato 24h (rejeita 25:99, 9h30, etc)."""
    valor = valor.strip()
    try:
        datetime.strptime(valor, "%H:%M")
    except ValueError:
        return False, "❌ Horário inválido. Use o formato `HH:MM`, 24h (ex: `20:00`)."

    return True, ""


def validar_limite(valor: str) -> tuple[bool, str, int]:
    """0 = sem limite. Retorna (valido, mensagem_de_erro, valor_convertido)."""
    valor = (valor or "0").strip()
    if not valor.isdigit():
        return False, "❌ Limite precisa ser um número inteiro (0 = sem limite).", 0

    limite_int = int(valor)
    if limite_int < 0:
        return False, "❌ Limite não pode ser negativo.", 0
    if limite_int > 25:
        return False, "❌ Limite muito alto (máximo 25). Use `0` para sem limite.", 0

    return True, "", limite_int


def validar_adversario(valor: str | None) -> tuple[bool, str]:
    """Só chamado pro ModalFacXFac — garante que não veio só espaços em branco."""
    if valor is None or not valor.strip():
        return False, "❌ O nome do adversário não pode ficar em branco."
    return True, ""


def tem_permissao_gate(member: discord.Member) -> bool:
    return any(role.name in CARGOS_CRIACAO_EVENTO_GATE for role in member.roles)
