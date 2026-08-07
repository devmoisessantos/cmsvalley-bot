"""Serviços de aplicar / remover / consultar punições."""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select

from src.database.connection import async_session
from src.database.models import (
    Punicao,
    agora,
)
from src.punicoes.helpers import parse_links
from src.punicoes.logs import (
    registrar_advertencia,
    registrar_log_advertencia,
    registrar_log_remocao,
)


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
    """Aplica cargo, grava no banco, posta em CANAL_ADVERTENCIAS + LOG_PUNICOES."""
    role = guild.get_role(cargo_id)
    if role is None:
        return (
            False,
            f"❌ Cargo de punição `{cargo_nome}` não encontrado no servidor.",
            None,
        )

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

    # 1) Registro público (CANAL_ADVERTENCIAS) + tópico de provas + DM
    msg_adv, thread = await registrar_advertencia(
        guild=guild,
        alvo=alvo,
        executor=executor,
        id_fivem=id_fivem,
        cargo_role=role,
        motivo=motivo,
        links=links,
        punicao_id=reg.id,
    )

    if msg_adv:
        async with async_session() as session:
            r = await session.execute(select(Punicao).where(Punicao.id == reg.id))
            row = r.scalar_one()
            row.channel_id = msg_adv.channel.id
            row.message_id = msg_adv.id
            if thread:
                row.thread_id = thread.id
            await session.commit()

    # 2) Log interno (LOG_PUNICOES)
    await registrar_log_advertencia(
        guild=guild,
        alvo=alvo,
        executor=executor,
        id_fivem=id_fivem,
        cargo_role=role,
        motivo=motivo,
        punicao_id=reg.id,
        msg_advertencia=msg_adv,
    )

    return True, f"✅ Punição **{cargo_nome}** aplicada em {alvo.mention}.", reg


async def remover_punicao(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    cargo_id: int | None = None,
    punicao_id: int | None = None,
    motivo_remocao: str | None = None,
) -> tuple[bool, str]:
    """Remove cargo(s) de punição, marca registros inativos e loga em LOG_PUNICOES."""
    removidos: list[str] = []
    punicao_ids: list[int] = []
    id_fivem: str | None = None
    roles_a_remover: list[discord.Role] = []

    async with async_session() as session:
        if punicao_id is not None:
            r = await session.execute(
                select(Punicao).where(
                    Punicao.id == punicao_id,
                    Punicao.discord_id == alvo.id,
                    Punicao.ativa.is_(True),
                )
            )
            rows = list(r.scalars().all())
        elif cargo_id is not None:
            r = await session.execute(
                select(Punicao).where(
                    Punicao.discord_id == alvo.id,
                    Punicao.ativa.is_(True),
                    Punicao.cargo_id == cargo_id,
                )
            )
            rows = list(r.scalars().all())
        else:
            r = await session.execute(
                select(Punicao).where(
                    Punicao.discord_id == alvo.id,
                    Punicao.ativa.is_(True),
                )
            )
            rows = list(r.scalars().all())

        if not rows:
            if cargo_id:
                role = guild.get_role(cargo_id)
                if role and role in alvo.roles:
                    try:
                        await alvo.remove_roles(
                            role,
                            reason=(
                                f"Remoção de punição por {executor} — "
                                f"{motivo_remocao or 'sem motivo'}"
                            ),
                        )
                    except discord.Forbidden:
                        return (
                            False,
                            "❌ Sem permissão para remover os cargos de punição.",
                        )
                    await registrar_log_remocao(
                        guild=guild,
                        alvo=alvo,
                        executor=executor,
                        cargos_removidos=[role.name],
                        motivo_remocao=motivo_remocao,
                    )
                    return (
                        True,
                        f"✅ Cargo de punição removido de {alvo.mention}: {role.mention}",
                    )
            return False, "❌ Este membro não possui punições ativas registradas."

        cargo_ids_marcados: set[int] = set()
        for row in rows:
            row.ativa = False
            row.removida_em = datetime.now(timezone.utc)
            row.removida_por = executor.id
            row.motivo_remocao = (motivo_remocao or "")[:500]
            removidos.append(row.cargo_nome)
            punicao_ids.append(row.id)
            if row.id_fivem and not id_fivem:
                id_fivem = row.id_fivem
            cargo_ids_marcados.add(row.cargo_id)

        await session.commit()

        for cid in cargo_ids_marcados:
            r2 = await session.execute(
                select(Punicao).where(
                    Punicao.discord_id == alvo.id,
                    Punicao.ativa.is_(True),
                    Punicao.cargo_id == cid,
                )
            )
            if r2.scalar_one_or_none() is None:
                role = guild.get_role(cid)
                if role and role in alvo.roles:
                    roles_a_remover.append(role)

    if roles_a_remover:
        try:
            await alvo.remove_roles(
                *roles_a_remover,
                reason=(
                    f"Remoção de punição por {executor} — "
                    f"{motivo_remocao or 'sem motivo'}"
                ),
            )
        except discord.Forbidden:
            return False, "❌ Sem permissão para remover os cargos de punição."

    await registrar_log_remocao(
        guild=guild,
        alvo=alvo,
        executor=executor,
        cargos_removidos=removidos,
        motivo_remocao=motivo_remocao,
        punicao_ids=punicao_ids,
        id_fivem=id_fivem,
    )

    lista = ", ".join(f"**{n.strip()}**" for n in removidos)
    return True, f"✅ Punição removida de {alvo.mention}: {lista}"


async def listar_punicoes_membro(
    discord_id: int, apenas_ativas: bool = False
) -> list[Punicao]:
    async with async_session() as session:
        stmt = select(Punicao).where(Punicao.discord_id == discord_id)
        if apenas_ativas:
            stmt = stmt.where(Punicao.ativa.is_(True))
        stmt = stmt.order_by(Punicao.criada_em.desc())
        r = await session.execute(stmt)
        return list(r.scalars().all())
