# src/backup/member_snapshot.py
"""
Snapshot vivo dos cargos de cada membro.

Duas responsabilidades:

1. Guardar no banco o último estado conhecido (cargos + apelido)
   sempre que alguém muda de cargo, de nick, ou sai do servidor.

2. Quando a pessoa VOLTA (on_member_join), reaplicar esses cargos
   — filtrando cargos perigosos (Administrator, Manage Guild, etc.)
   e cargos acima do bot na hierarquia.

Isso é independente do backup JSON periódico.
"""

from __future__ import annotations

import json

import discord
from sqlalchemy import select

from src.config import CARGOS
from src.database.conexao import async_session
from src.database.models import (
    SnapshotCargosMembro,
    agora,
)
from src.utils.error_handling import ignorar_falha_cosmetica

# Permissões que NUNCA devolvemos automaticamente no rejoin
PERMISSOES_PERIGOSAS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "ban_members",
    "kick_members",
    "mention_everyone",
)

# Liga/desliga o restore automático por servidor (em memória; padrão = ligado)
_rejoin_por_servidor: dict[int, bool] = {}


def rejoin_esta_ativo(guild_id: int) -> bool:
    """Retorna se o rejoin automático está ligado neste servidor."""
    return _rejoin_por_servidor.get(guild_id, True)


def definir_rejoin(guild_id: int, ativo: bool) -> None:
    """Liga ou desliga o rejoin automático neste servidor."""
    _rejoin_por_servidor[guild_id] = ativo


def _serializar_ids(ids: list[int]) -> str:
    return json.dumps(ids)


def _serializar_nomes(nomes: list[str]) -> str:
    return json.dumps(nomes, ensure_ascii=False)


def _desserializar_ids(texto: str | None) -> list[int]:
    if not texto:
        return []
    try:
        return [int(valor) for valor in json.loads(texto)]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _desserializar_nomes(texto: str | None) -> list[str]:
    if not texto:
        return []
    try:
        return [str(valor) for valor in json.loads(texto)]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _cargo_tem_permissao_perigosa(cargo: discord.Role) -> bool:
    """True se o cargo tem alguma permissão que não devemos devolver no rejoin."""
    for nome_da_permissao in PERMISSOES_PERIGOSAS:
        if getattr(cargo.permissions, nome_da_permissao, False):
            return True
    return False


def _cargos_seguros_do_membro(membro: discord.Member) -> list[discord.Role]:
    """Cargos que faz sentido guardar e devolver no rejoin."""
    cargos_seguros: list[discord.Role] = []

    for cargo in membro.roles:
        if cargo.is_default() or cargo.managed:
            continue
        if _cargo_tem_permissao_perigosa(cargo):
            continue
        cargos_seguros.append(cargo)

    return cargos_seguros


async def salvar_snapshot_membro(membro: discord.Member) -> None:
    """Grava (ou atualiza) o snapshot do membro no banco."""
    if membro.bot:
        return

    cargos_seguros = _cargos_seguros_do_membro(membro)
    ids_dos_cargos = [cargo.id for cargo in cargos_seguros]
    nomes_dos_cargos = [cargo.name for cargo in cargos_seguros]

    async with async_session() as sessao:
        resultado_consulta = await sessao.execute(
            select(SnapshotCargosMembro).where(
                SnapshotCargosMembro.discord_id == membro.id
            )
        )
        registro = resultado_consulta.scalar_one_or_none()

        if registro is None:
            registro = SnapshotCargosMembro(
                discord_id=membro.id,
                guild_id=membro.guild.id,
                role_ids=_serializar_ids(ids_dos_cargos),
                role_names=_serializar_nomes(nomes_dos_cargos),
                nickname=membro.nick,
                atualizado_em=agora(),
            )
            sessao.add(registro)
        else:
            registro.guild_id = membro.guild.id
            registro.role_ids = _serializar_ids(ids_dos_cargos)
            registro.role_names = _serializar_nomes(nomes_dos_cargos)
            registro.nickname = membro.nick
            registro.atualizado_em = agora()

        await sessao.commit()


async def carregar_snapshot(discord_id: int) -> SnapshotCargosMembro | None:
    """Busca o snapshot salvo deste usuário no banco."""
    async with async_session() as sessao:
        resultado_consulta = await sessao.execute(
            select(SnapshotCargosMembro).where(
                SnapshotCargosMembro.discord_id == discord_id
            )
        )
        return resultado_consulta.scalar_one_or_none()


async def sincronizar_todos_os_membros(guilda: discord.Guild) -> int:
    """
    Percorre o cache de membros e grava snapshot de cada um.

    Retorna quantos membros foram salvos.
    """
    quantidade_salvos = 0

    for membro in guilda.members:
        if membro.bot:
            continue
        await salvar_snapshot_membro(membro)
        quantidade_salvos += 1

    return quantidade_salvos


def _cargo_esta_abaixo_do_bot(
    cargo: discord.Role,
    cargo_mais_alto_do_bot: discord.Role | None,
) -> bool:
    """O bot só consegue atribuir cargos abaixo do dele na hierarquia."""
    if cargo_mais_alto_do_bot is None:
        return False
    return cargo.position < cargo_mais_alto_do_bot.position


async def restaurar_cargos_no_rejoin(membro: discord.Member) -> list[str]:
    """
    Chamado no on_member_join.

    Devolve uma lista de mensagens para o log do backup.
    """
    relatorio: list[str] = []

    if not rejoin_esta_ativo(membro.guild.id):
        relatorio.append("Rejoin automático desligado neste servidor.")
        return relatorio

    if membro.bot:
        return relatorio

    snapshot = await carregar_snapshot(membro.id)
    if snapshot is None:
        relatorio.append(f"Sem snapshot salvo para {membro} (`{membro.id}`).")
        return relatorio

    ids_salvos = _desserializar_ids(snapshot.role_ids)
    nomes_salvos = _desserializar_nomes(snapshot.role_names)

    cargos_por_id = {cargo.id: cargo for cargo in membro.guild.roles}
    cargos_por_nome = {
        cargo.name: cargo for cargo in membro.guild.roles if not cargo.is_default()
    }

    cargo_mais_alto_do_bot = membro.guild.me.top_role if membro.guild.me else None
    cargos_para_dar: list[discord.Role] = []

    for id_do_cargo in ids_salvos:
        cargo = cargos_por_id.get(id_do_cargo)
        if cargo is None:
            continue
        if cargo.managed or cargo.is_default():
            continue
        if _cargo_tem_permissao_perigosa(cargo):
            continue
        if not _cargo_esta_abaixo_do_bot(cargo, cargo_mais_alto_do_bot):
            relatorio.append(
                f"⚠️ Cargo `{cargo.name}` ignorado "
                "(acima ou igual ao bot na hierarquia)."
            )
            continue
        if cargo not in cargos_para_dar:
            cargos_para_dar.append(cargo)

    # Fallback por nome quando o id do cargo não existe mais no servidor
    for nome_do_cargo in nomes_salvos:
        cargo = cargos_por_nome.get(nome_do_cargo)
        if cargo is None or cargo in cargos_para_dar:
            continue
        if cargo.managed or cargo.is_default():
            continue
        if _cargo_tem_permissao_perigosa(cargo):
            continue
        if not _cargo_esta_abaixo_do_bot(cargo, cargo_mais_alto_do_bot):
            continue
        cargos_para_dar.append(cargo)

    # Recrutamento incompleto / cancelado: no rejoin só Visitantes + ESTUDANTE
    # (nunca devolver PROVA / Aprovado / cargos de avaliação)
    try:
        from src.recrutamento.recrutamento_service import candidato_tem_fluxo_incompleto

        fluxo_incompleto = await candidato_tem_fluxo_incompleto(membro.id)
    except Exception:
        fluxo_incompleto = False

    if fluxo_incompleto:
        ids_permitidos = {
            CARGOS.get("Visitantes"),
            CARGOS.get("ESTUDANTE"),
        }
        ids_permitidos.discard(None)
        filtrados = [
            cargo_a_restaurar
            for cargo_a_restaurar in cargos_para_dar
            if cargo_a_restaurar.id in ids_permitidos
        ]
        removidos = [
            cargo_a_restaurar
            for cargo_a_restaurar in cargos_para_dar
            if cargo_a_restaurar.id not in ids_permitidos
        ]
        if removidos:
            nomes = ", ".join(
                f"`{cargo_a_restaurar.name}`" for cargo_a_restaurar in removidos
            )
            relatorio.append(
                f"⚠️ Recrutamento incompleto/cancelado — cargos não restaurados: {nomes}"
            )
        cargos_para_dar = filtrados
        relatorio.append(
            "ℹ️ Rejoin limitado a Visitantes + ESTUDANTE (fluxo de recrutamento "
            "incompleto)."
        )

    if cargos_para_dar:
        try:
            await membro.add_roles(
                *cargos_para_dar,
                reason="Restore automático de cargos (rejoin)",
            )
            nomes_formatados = ", ".join(f"`{cargo.name}`" for cargo in cargos_para_dar)
            relatorio.append(f"✅ Cargos reaplicados em {membro}: {nomes_formatados}")
        except discord.Forbidden:
            relatorio.append(f"❌ Sem permissão para dar cargos a {membro}.")
        except discord.HTTPException as erro:
            relatorio.append(f"❌ Erro ao dar cargos a {membro}: {erro}")
    else:
        relatorio.append(f"Nenhum cargo seguro para reaplicar em {membro}.")

    if snapshot.nickname and snapshot.nickname != membro.nick:
        try:
            await membro.edit(
                nick=snapshot.nickname,
                reason="Restore automático de apelido (rejoin)",
            )
            relatorio.append(f"✅ Apelido restaurado: `{snapshot.nickname}`")
        except discord.Forbidden:
            relatorio.append(f"⚠️ Sem permissão para alterar apelido de {membro}.")
        except discord.HTTPException as erro_em_restaurar_cargos_no_rejoin:
            # Enfeite que falhou: devolver os cargos de quem voltou.
            # A acao principal ja tinha dado certo, entao so registro.
            ignorar_falha_cosmetica(
                erro_em_restaurar_cargos_no_rejoin,
                o_que_falhou="devolver os cargos de quem voltou",
            )

    return relatorio
