# src/demissao/demissao_service.py
"""Regras de demissão voluntária."""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import (
    func,
    select,
)

from src.config import (
    CARGOS,
    CARGOS_DIRETORIA,
    CARGOS_HIERARQUIA,
)
from src.database.connection import async_session
from src.database.models import (
    Punicao,
    SolicitacaoDemissao,
)
from src.utils.nickname import remover_prefixo_existente


def cargo_atual_hierarquia(membro: discord.Member) -> str:
    """Maior cargo da hierarquia que o membro possui (ou '—' se nenhum)."""
    nomes = {cargo.name for cargo in membro.roles}
    for nome in CARGOS_HIERARQUIA:
        if nome in nomes:
            return nome
    return "—"


def membro_pode_solicitar_demissao(membro: discord.Member) -> bool:
    """Só quem tem cargo da hierarquia (não é só Visitante)."""
    nomes = {cargo.name for cargo in membro.roles}
    return bool(nomes.intersection(set(CARGOS_HIERARQUIA)))


def membro_e_diretoria(membro: discord.Member) -> bool:
    nomes = {cargo.name for cargo in membro.roles}
    return bool(nomes.intersection(set(CARGOS_DIRETORIA)))


async def contar_advertencias_ativas(discord_id: int) -> int:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.count())
            .select_from(Punicao)
            .where(Punicao.discord_id == discord_id, Punicao.ativa.is_(True))
        )
        return int(resultado.scalar_one() or 0)


async def obter_pedido_pendente(discord_id: int) -> SolicitacaoDemissao | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoDemissao)
            .where(
                SolicitacaoDemissao.discord_id == discord_id,
                SolicitacaoDemissao.status == "pendente",
            )
            .order_by(SolicitacaoDemissao.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def criar_solicitacao(
    *,
    membro: discord.Member,
    motivo: str,
) -> SolicitacaoDemissao:
    advertencias = await contar_advertencias_ativas(membro.id)
    cargo = cargo_atual_hierarquia(membro)
    async with async_session() as sessao:
        registro = SolicitacaoDemissao(
            discord_id=membro.id,
            membro_nome=membro.display_name[:120],
            cargo=cargo,
            tipo_demissao="voluntaria",
            motivo=motivo[:2000],
            data_solicitacao=datetime.now(timezone.utc),
            solicitante_nome=str(membro)[:120],
            advertencias=advertencias,
            status="pendente",
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
        registro = await sessao.get(SolicitacaoDemissao, solicitacao_id)
        if registro is None:
            return
        registro.mensagem_canal_id = canal_id
        registro.mensagem_id = mensagem_id
        registro.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()


async def obter_solicitacao(solicitacao_id: int) -> SolicitacaoDemissao | None:
    async with async_session() as sessao:
        return await sessao.get(SolicitacaoDemissao, solicitacao_id)


async def decidir_demissao(
    *,
    solicitacao_id: int,
    aprovada: bool,
    diretor: discord.Member,
) -> tuple[SolicitacaoDemissao | None, bool]:
    """
    Atualiza status. Retorna (registro, foi_decidido_agora).
    Se já estava decidido, foi_decidido_agora=False.
    """
    async with async_session() as sessao:
        registro = await sessao.get(SolicitacaoDemissao, solicitacao_id)
        if registro is None:
            return None, False
        if registro.status != "pendente":
            return registro, False

        registro.status = "aprovada" if aprovada else "negada"
        registro.aprovado_por_id = diretor.id
        registro.aprovado_por_nome = str(diretor)[:120]
        registro.atualizado_em = datetime.now(timezone.utc)
        if aprovada:
            registro.data_efetiva = datetime.now(timezone.utc)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro, True


async def aplicar_cargos_demissao(
    membro: discord.Member,
    *,
    executor: discord.Member,
    motivo: str,
) -> tuple[bool, str]:
    """
    Remove todos os cargos gerenciáveis e deixa apenas Visitantes.
    """
    guilda = membro.guild
    id_visitantes = CARGOS.get("Visitantes")
    role_visitantes = guilda.get_role(id_visitantes) if id_visitantes else None
    if role_visitantes is None:
        return False, "Cargo **Visitantes** não encontrado no config/servidor."

    bot_membro = guilda.me
    if bot_membro is None:
        return False, "Bot sem contexto de membro na guilda."

    ids_manter = {guilda.default_role.id, role_visitantes.id}
    cargos_para_remover: list[discord.Role] = []
    for cargo in list(membro.roles):
        if cargo.id in ids_manter:
            continue
        if cargo.managed:
            continue
        if cargo >= bot_membro.top_role:
            continue
        cargos_para_remover.append(cargo)

    motivo_discord = f"Demissão voluntária — {executor} — {motivo[:80]}"
    try:
        if cargos_para_remover:
            await membro.remove_roles(*cargos_para_remover, reason=motivo_discord)
        if role_visitantes not in membro.roles:
            await membro.add_roles(role_visitantes, reason=motivo_discord)
    except discord.Forbidden:
        return False, "Sem permissão para alterar os cargos do membro."
    except discord.HTTPException as erro:
        return False, f"Falha ao ajustar cargos: {erro}"

    # Remove prefixo [ TAG ] do nick — mesmo padrão da exoneração
    nick_limpo = remover_prefixo_existente(membro.display_name)[:32]
    try:
        nick_atual = membro.nick or membro.display_name
        if nick_limpo and nick_limpo != nick_atual:
            await membro.edit(nick=nick_limpo, reason=motivo_discord)
    except (discord.Forbidden, discord.HTTPException):
        # Nick não é crítico — demissão segue mesmo se falhar
        pass

    return True, "Cargos ajustados (restou Visitantes) e prefixo removido do nick."
