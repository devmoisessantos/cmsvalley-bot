"""Helpers do sistema de punições."""

from __future__ import annotations

import re

import discord

from src.config import (
    CARGOS,
    CARGOS_DIRETORIA,
    CARGOS_PUNICOES,
)


def e_staff_punicao(membro: discord.Member) -> bool:
    """Quem pode aplicar / remover / consultar punições."""
    if membro.guild_permissions.administrator or membro.guild_permissions.manage_roles:
        return True
    ids = {CARGOS[n] for n in CARGOS_DIRETORIA if n in CARGOS}
    return any(r.id in ids for r in membro.roles)


def mensagem_sem_permissao() -> str:
    return "❌ Você não tem permissão para usar o painel de punições."


def lista_cargos_punicao_ordenada() -> list[tuple[str, int]]:
    """Ordem de escalonamento: verbal → adv1 → adv2 → adv3 → exonerado."""
    return [(nome, rid) for nome, rid in CARGOS_PUNICOES.items()]


def cargo_punicao_atual(membro: discord.Member) -> tuple[str, int] | None:
    """Retorna o cargo de punição ativo de maior grau (último da lista presente)."""
    atual = None
    for nome, rid in lista_cargos_punicao_ordenada():
        if any(r.id == rid for r in membro.roles):
            atual = (nome, rid)
    return atual


def proximo_cargo_punicao(membro: discord.Member) -> tuple[str, int] | None:
    """Próximo nível de advertência a aplicar (primeiro que o membro ainda não tem)."""
    for nome, rid in lista_cargos_punicao_ordenada():
        if not any(r.id == rid for r in membro.roles):
            return nome, rid
    return None


def parse_links(texto: str | None) -> list[str]:
    if not texto:
        return []
    urls = re.findall(r"https?://\S+", texto)
    return urls[:15]


async def resolver_id_fivem(discord_id: int) -> str | None:
    from sqlalchemy import select

    from src.database.connection import async_session
    from src.database.models import (
        EstadoPlantao,
        Recrutamento,
        Usuario,
    )

    async with async_session() as session:
        for model, col in (
            (EstadoPlantao, EstadoPlantao.id_fivem),
            (Usuario, Usuario.id_fivem),
        ):
            r = await session.execute(
                select(col).where(
                    model.discord_id == discord_id,
                    col.is_not(None),
                )
            )
            v = r.scalar_one_or_none()
            if v:
                return str(v)

        r = await session.execute(
            select(Recrutamento.id_fivem)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.status == "APROVADO",
                Recrutamento.id_fivem.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        v = r.scalar_one_or_none()
        return str(v) if v else None
