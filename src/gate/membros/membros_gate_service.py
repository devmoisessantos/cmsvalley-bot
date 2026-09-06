"""
Regras de negócio do ingresso e da gestão de membros GATE.

- Validar requisitos de entrada (Paramédico++, cursos, não estar na GATE).
- Aprovar / reprovar solicitação.
- Promover, rebaixar e expulsar (remove cargos GATE).
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select

from src.config import (
    CARGO_BASE_GATE,
    CARGO_INGRESSO_GATE,
    CARGO_PARAMEDICO,
    CARGOS,
    CARGOS_GESTAO_GATE,
    CARGOS_HIERARQUIA,
    CURSOS_OBRIGATORIOS_INGRESSO_GATE,
    HIERARQUIA_GATE,
)
from src.cursos.cursos_service import (
    listar_cursos_que_faltam,
    rotulo_curso,
)
from src.database.conexao import async_session
from src.database.models import SolicitacaoIngressoGate
from src.gate.gate_service import membro_pertence_a_gate


def e_gestor_gate(membro: discord.Member) -> bool:
    """Comandante ou Subcomandante tático."""
    nomes = {cargo.name for cargo in membro.roles}
    return bool(nomes.intersection(CARGOS_GESTAO_GATE))


def e_paramedico_ou_acima(membro: discord.Member) -> bool:
    """Paramédico ou qualquer cargo hospitalar acima dele na hierarquia."""
    if CARGO_PARAMEDICO not in CARGOS_HIERARQUIA:
        return any(cargo.name == CARGO_PARAMEDICO for cargo in membro.roles)
    indice_paramedico = CARGOS_HIERARQUIA.index(CARGO_PARAMEDICO)
    cargos_aceitos = set(CARGOS_HIERARQUIA[: indice_paramedico + 1])
    nomes = {cargo.name for cargo in membro.roles}
    return bool(nomes.intersection(cargos_aceitos))


def indice_cargo_gate(nome_cargo: str) -> int | None:
    """Posição na HIERARQUIA_GATE (0 = mais alto) ou None."""
    if nome_cargo not in HIERARQUIA_GATE:
        return None
    return HIERARQUIA_GATE.index(nome_cargo)


def cargo_gate_atual(membro: discord.Member) -> str | None:
    """Nome do cargo GATE mais alto do membro, ou None."""
    nomes = {cargo.name for cargo in membro.roles}
    for nome in HIERARQUIA_GATE:
        if nome in nomes:
            return nome
    return None


def validar_requisitos_ingresso(
    membro: discord.Member,
) -> tuple[bool, list[str]]:
    """
    Confere se o membro pode solicitar ingresso.

    Devolve (ok, lista de pendências em texto humano).
    """
    pendencias: list[str] = []

    if membro_pertence_a_gate(membro):
        pendencias.append("Você já faz parte da GATE.")

    if not e_paramedico_ou_acima(membro):
        pendencias.append(
            "É necessário ser **Paramédico** (ou cargo hospitalar superior) "
            "ativo no CMS."
        )

    faltando = listar_cursos_que_faltam(
        membro, list(CURSOS_OBRIGATORIOS_INGRESSO_GATE)
    )
    for chave in faltando:
        pendencias.append(f"Curso pendente: **{rotulo_curso(chave)}**")

    return (len(pendencias) == 0, pendencias)


async def buscar_solicitacao_pendente(
    discord_id: int,
) -> SolicitacaoIngressoGate | None:
    """Última solicitação ainda pendente do membro."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoIngressoGate)
            .where(
                SolicitacaoIngressoGate.discord_id_candidato == discord_id,
                SolicitacaoIngressoGate.status == "pendente",
            )
            .order_by(SolicitacaoIngressoGate.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def criar_solicitacao_ingresso(
    membro: discord.Member,
) -> SolicitacaoIngressoGate:
    """Grava solicitação pendente no banco."""
    async with async_session() as sessao:
        registro = SolicitacaoIngressoGate(
            discord_id_candidato=membro.id,
            status="pendente",
        )
        sessao.add(registro)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def marcar_mensagem_solicitacao(
    solicitacao_id: int,
    canal_id: int,
    mensagem_id: int,
) -> None:
    """Guarda onde está o card de aprovação no Discord."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoIngressoGate).where(
                SolicitacaoIngressoGate.id == solicitacao_id
            )
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return
        registro.canal_id = canal_id
        registro.mensagem_id = mensagem_id
        await sessao.commit()


async def buscar_solicitacao_por_id(
    solicitacao_id: int,
) -> SolicitacaoIngressoGate | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoIngressoGate).where(
                SolicitacaoIngressoGate.id == solicitacao_id
            )
        )
        return resultado.scalar_one_or_none()


async def aprovar_ingresso(
    guild: discord.Guild,
    solicitacao: SolicitacaoIngressoGate,
    aprovador: discord.Member,
) -> tuple[bool, str]:
    """
    Aprova ingresso: aplica Guardião + base GATE e marca solicitação.
    """
    candidato = guild.get_member(solicitacao.discord_id_candidato)
    if candidato is None:
        return False, "Candidato não está no servidor."

    if solicitacao.status != "pendente":
        return False, "Esta solicitação já foi decidida."

    cargo_guardiao = guild.get_role(CARGOS.get(CARGO_INGRESSO_GATE, 0) or 0)
    cargo_base = guild.get_role(CARGOS.get(CARGO_BASE_GATE, 0) or 0)
    cargos_adicionar = [
        cargo
        for cargo in (cargo_guardiao, cargo_base)
        if cargo is not None and cargo not in candidato.roles
    ]
    if cargos_adicionar:
        await candidato.add_roles(
            *cargos_adicionar,
            reason=f"Ingresso GATE aprovado por {aprovador}",
        )

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoIngressoGate).where(
                SolicitacaoIngressoGate.id == solicitacao.id
            )
        )
        registro = resultado.scalar_one()
        registro.status = "aprovado"
        registro.discord_id_recrutador = aprovador.id
        registro.decidido_em = datetime.now(timezone.utc)
        await sessao.commit()

    return True, "Ingresso aprovado. Cargos GATE aplicados."


async def reprovar_ingresso(
    solicitacao: SolicitacaoIngressoGate,
    reprovador: discord.Member,
    motivo: str,
) -> tuple[bool, str]:
    if solicitacao.status != "pendente":
        return False, "Esta solicitação já foi decidida."

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoIngressoGate).where(
                SolicitacaoIngressoGate.id == solicitacao.id
            )
        )
        registro = resultado.scalar_one()
        registro.status = "reprovado"
        registro.discord_id_recrutador = reprovador.id
        registro.motivo_reprovacao = motivo[:500]
        registro.decidido_em = datetime.now(timezone.utc)
        await sessao.commit()

    return True, "Solicitação reprovada."


async def promover_membro_gate(
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
) -> tuple[bool, str]:
    """Sobe um degrau na HIERARQUIA_GATE (em direção ao Comandante)."""
    atual = cargo_gate_atual(alvo)
    if atual is None:
        return False, "O membro não possui cargo GATE."

    indice = indice_cargo_gate(atual)
    if indice is None or indice == 0:
        return False, "O membro já está no topo da hierarquia GATE."

    nome_novo = HIERARQUIA_GATE[indice - 1]
    cargo_novo = guild.get_role(CARGOS.get(nome_novo, 0) or 0)
    cargo_antigo = guild.get_role(CARGOS.get(atual, 0) or 0)
    if cargo_novo is None:
        return False, f"Cargo `{nome_novo}` não encontrado no servidor."

    if cargo_antigo is not None and cargo_antigo in alvo.roles:
        await alvo.remove_roles(
            cargo_antigo, reason=f"Promoção GATE por {executor}"
        )
    if cargo_novo not in alvo.roles:
        await alvo.add_roles(
            cargo_novo, reason=f"Promoção GATE por {executor}"
        )

    return True, f"Promovido de **{atual}** para **{nome_novo}**."


async def rebaixar_membro_gate(
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
) -> tuple[bool, str]:
    """Desce um degrau na HIERARQUIA_GATE."""
    atual = cargo_gate_atual(alvo)
    if atual is None:
        return False, "O membro não possui cargo GATE."

    indice = indice_cargo_gate(atual)
    if indice is None or indice >= len(HIERARQUIA_GATE) - 1:
        return False, "O membro já está no cargo GATE mais baixo."

    nome_novo = HIERARQUIA_GATE[indice + 1]
    cargo_novo = guild.get_role(CARGOS.get(nome_novo, 0) or 0)
    cargo_antigo = guild.get_role(CARGOS.get(atual, 0) or 0)
    if cargo_novo is None:
        return False, f"Cargo `{nome_novo}` não encontrado no servidor."

    if cargo_antigo is not None and cargo_antigo in alvo.roles:
        await alvo.remove_roles(
            cargo_antigo, reason=f"Rebaixamento GATE por {executor}"
        )
    if cargo_novo not in alvo.roles:
        await alvo.add_roles(
            cargo_novo, reason=f"Rebaixamento GATE por {executor}"
        )

    return True, f"Rebaixado de **{atual}** para **{nome_novo}**."


async def expulsar_membro_gate(
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
) -> tuple[bool, str]:
    """
    Remove todos os cargos GATE.

    Os cargos hospitalares permanecem (último cargo do hospital fica intacto).
    """
    cargos_gate = []
    for nome in HIERARQUIA_GATE:
        cargo = guild.get_role(CARGOS.get(nome, 0) or 0)
        if cargo is not None and cargo in alvo.roles:
            cargos_gate.append(cargo)

    if not cargos_gate:
        return False, "O membro não possui cargos GATE para remover."

    await alvo.remove_roles(
        *cargos_gate, reason=f"Expulsão GATE por {executor}"
    )
    nomes = ", ".join(cargo.name for cargo in cargos_gate)
    return True, f"Cargos GATE removidos: {nomes}."
