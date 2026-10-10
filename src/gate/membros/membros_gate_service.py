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

import logging

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
from src.utils.nickname import (
    aplicar_prefixo,
    escolher_cargo_do_prefixo,
    nomes_de_cargos_com_prefixo_do_membro,
)

logger = logging.getLogger(__name__)

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


async def _aplicar_tag_pelo_cargo_mais_alto(
    membro: discord.Member,
    *,
    motivo: str,
    nomes_extras: list[str] | None = None,
) -> None:
    """
    Atualiza o nick com a tag do cargo mais alto (GATE tem prioridade).

    Nunca rebaixa a tag: se o membro já tem cargo mais alto, a tag permanece.
    """
    nomes = nomes_de_cargos_com_prefixo_do_membro(membro)
    for nome in nomes_extras or []:
        if nome not in nomes:
            nomes.append(nome)
    cargo_do_prefixo = escolher_cargo_do_prefixo(nomes)
    if cargo_do_prefixo is None:
        return
    try:
        nick_atual = membro.nick or membro.display_name or membro.name
        novo_nick = aplicar_prefixo(nick_atual, cargo_do_prefixo)
        if novo_nick and novo_nick != membro.nick:
            await membro.edit(nick=novo_nick[:32], reason=motivo)
    except discord.Forbidden:
        logger.warning(
            "Sem permissão para editar nick de %s (%s)", membro.id, motivo
        )
    except discord.HTTPException as erro:
        logger.warning(
            "Falha ao editar nick de %s (%s): %s", membro.id, motivo, erro
        )


async def aprovar_ingresso(
    guild: discord.Guild,
    solicitacao: SolicitacaoIngressoGate,
    aprovador: discord.Member,
) -> tuple[bool, str]:
    """
    Aprova ingresso: aplica Guardião + base GATE, tag GATE e marca solicitação.
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

    # Tag GATE substitui a hospitalar (ex.: [ PAR ] → 【G · GATE】)
    await _aplicar_tag_pelo_cargo_mais_alto(
        candidato,
        motivo=f"Prefixo GATE no ingresso por {aprovador}",
        nomes_extras=[CARGO_INGRESSO_GATE],
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

    return True, "Ingresso aprovado. Cargos GATE e tag aplicados."


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
    """
    Sobe um degrau na HIERARQUIA_GATE (em direção ao Comandante).

    Não remove cargos anteriores. Garante o cargo novo e todos os cargos
    abaixo dele (ex.: subir a Capitão também assegura Operador, Guardião
    e base, se faltarem).
    """
    atual = cargo_gate_atual(alvo)
    if atual is None:
        return False, "O membro não possui cargo GATE."

    indice = indice_cargo_gate(atual)
    if indice is None or indice == 0:
        return False, "O membro já está no topo da hierarquia GATE."

    nome_novo = HIERARQUIA_GATE[indice - 1]
    indice_novo = indice - 1

    # Cargo promovido + todos abaixo dele (índices maiores na lista)
    nomes_desejados = list(HIERARQUIA_GATE[indice_novo:])
    roles_para_adicionar: list[discord.Role] = []
    nomes_adicionados: list[str] = []
    for nome_cargo in nomes_desejados:
        role = guild.get_role(CARGOS.get(nome_cargo, 0) or 0)
        if role is None:
            if nome_cargo == nome_novo:
                return False, f"Cargo `{nome_novo}` não encontrado no servidor."
            continue
        if role not in alvo.roles and role not in roles_para_adicionar:
            roles_para_adicionar.append(role)
            nomes_adicionados.append(nome_cargo)

    if roles_para_adicionar:
        await alvo.add_roles(
            *roles_para_adicionar,
            reason=f"Promoção GATE por {executor} (mantém cargos anteriores)",
        )

    await _aplicar_tag_pelo_cargo_mais_alto(
        alvo,
        motivo=f"Prefixo GATE na promoção por {executor}",
        nomes_extras=[nome_novo],
    )

    if nomes_adicionados:
        lista = ", ".join(f"**{nome}**" for nome in nomes_adicionados)
        return (
            True,
            f"Promovido de **{atual}** para **{nome_novo}**. "
            f"Cargos adicionados/confirmados: {lista}.",
        )
    return (
        True,
        f"Promovido de **{atual}** para **{nome_novo}** "
        "(já possuía os cargos da faixa).",
    )


async def rebaixar_membro_gate(
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
) -> tuple[bool, str]:
    """
    Desce um degrau na HIERARQUIA_GATE.

    Remove só os cargos **acima** do novo nível. Mantém o cargo novo e
    todos os de baixo (e completa os que faltarem). Atualiza a tag.
    """
    atual = cargo_gate_atual(alvo)
    if atual is None:
        return False, "O membro não possui cargo GATE."

    indice = indice_cargo_gate(atual)
    if indice is None or indice >= len(HIERARQUIA_GATE) - 1:
        return False, "O membro já está no cargo GATE mais baixo."

    nome_novo = HIERARQUIA_GATE[indice + 1]
    indice_novo = indice + 1

    # Tudo acima do novo nível sai
    roles_para_remover: list[discord.Role] = []
    nomes_removidos: list[str] = []
    for nome_acima in HIERARQUIA_GATE[:indice_novo]:
        role = guild.get_role(CARGOS.get(nome_acima, 0) or 0)
        if role is not None and role in alvo.roles:
            roles_para_remover.append(role)
            nomes_removidos.append(nome_acima)

    # Novo nível + todos abaixo ficam (ou são adicionados se faltarem)
    roles_para_adicionar: list[discord.Role] = []
    nomes_adicionados: list[str] = []
    for nome_abaixo in HIERARQUIA_GATE[indice_novo:]:
        role = guild.get_role(CARGOS.get(nome_abaixo, 0) or 0)
        if role is None:
            if nome_abaixo == nome_novo:
                return False, f"Cargo `{nome_novo}` não encontrado no servidor."
            continue
        if role not in alvo.roles and role not in roles_para_adicionar:
            roles_para_adicionar.append(role)
            nomes_adicionados.append(nome_abaixo)

    if roles_para_remover:
        await alvo.remove_roles(
            *roles_para_remover,
            reason=f"Rebaixamento GATE por {executor}",
        )
    if roles_para_adicionar:
        await alvo.add_roles(
            *roles_para_adicionar,
            reason=f"Rebaixamento GATE por {executor} (completa faixa)",
        )

    await _aplicar_tag_pelo_cargo_mais_alto(
        alvo,
        motivo=f"Prefixo GATE no rebaixamento por {executor}",
        nomes_extras=[nome_novo],
    )

    partes: list[str] = [f"Rebaixado de **{atual}** para **{nome_novo}**."]
    if nomes_removidos:
        lista_rem = ", ".join(f"**{nome}**" for nome in nomes_removidos)
        partes.append(f"Removidos (acima): {lista_rem}.")
    if nomes_adicionados:
        lista_add = ", ".join(f"**{nome}**" for nome in nomes_adicionados)
        partes.append(f"Completados (faixa): {lista_add}.")
    return True, " ".join(partes)


async def expulsar_membro_gate(
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
) -> tuple[bool, str]:
    """
    Remove todos os cargos GATE.

    Os cargos hospitalares permanecem. A tag do nick volta para o cargo
    hospitalar mais alto (ou some a tag GATE).
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

    # Tag: após sair da GATE, usa o cargo hospitalar mais alto
    await _aplicar_tag_pelo_cargo_mais_alto(
        alvo,
        motivo=f"Prefixo após expulsão GATE por {executor}",
    )

    nomes = ", ".join(cargo.name for cargo in cargos_gate)
    return True, f"Cargos GATE removidos: {nomes}."
