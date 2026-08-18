# src/utils/permissions.py
"""
Checagens de permissão para comandos de barra (slash commands).
"""

from __future__ import annotations

import discord
from discord import app_commands

from src.config import ADMIN_ROLE_NAMES
from src.utils.mensagens import responder_erro


def esta_autorizado():
    """
    Libera o comando se o membro for Administrador do Discord
    ou tiver um dos cargos listados em ADMIN_ROLE_NAMES.
    """

    async def predicado(interacao: discord.Interaction) -> bool:
        """
        Aplica a regra compartilhada de cargos administrativos antes do comando.

        Esta base de todos os domínios aceita a permissão nativa ou cargos
        configurados e responde no Discord na negativa. O booleano impede que a
        operação protegida tenha qualquer efeito para alguém sem autorização.
        """
        membro = interacao.user

        if membro.guild_permissions.administrator:
            return True

        nomes_dos_cargos_do_membro = {cargo.name for cargo in membro.roles}
        tem_cargo_autorizado = bool(
            nomes_dos_cargos_do_membro.intersection(ADMIN_ROLE_NAMES)
        )

        if tem_cargo_autorizado:
            return True

        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=[
                "Você não tem permissão para usar este comando.",
                "É necessário ser Administrador ou ter um dos cargos autorizados.",
            ],
        )
        return False

    return app_commands.check(predicado)


# Nome antigo mantido para não quebrar imports existentes.
# Em código novo, use esta_autorizado.
is_authorized = esta_autorizado


def apenas_administrador():
    """Libera o comando somente para quem tem a permissão Administrator."""

    async def predicado(interacao: discord.Interaction) -> bool:
        """
        Exige a permissão nativa de administrador antes de executar o comando.

        A verificação é reutilizada por todos os domínios e envia uma explicação
        centralizada quando falha. Retornar `False` bloqueia o callback antes que
        ações administrativas sejam iniciadas.
        """
        membro = interacao.user

        if membro.guild_permissions.administrator:
            return True

        await responder_erro(
            interacao,
            titulo="Somente administradores",
            linhas=[
                "Este comando é restrito a **Administradores** do servidor.",
            ],
        )
        return False

    return app_commands.check(predicado)


def membro_tem_cargo(membro: discord.Member, nome_do_cargo: str) -> bool:
    """
    Diz se o membro tem um cargo com aquele nome exato.

    Use quando a checagem for de um unico cargo. Compara pelo nome porque e
    assim que os cargos aparecem no dicionario CARGOS do config.
    """
    for cargo_do_membro in membro.roles:
        if cargo_do_membro.name == nome_do_cargo:
            return True

    return False


def membro_tem_algum_cargo(
    membro: discord.Member,
    nomes_dos_cargos: list[str] | set[str] | tuple[str, ...],
) -> bool:
    """
    Diz se o membro tem pelo menos um dos cargos da lista.

    Use quando varios cargos diferentes liberam a mesma acao.
    """
    nomes_procurados = set(nomes_dos_cargos)

    for cargo_do_membro in membro.roles:
        if cargo_do_membro.name in nomes_procurados:
            return True

    return False


def membro_tem_cargo_por_id(membro: discord.Member, id_do_cargo: int) -> bool:
    """
    Diz se o membro tem o cargo daquele ID.

    Mais seguro que comparar por nome quando o cargo pode ser renomeado no
    Discord: o ID nunca muda.
    """
    for cargo_do_membro in membro.roles:
        if cargo_do_membro.id == id_do_cargo:
            return True

    return False


def membro_e_administrador(membro: discord.Member) -> bool:
    """
    Diz se o membro e administrador do servidor ou tem cargo de administracao.

    E a mesma regra usada pelo decorador `esta_autorizado`, mas em forma de
    funcao simples, para quando a checagem precisa acontecer no meio do codigo
    e nao na entrada de um comando.
    """
    if membro.guild_permissions.administrator:
        return True

    return membro_tem_algum_cargo(membro, ADMIN_ROLE_NAMES)
