# src/ausencia/ausencia_service.py
"""Regras de solicitação e aprovação de ausência / afastamento."""

from __future__ import annotations

import json
from datetime import (
    datetime,
    timedelta,
    timezone,
)

import discord
from sqlalchemy import select

from src.config import (
    CARGOS,
    CARGOS_DIRETORIA,
    CARGOS_HIERARQUIA,
)
from src.database.connection import async_session
from src.database.models import (
    SolicitacaoAusencia,
    Usuario,
)

TIPOS_AUSENCIA = {
    "viagem_ferias": "🟡 Viagem / Férias",
    "motivos_pessoais": "🔵 Motivos Pessoais",
    "emergencia": "🔴 Emergência",
}

PERIODOS_AUSENCIA = {
    "3": ("3 Dias", 3),
    "7": ("7 Dias", 7),
    "15": ("15 Dias", 15),
    "30plus": ("30+ Dias", 30),
}

# Cargos que permanecem no membro durante a ausência
CARGOS_MANTER_AUSENCIA = (
    "🚫 Ausente",
    "HP S・Valley",
    "Aprovado",
)


def cargo_atual_hierarquia(membro: discord.Member) -> str:
    nomes = {cargo.name for cargo in membro.roles}
    for nome in CARGOS_HIERARQUIA:
        if nome in nomes:
            return nome
    return "—"


def membro_e_diretoria(membro: discord.Member) -> bool:
    nomes = {cargo.name for cargo in membro.roles}
    return bool(nomes.intersection(set(CARGOS_DIRETORIA)))


def membro_pode_solicitar_ausencia(membro: discord.Member) -> bool:
    """Quem tem HP S・Valley ou cargo da hierarquia (não só Visitante)."""
    nomes = {cargo.name for cargo in membro.roles}
    if "HP S・Valley" in nomes or "Aprovado" in nomes:
        return True
    return bool(nomes.intersection(set(CARGOS_HIERARQUIA)))


def calcular_datas_periodo(
    periodo_chave: str,
    data_inicio: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Retorna (inicio, fim) em UTC a partir da chave de período."""
    inicio = data_inicio or datetime.now(timezone.utc)
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=timezone.utc)
    rotulo, dias = PERIODOS_AUSENCIA.get(periodo_chave, ("3 Dias", 3))
    fim = inicio + timedelta(days=dias)
    return inicio, fim


async def obter_id_fivem(discord_id: int) -> str | None:
    async with async_session() as sessao:
        usuario = await sessao.get(Usuario, discord_id)
        if usuario is None:
            return None
        return usuario.id_fivem


async def obter_pedido_pendente(discord_id: int) -> SolicitacaoAusencia | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoAusencia)
            .where(
                SolicitacaoAusencia.discord_id == discord_id,
                SolicitacaoAusencia.status == "pendente",
            )
            .order_by(SolicitacaoAusencia.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def obter_ausencia_ativa(discord_id: int) -> SolicitacaoAusencia | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoAusencia)
            .where(
                SolicitacaoAusencia.discord_id == discord_id,
                SolicitacaoAusencia.status == "aprovada",
            )
            .order_by(SolicitacaoAusencia.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


def serializar_cargos(membro: discord.Member) -> tuple[str, str]:
    ids = [r.id for r in membro.roles if r != membro.guild.default_role]
    nomes = [r.name for r in membro.roles if r != membro.guild.default_role]
    return json.dumps(ids), json.dumps(nomes, ensure_ascii=False)


async def criar_solicitacao(
    *,
    membro: discord.Member,
    tipo: str,
    periodo_chave: str,
    data_inicio: datetime,
    data_fim: datetime,
    motivo: str,
) -> SolicitacaoAusencia:
    ids_json, nomes_json = serializar_cargos(membro)
    id_fivem = await obter_id_fivem(membro.id)
    cargo = cargo_atual_hierarquia(membro)
    rotulo, _ = PERIODOS_AUSENCIA.get(periodo_chave, (periodo_chave, 0))

    async with async_session() as sessao:
        registro = SolicitacaoAusencia(
            discord_id=membro.id,
            id_fivem=id_fivem,
            membro_nome=membro.display_name[:120],
            tipo=tipo,
            periodo_chave=periodo_chave,
            periodo_rotulo=rotulo,
            data_inicio=data_inicio,
            data_fim=data_fim,
            motivo=(motivo or "")[:2000],
            cargos_anteriores_ids=ids_json,
            cargos_anteriores_nomes=nomes_json,
            cargo_principal=cargo,
            status="pendente",
            data_solicitacao=datetime.now(timezone.utc),
        )
        sessao.add(registro)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def marcar_mensagem_pedido(
    solicitacao_id: int,
    canal_id: int,
    mensagem_id: int,
) -> None:
    async with async_session() as sessao:
        registro = await sessao.get(SolicitacaoAusencia, solicitacao_id)
        if registro is None:
            return
        registro.mensagem_canal_id = canal_id
        registro.mensagem_id = mensagem_id
        registro.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()


async def obter_solicitacao(solicitacao_id: int) -> SolicitacaoAusencia | None:
    async with async_session() as sessao:
        return await sessao.get(SolicitacaoAusencia, solicitacao_id)


async def decidir_ausencia(
    *,
    solicitacao_id: int,
    aprovada: bool,
    diretor: discord.Member,
) -> tuple[SolicitacaoAusencia | None, bool]:
    """Atualiza status. Retorna (registro, foi_decidido_agora)."""
    async with async_session() as sessao:
        registro = await sessao.get(SolicitacaoAusencia, solicitacao_id)
        if registro is None:
            return None, False
        if registro.status != "pendente":
            return registro, False

        registro.status = "aprovada" if aprovada else "negada"
        registro.aprovado_por_id = diretor.id
        registro.aprovado_por_nome = str(diretor)[:120]
        registro.data_decisao = datetime.now(timezone.utc)
        registro.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro, True


async def aplicar_cargos_ausencia(
    membro: discord.Member,
    *,
    executor: discord.Member,
    motivo: str,
) -> tuple[bool, str]:
    """
    Remove cargos gerenciáveis e deixa apenas:
    🚫 Ausente + HP S・Valley + Aprovado (+ @everyone).
    """
    guilda = membro.guild
    bot_membro = guilda.me
    if bot_membro is None:
        return False, "Bot sem contexto de membro na guilda."

    roles_manter: list[discord.Role] = []
    for nome in CARGOS_MANTER_AUSENCIA:
        rid = CARGOS.get(nome)
        if rid:
            role = guilda.get_role(rid)
            if role is not None:
                roles_manter.append(role)

    if not any(
        r.name == "🚫 Ausente" or r.id == CARGOS.get("🚫 Ausente") for r in roles_manter
    ):
        rid_ausente = CARGOS.get("🚫 Ausente")
        if rid_ausente:
            role_a = guilda.get_role(rid_ausente)
            if role_a:
                roles_manter.append(role_a)

    ids_manter = {guilda.default_role.id} | {r.id for r in roles_manter}
    cargos_para_remover: list[discord.Role] = []
    for cargo in list(membro.roles):
        if cargo.id in ids_manter:
            continue
        if cargo.managed:
            continue
        if cargo >= bot_membro.top_role:
            continue
        cargos_para_remover.append(cargo)

    motivo_discord = f"Ausência aprovada — {executor} — {motivo[:80]}"
    try:
        if cargos_para_remover:
            await membro.remove_roles(*cargos_para_remover, reason=motivo_discord)
        for role in roles_manter:
            if role not in membro.roles:
                await membro.add_roles(role, reason=motivo_discord)
    except discord.Forbidden:
        return False, "Sem permissão para alterar os cargos do membro."
    except discord.HTTPException as erro:
        return False, f"Falha ao ajustar cargos: {erro}"

    return True, "Cargos ajustados (Ausente + HP S・Valley + Aprovado)."
