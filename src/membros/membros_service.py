"""Consultas e formatação da ficha de membros (domínio membros).

Toda lógica de banco de gerenciar-membros fica aqui.
O painel só monta interface e chama estas funções.
"""

from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import discord
from sqlalchemy import (
    func,
    select,
)

from src.database.connection import async_session
from src.database.models import (
    Chamada,
    EstadoPlantao,
    FaltaChamada,
    LogPlantao,
    Recrutamento,
    Usuario,
)


async def resolver_id_fivem_do_membro(discord_id: int) -> str | None:
    """Prioridade: EstadoPlantao → Usuario → último Recrutamento APROVADO."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao.id_fivem).where(
                EstadoPlantao.discord_id == discord_id,
                EstadoPlantao.id_fivem.is_not(None),
            )
        )
        valor = resultado.scalar_one_or_none()
        if valor:
            return str(valor)

        resultado = await sessao.execute(
            select(Usuario.id_fivem).where(
                Usuario.discord_id == discord_id,
                Usuario.id_fivem.is_not(None),
            )
        )
        valor = resultado.scalar_one_or_none()
        if valor:
            return str(valor)

        resultado = await sessao.execute(
            select(Recrutamento.id_fivem)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.status == "APROVADO",
                Recrutamento.id_fivem.is_not(None),
            )
            .order_by(Recrutamento.data_fim.desc().nullslast(), Recrutamento.id.desc())
            .limit(1)
        )
        valor = resultado.scalar_one_or_none()
        return str(valor) if valor else None


async def buscar_estado_plantao(discord_id: int) -> EstadoPlantao | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        return resultado.scalar_one_or_none()


async def buscar_usuario(discord_id: int) -> Usuario | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario).where(Usuario.discord_id == discord_id)
        )
        return resultado.scalar_one_or_none()


async def buscar_recrutamento_como_candidato(discord_id: int) -> Recrutamento | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Recrutamento)
            .where(Recrutamento.discord_id_candidato == discord_id)
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def estatisticas_como_recrutador(
    discord_id: int,
) -> tuple[int, int, list[Recrutamento]]:
    """Retorna (total APROVADO, total última semana, últimos 5 APROVADO)."""
    agora = datetime.now(timezone.utc)
    semana = agora - timedelta(days=7)
    async with async_session() as sessao:
        total = await sessao.execute(
            select(func.count())
            .select_from(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
            )
        )
        total_n = int(total.scalar_one() or 0)

        sem = await sessao.execute(
            select(func.count())
            .select_from(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
                Recrutamento.data_fim.is_not(None),
                Recrutamento.data_fim >= semana,
            )
        )
        sem_n = int(sem.scalar_one() or 0)

        ultimos = await sessao.execute(
            select(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
            )
            .order_by(Recrutamento.data_fim.desc().nullslast(), Recrutamento.id.desc())
            .limit(5)
        )
        lista = list(ultimos.scalars().all())
    return total_n, sem_n, lista


async def estatisticas_chamadas(discord_id: int) -> tuple[int, int]:
    """(faltas, chamadas_como_doutor)."""
    async with async_session() as sessao:
        faltas = await sessao.execute(
            select(func.count())
            .select_from(FaltaChamada)
            .where(FaltaChamada.discord_id == discord_id)
        )
        faltas_n = int(faltas.scalar_one() or 0)

        como_doutor = await sessao.execute(
            select(func.count())
            .select_from(Chamada)
            .where(Chamada.doutor_id == discord_id)
        )
        doutor_n = int(como_doutor.scalar_one() or 0)

    return faltas_n, doutor_n


async def tempo_total_segundos_plantao(discord_id: int) -> int:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.coalesce(func.sum(LogPlantao.duracao_segundos), 0)).where(
                LogPlantao.discord_id == discord_id,
                LogPlantao.duracao_segundos.is_not(None),
            )
        )
        return int(resultado.scalar_one() or 0)


async def ultimos_logs_plantao(discord_id: int, limite: int = 6) -> list[LogPlantao]:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(LogPlantao)
            .where(LogPlantao.discord_id == discord_id)
            .order_by(LogPlantao.criado_em.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


def formatar_cargos_do_membro(membro: discord.Member) -> str:
    cargos = [
        cargo
        for cargo in sorted(membro.roles, key=lambda x: x.position, reverse=True)
        if cargo.name != "@everyone"
    ]
    if not cargos:
        return "_Nenhum cargo._"
    mencoes = [cargo.mention for cargo in cargos]
    linhas = []
    for i in range(0, len(mencoes), 3):
        linhas.append(" · ".join(mencoes[i : i + 3]))
    return "\n".join(linhas)


def formatar_timestamp(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:d>"
