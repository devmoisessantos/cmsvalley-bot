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
from src.database.conexao import async_session
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
    """Identifica o primeiro cargo hierárquico configurado para registrar o contexto."""
    nomes = {cargo.name for cargo in membro.roles}
    for nome in CARGOS_HIERARQUIA:
        if nome in nomes:
            return nome
    return "—"


def membro_e_diretoria(membro: discord.Member) -> bool:
    """Confere a Diretoria por nomes configurados, não pela posição do cargo."""
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
    """Lê o FiveM salvo no cadastro sem obrigar a existência prévia do usuário."""
    async with async_session() as sessao:
        usuario = await sessao.get(Usuario, discord_id)
        if usuario is None:
            return None
        return usuario.id_fivem


async def obter_pedido_pendente(discord_id: int) -> SolicitacaoAusencia | None:
    """Localiza pendência recente para impedir pedidos simultâneos do mesmo membro."""
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
    """Ausência em vigor (aprovada) — ainda não finalizada."""
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


async def obter_retorno_pendente(discord_id: int) -> SolicitacaoAusencia | None:
    """Localiza retorno em análise para evitar restaurar cargos mais de uma vez."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoAusencia)
            .where(
                SolicitacaoAusencia.discord_id == discord_id,
                SolicitacaoAusencia.status == "retorno_pendente",
            )
            .order_by(SolicitacaoAusencia.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


def serializar_cargos(membro: discord.Member) -> tuple[str, str]:
    """Guarda IDs e nomes dos cargos para permitir recuperação após a ausência."""
    ids = [cargo.id for cargo in membro.roles if cargo != membro.guild.default_role]
    nomes = [cargo.name for cargo in membro.roles if cargo != membro.guild.default_role]
    return json.dumps(ids), json.dumps(nomes, ensure_ascii=False)


def deserializar_cargos_ids(texto: str | None) -> list[int]:
    """Recupera IDs válidos sem propagar JSON corrompido na restauração de cargos."""
    if not texto:
        return []
    try:
        dados = json.loads(texto)
        return [int(id_de_cargo) for id_de_cargo in dados if id_de_cargo is not None]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


async def atualizar_cargos_anteriores(
    solicitacao_id: int,
    membro: discord.Member,
) -> None:
    """Regrava snapshot de cargos no momento da aprovação da ausência."""
    ids_json, nomes_json = serializar_cargos(membro)
    async with async_session() as sessao:
        registro = await sessao.get(SolicitacaoAusencia, solicitacao_id)
        if registro is None:
            return
        registro.cargos_anteriores_ids = ids_json
        registro.cargos_anteriores_nomes = nomes_json
        registro.cargo_principal = cargo_atual_hierarquia(membro)
        registro.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()


async def criar_solicitacao(
    *,
    membro: discord.Member,
    tipo: str,
    periodo_chave: str,
    data_inicio: datetime,
    data_fim: datetime,
    motivo: str,
) -> SolicitacaoAusencia:
    """Cria uma solicitação pendente com o retrato de cargos do membro.

    Salva o FiveM, o cargo hierárquico e os cargos completos antes de qualquer
    remoção no Discord. Esse retrato é indispensável para restaurar corretamente
    a posição do membro caso o retorno seja aprovado.
    """
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
    """Vincula ao pedido a mensagem administrativa que deverá ser atualizada depois."""
    async with async_session() as sessao:
        registro = await sessao.get(SolicitacaoAusencia, solicitacao_id)
        if registro is None:
            return
        registro.mensagem_canal_id = canal_id
        registro.mensagem_id = mensagem_id
        registro.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()


async def obter_solicitacao(solicitacao_id: int) -> SolicitacaoAusencia | None:
    """Obtém o pedido pela chave sem falhar quando ele foi removido."""
    async with async_session() as sessao:
        return await sessao.get(SolicitacaoAusencia, solicitacao_id)


async def decidir_ausencia(
    *,
    solicitacao_id: int,
    aprovada: bool,
    diretor: discord.Member,
) -> tuple[SolicitacaoAusencia | None, bool]:
    """Atualiza status de pedido de ausência. Retorna (registro, foi_decidido_agora)."""
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


async def solicitar_retorno(solicitacao_id: int) -> SolicitacaoAusencia | None:
    """Marca ausência ativa como retorno_pendente (aguarda Diretoria)."""
    async with async_session() as sessao:
        registro = await sessao.get(SolicitacaoAusencia, solicitacao_id)
        if registro is None or registro.status != "aprovada":
            return None
        registro.status = "retorno_pendente"
        registro.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def decidir_retorno(
    *,
    solicitacao_id: int,
    aprovada: bool,
    diretor: discord.Member,
) -> tuple[SolicitacaoAusencia | None, bool]:
    """
    Aprova/nega pedido de retorno.
    - aprovada → finalizada (cargos serão restaurados pelo painel)
    - negada → volta para aprovada (continua ausente)
    """
    async with async_session() as sessao:
        registro = await sessao.get(SolicitacaoAusencia, solicitacao_id)
        if registro is None:
            return None, False
        if registro.status != "retorno_pendente":
            return registro, False

        if aprovada:
            registro.status = "finalizada"
        else:
            registro.status = "aprovada"
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
        id_do_cargo = CARGOS.get(nome)
        if id_do_cargo:
            role = guilda.get_role(id_do_cargo)
            if role is not None:
                roles_manter.append(role)

    if not any(
        cargo_a_manter.name == "🚫 Ausente"
        or cargo_a_manter.id == CARGOS.get("🚫 Ausente")
        for cargo_a_manter in roles_manter
    ):
        rid_ausente = CARGOS.get("🚫 Ausente")
        if rid_ausente:
            role_a = guilda.get_role(rid_ausente)
            if role_a:
                roles_manter.append(role_a)

    ids_manter = {guilda.default_role.id} | {
        cargo_a_manter.id for cargo_a_manter in roles_manter
    }
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


async def restaurar_cargos_ausencia(
    membro: discord.Member,
    *,
    solicitacao: SolicitacaoAusencia,
    executor: discord.Member,
) -> tuple[bool, str]:
    """
    Restaura cargos salvos no pedido e remove 🚫 Ausente.
    Mantém cargos que o bot não consegue gerenciar.
    """
    guilda = membro.guild
    bot_membro = guilda.me
    if bot_membro is None:
        return False, "Bot sem contexto de membro na guilda."

    ids_salvos = deserializar_cargos_ids(solicitacao.cargos_anteriores_ids)
    id_ausente = CARGOS.get("🚫 Ausente")

    roles_para_adicionar: list[discord.Role] = []
    for id_do_cargo in ids_salvos:
        if id_ausente and id_do_cargo == id_ausente:
            continue
        role = guilda.get_role(id_do_cargo)
        if role is None:
            continue
        if role.managed:
            continue
        if role >= bot_membro.top_role:
            continue
        if role not in membro.roles:
            roles_para_adicionar.append(role)

    roles_para_remover: list[discord.Role] = []
    if id_ausente:
        role_ausente = guilda.get_role(id_ausente)
        if role_ausente is not None and role_ausente in membro.roles:
            if role_ausente < bot_membro.top_role and not role_ausente.managed:
                roles_para_remover.append(role_ausente)

    motivo_discord = (
        f"Retorno de ausência #{solicitacao.id} — {executor} — cargos restaurados"
    )
    try:
        if roles_para_remover:
            await membro.remove_roles(*roles_para_remover, reason=motivo_discord)
        if roles_para_adicionar:
            await membro.add_roles(*roles_para_adicionar, reason=motivo_discord)
    except discord.Forbidden:
        return False, "Sem permissão para restaurar os cargos do membro."
    except discord.HTTPException as erro:
        return False, f"Falha ao restaurar cargos: {erro}"

    return (
        True,
        f"Cargos restaurados ({len(roles_para_adicionar)} adicionados, "
        f"{len(roles_para_remover)} removidos).",
    )
