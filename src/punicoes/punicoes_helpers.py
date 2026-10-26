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
    ids = {CARGOS[numero] for numero in CARGOS_DIRETORIA if numero in CARGOS}
    return any(cargo.id in ids for cargo in membro.roles)


def mensagem_sem_permissao() -> str:
    """Mantém o bloqueio do painel com texto único e consistente para a equipe."""
    return "❌ Você não tem permissão para usar o painel de punições."


def lista_cargos_punicao_ordenada() -> list[tuple[str, int]]:
    """Ordem de escalonamento: verbal → adv1 → adv2 → adv3 → exonerado."""
    return [(nome, id_do_cargo) for nome, id_do_cargo in CARGOS_PUNICOES.items()]


def cargo_punicao_atual(membro: discord.Member) -> tuple[str, int] | None:
    """Retorna o cargo de punição ativo de maior grau (último da lista presente)."""
    atual = None
    for nome, id_do_cargo in lista_cargos_punicao_ordenada():
        if any(cargo.id == id_do_cargo for cargo in membro.roles):
            atual = (nome, id_do_cargo)
    return atual


def proximo_cargo_punicao(membro: discord.Member) -> tuple[str, int] | None:
    """Próximo nível de advertência a aplicar (primeiro que o membro ainda não tem)."""
    for nome, id_do_cargo in lista_cargos_punicao_ordenada():
        if not any(cargo.id == id_do_cargo for cargo in membro.roles):
            return nome, id_do_cargo
    return None


def parse_links(texto: str | None) -> list[str]:
    """Extrai URLs de um texto (até 15)."""
    if not texto:
        return []
    urls = re.findall(r"https?://\S+", texto)
    return urls[:15]


def ids_cargos_advertencia_formal() -> set[int]:
    """IDs de Adv 01 / Adv 02 / Adv 03 (exclui verbal e exonerado)."""
    ids: set[int] = set()
    for nome, cargo_id in CARGOS_PUNICOES.items():
        nome_minusculo = nome.lower()
        e_formal = "adv 0" in nome_minusculo or "adv0" in nome_minusculo
        e_exonerado = "exonerado" in nome_minusculo
        e_verbal = "verbal" in nome_minusculo
        if e_formal and not e_exonerado and not e_verbal:
            ids.add(cargo_id)
    return ids


def id_cargo_exonerado() -> int | None:
    """ID do cargo Exonerado, se configurado."""
    for nome, cargo_id in CARGOS_PUNICOES.items():
        if "exonerado" in nome.lower():
            return cargo_id
    return None


def e_cargo_exonerado(
    cargo_nome: str | None = None, cargo_id: int | None = None
) -> bool:
    """True se o cargo indicado for o de Exonerado."""
    id_exonerado = id_cargo_exonerado()
    if cargo_id is not None and id_exonerado is not None:
        return cargo_id == id_exonerado
    if cargo_nome:
        return "exonerado" in cargo_nome.lower()
    return False


def pontos_da_advertencia(cargo_nome: str | None) -> int:
    """
    Nível de gravidade em pontos (soma ≥ 3 → exoneração).

    Adv 01 (leve) = 1
    Adv 02 (grave) = 2
    Adv 03 (gravíssimo) = 3
    Verbal e Exonerado = 0
    """
    if not cargo_nome:
        return 0
    nome = cargo_nome.lower()
    if "exonerado" in nome or "verbal" in nome:
        return 0
    if "adv 03" in nome or "adv03" in nome:
        return 3
    if "adv 02" in nome or "adv02" in nome:
        return 2
    if "adv 01" in nome or "adv01" in nome:
        return 1
    if "adv 0" in nome or "adv0" in nome:
        return 1
    return 0


def quantidade_advertencias_formais_no_membro(membro: discord.Member) -> int:
    """
    Quantos cargos formais (Adv 01/02/03) o membro tem no Discord.

    Mantido para telas. A regra de exoneração usa a soma de pontos
    dos registros ativos no banco (ver pontos_formais_ativos).
    """
    ids_formais = ids_cargos_advertencia_formal()
    quantidade = 0
    for cargo in membro.roles:
        if cargo.id in ids_formais:
            quantidade += 1
    return quantidade


async def pontos_formais_ativos(discord_id: int) -> int:
    """
    Soma os pontos das advertências formais ainda ativas no banco.

    Cada aplicação grava um registro. Assim 3x Adv 01 = 3 pontos,
    Adv 01 + Adv 02 = 3 pontos, Adv 03 sozinha = 3 pontos.
    """
    from sqlalchemy import select

    from src.database.conexao import async_session
    from src.database.models import Punicao

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Punicao.cargo_nome).where(
                Punicao.discord_id == discord_id,
                Punicao.ativa.is_(True),
            )
        )
        nomes = list(resultado.scalars().all())

    total = 0
    for nome in nomes:
        total += pontos_da_advertencia(nome)
    return total


async def resolver_id_fivem(discord_id: int) -> str | None:
    """Busca o FiveM nas fontes internas em ordem de maior disponibilidade.

    Consulta primeiro os estados operacionais e o perfil, depois o recrutamento aprovado
    mais recente. A ordem aproveita dados já atualizados sem deixar de exigir a fonte
    formal quando não há vínculo nos registros mais imediatos.
    """
    from sqlalchemy import select

    from src.database.conexao import async_session
    from src.database.models import (
        EstadoPlantao,
        Recrutamento,
        Usuario,
    )

    async with async_session() as session:
        for model, coluna in (
            (EstadoPlantao, EstadoPlantao.id_fivem),
            (Usuario, Usuario.id_fivem),
        ):
            resultado_da_consulta = await session.execute(
                select(coluna).where(
                    model.discord_id == discord_id,
                    coluna.is_not(None),
                )
            )
            id_fivem_encontrado = resultado_da_consulta.scalar_one_or_none()
            if id_fivem_encontrado:
                return str(id_fivem_encontrado)

        resultado_da_consulta = await session.execute(
            select(Recrutamento.id_fivem)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.status == "APROVADO",
                Recrutamento.id_fivem.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        id_fivem_encontrado = resultado_da_consulta.scalar_one_or_none()
        return str(id_fivem_encontrado) if id_fivem_encontrado else None



def nivel_da_advertencia(cargo_nome: str | None) -> int:
    """
    0 = verbal / desconhecido, 1 = Adv 01, 2 = Adv 02, 3 = Adv 03 ou Exonerado.
    """
    if not cargo_nome:
        return 0
    nome = cargo_nome.lower()
    if "verbal" in nome:
        return 0
    if "exonerado" in nome:
        return 3
    if "adv 03" in nome or "adv03" in nome:
        return 3
    if "adv 02" in nome or "adv02" in nome:
        return 2
    if "adv 01" in nome or "adv01" in nome:
        return 1
    if "adv 0" in nome or "adv0" in nome:
        return 1
    return 0


def valor_pagamento_advertencia(
    cargo_nome: str | None,
    *,
    membro: "discord.Member | None" = None,
    cargos_ids_snapshot: list[int] | None = None,
) -> tuple[int, str]:
    """
    Valor in-game esperado para quitar a advertência.

    Adv 01 → 500 mil
    Adv 02 → 1 milhão
    Adv 03 / Exonerado → valor da blacklist do cargo mais alto do membro
    Verbal → 0 (não tem tabela de pagamento formal)

    Devolve (valor, rotulo_explicativo).
    """
    from src.config import (
        BLACKLIST_POR_CARGO,
        CARGOS,
        CARGOS_HIERARQUIA,
        VALOR_PAGAMENTO_ADVERTENCIA,
    )

    nivel = nivel_da_advertencia(cargo_nome)
    if nivel <= 0:
        return 0, "ADV VERBAL — sem valor de pagamento formal"
    if nivel == 1:
        valor = int(VALOR_PAGAMENTO_ADVERTENCIA.get(1, 500_000))
        return valor, "Nível 1 (média) — Adv 01"
    if nivel == 2:
        valor = int(VALOR_PAGAMENTO_ADVERTENCIA.get(2, 1_000_000))
        return valor, "Nível 2 (grave) — Adv 02"

    # Nível 3: blacklist do cargo mais alto
    nomes_por_id = {int(v): k for k, v in CARGOS.items()}
    candidatos: list[str] = []
    if cargos_ids_snapshot:
        for cid in cargos_ids_snapshot:
            nome = nomes_por_id.get(int(cid))
            if nome:
                candidatos.append(nome)
    if membro is not None:
        for cargo in membro.roles:
            nome = nomes_por_id.get(int(cargo.id))
            if nome:
                candidatos.append(nome)
            # também tenta pelo nome do role no Discord
            if cargo.name and cargo.name in BLACKLIST_POR_CARGO:
                candidatos.append(cargo.name)

    melhor_nome = "🚑・Paramédico"
    melhor_rank = 10_000
    for nome in candidatos:
        if nome not in BLACKLIST_POR_CARGO:
            continue
        try:
            rank = CARGOS_HIERARQUIA.index(nome)
        except ValueError:
            rank = 9999
        if rank < melhor_rank:
            melhor_rank = rank
            melhor_nome = nome

    cfg = BLACKLIST_POR_CARGO.get(melhor_nome) or {
        "dias": 14,
        "valor": 3_000_000,
    }
    valor = int(cfg.get("valor") or 3_000_000)
    return valor, f"Nível 3 — blacklist de {melhor_nome}"
