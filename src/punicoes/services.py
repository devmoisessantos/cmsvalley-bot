"""Serviços de aplicar / remover / consultar punições."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from sqlalchemy import select

from src.config import CARGOS_PUNICOES
from src.database.connection import async_session
from src.database.models import Punicao, agora
from src.punicoes.helpers import lista_cargos_punicao_ordenada, parse_links
from src.punicoes.logs import registrar_log_punicao


async def aplicar_punicao(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    id_fivem: str,
    cargo_nome: str,
    cargo_id: int,
    motivo: str,
    links_texto: str | None,
) -> tuple[bool, str, Punicao | None]:
    """Aplica cargo de punição, grava no banco e posta log + tópico de provas."""
    role = guild.get_role(cargo_id)
    if role is None:
        return False, f"❌ Cargo de punição `{cargo_nome}` não encontrado no servidor.", None

    # Remove outros cargos de adv menores/maiores? Mantém acumulativo (adv verbal + adv1 etc)
    # Apenas garante o cargo escolhido.
    try:
        if role not in alvo.roles:
            await alvo.add_roles(role, reason=f"Punição por {executor} — {motivo[:80]}")
    except discord.Forbidden:
        return False, "❌ Sem permissão para adicionar o cargo de punição.", None

    links = parse_links(links_texto)
    links_join = "\n".join(links) if links else (links_texto or None)

    async with async_session() as session:
        reg = Punicao(
            discord_id=alvo.id,
            id_fivem=id_fivem,
            cargo_id=cargo_id,
            cargo_nome=cargo_nome,
            motivo=motivo[:1500],
            links=links_join[:2000] if links_join else None,
            executor_id=executor.id,
            ativa=True,
            criada_em=agora(),
        )
        session.add(reg)
        await session.commit()
        await session.refresh(reg)

    msg_log, thread = await registrar_log_punicao(
        guild=guild,
        alvo=alvo,
        executor=executor,
        id_fivem=id_fivem,
        cargo_role=role,
        motivo=motivo,
        links=links,
        punicao_id=reg.id,
    )

    if msg_log:
        async with async_session() as session:
            r = await session.execute(select(Punicao).where(Punicao.id == reg.id))
            row = r.scalar_one()
            row.channel_id = msg_log.channel.id
            row.message_id = msg_log.id
            if thread:
                row.thread_id = thread.id
            await session.commit()

    return True, f"✅ Punição **{cargo_nome}** aplicada em {alvo.mention}.", reg


async def remover_punicao(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    cargo_id: int | None = None,
    motivo_remocao: str | None = None,
) -> tuple[bool, str]:
    """Remove cargo(s) de punição e marca registros ativos como inativos."""
    nomes_por_id = {rid: nome for nome, rid in CARGOS_PUNICOES.items()}
    removidos = []

    alvos_roles = []
    if cargo_id:
        role = guild.get_role(cargo_id)
        if role and role in alvo.roles:
            alvos_roles.append(role)
    else:
        for _, rid in lista_cargos_punicao_ordenada():
            role = guild.get_role(rid)
            if role and role in alvo.roles:
                alvos_roles.append(role)

    if not alvos_roles:
        return False, "❌ Este membro não possui cargos de punição ativos."

    try:
        await alvo.remove_roles(
            *alvos_roles,
            reason=f"Remoção de punição por {executor} — {motivo_remocao or 'sem motivo'}",
        )
    except discord.Forbidden:
        return False, "❌ Sem permissão para remover os cargos de punição."

    ids = [r.id for r in alvos_roles]
    async with async_session() as session:
        r = await session.execute(
            select(Punicao).where(
                Punicao.discord_id == alvo.id,
                Punicao.ativa.is_(True),
                Punicao.cargo_id.in_(ids),
            )
        )
        for row in r.scalars().all():
            row.ativa = False
            row.removida_em = datetime.now(timezone.utc)
            row.removida_por = executor.id
            row.motivo_remocao = (motivo_remocao or "")[:500]
            removidos.append(row.cargo_nome)
        await session.commit()

    lista = ", ".join(f"**{n}**" for n in removidos) or ", ".join(r.mention for r in alvos_roles)
    return True, f"✅ Punição removida de {alvo.mention}: {lista}"


async def listar_punicoes_membro(discord_id: int, apenas_ativas: bool = False) -> list[Punicao]:
    async with async_session() as session:
        stmt = select(Punicao).where(Punicao.discord_id == discord_id)
        if apenas_ativas:
            stmt = stmt.where(Punicao.ativa.is_(True))
        stmt = stmt.order_by(Punicao.criada_em.desc())
        r = await session.execute(stmt)
        return list(r.scalars().all())
