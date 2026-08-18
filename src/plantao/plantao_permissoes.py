"""Validação de permissões do sistema de plantão / chamada / admin."""

from __future__ import annotations

import discord

from src.config import (
    CARGOS,
    CARGOS_DIRETORIA,
    CARGOS_DOUTOR_OU_ACIMA,
)


def _ids_cargos(nomes: list[str]) -> set[int]:
    return {CARGOS[numero] for numero in nomes if numero in CARGOS}


def membro_tem_cargo(membro: discord.Member, nomes: list[str]) -> bool:
    """
    Aceita administradores e compara cargos pelos IDs configurados, não pelos nomes.
    """
    if membro.guild_permissions.administrator:
        return True
    ids = _ids_cargos(nomes)
    return any(cargo.id in ids for cargo in membro.roles)


def e_diretoria(membro: discord.Member) -> bool:
    """Diretoria++ — acesso a #gerenciar-membros e ações admin."""
    return membro_tem_cargo(membro, CARGOS_DIRETORIA)


def e_doutor_ou_acima(membro: discord.Member) -> bool:
    """Pode iniciar chamada em #fazer-chamada."""
    return membro_tem_cargo(membro, CARGOS_DOUTOR_OU_ACIMA)


def mensagem_sem_permissao(contexto: str = "esta ação") -> str:
    """Produz um aviso consistente, permitindo informar qual ação foi bloqueada."""
    return f"❌ Você não tem permissão para {contexto}."
