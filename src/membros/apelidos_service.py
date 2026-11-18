# src/membros/apelidos_service.py
"""
Varredura de apelidos: espelha o cargo mais alto no nick de cada membro.

O comando `/moderacao corrigir-apelidos` usa este serviço. A regra é a mesma
que o bot já aplica quando muda o cargo de alguém:

- só entra quem tem cargo de `CARGOS_HIERARQUIA` ou `HIERARQUIA_GATE`;
- a tag vem do cargo mais alto **que tem prefixo cadastrado**;
- o `| ID` que já está no apelido é preservado;
- Visitante, quem não tem cargo e quem só tem cargo base não é alterado.

Cargo de hierarquia sem prefixo em `PREFIXOS_NICKNAME` (por exemplo SUPERVISOR
e os cargos de "Responsável" de área) não ganha tag inventada: a varredura
conta esses membros como "sem tag cadastrada" e deixa o apelido como está.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import discord
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.config import (
    ATRASO_CORRECAO_APELIDOS_SEGUNDOS,
    CARGOS_HIERARQUIA,
    HIERARQUIA_GATE,
)
from src.database.conexao import async_session
from src.database.models import Usuario
from src.utils.nickname import aplicar_prefixo, escolher_cargo_do_prefixo

registrador = logging.getLogger(__name__)

# De quantos em quantos segundos a barra de progresso é atualizada.
SEGUNDOS_ENTRE_PROGRESSOS = 3.0

# Quantos exemplos de mudança cabem no card final.
LIMITE_DE_EXEMPLOS = 5


@dataclass
class ResultadoDaVarredura:
    """Contagem do que a varredura fez, para o card de resposta."""

    total_analisados: int = 0
    corrigidos: int = 0
    ja_certos: int = 0
    sem_cargo_de_hierarquia: int = 0
    sem_tag_cadastrada: int = 0
    falhas: int = 0
    exemplos: list[str] = field(default_factory=list)


def cargos_de_hierarquia_do_membro(membro: discord.Member) -> list[str]:
    """Nomes dos cargos do membro que estão na hierarquia hospitalar ou GATE."""
    nomes_de_cargos: list[str] = []
    for cargo in membro.roles:
        if cargo.name in CARGOS_HIERARQUIA:
            nomes_de_cargos.append(cargo.name)
        elif cargo.name in HIERARQUIA_GATE:
            nomes_de_cargos.append(cargo.name)
    return nomes_de_cargos


def apelido_corrigido_do_membro(
    membro: discord.Member,
    id_fivem: str | None = None,
) -> str | None:
    """
    Novo apelido no padrão `[TAG] NOME | ID`, ou None se não há o que espelhar.

    Devolve None quando o membro não tem cargo de hierarquia ou quando nenhum
    desses cargos tem prefixo cadastrado. Nunca inventa tag.
    """
    nomes_de_cargos = cargos_de_hierarquia_do_membro(membro)
    if not nomes_de_cargos:
        return None

    cargo_do_prefixo = escolher_cargo_do_prefixo(nomes_de_cargos)
    if cargo_do_prefixo is None:
        return None

    apelido_atual = membro.nick or membro.display_name or membro.name
    return aplicar_prefixo(apelido_atual, cargo_do_prefixo, id_fivem)


async def carregar_ids_fivem_do_banco() -> dict[int, str]:
    """Mapa discord_id → id_fivem, para devolver o ID a quem perdeu do apelido."""
    try:
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(Usuario.discord_id, Usuario.id_fivem).where(
                    Usuario.id_fivem.is_not(None)
                )
            )
            linhas = resultado.all()
    except (SQLAlchemyError, OSError) as erro:
        registrador.warning("Não consegui ler os IDs FiveM do banco: %s", erro)
        return {}

    ids_por_membro: dict[int, str] = {}
    for linha in linhas:
        id_fivem = str(linha.id_fivem or "").strip()
        if id_fivem:
            ids_por_membro[int(linha.discord_id)] = id_fivem
    return ids_por_membro


def _guardar_exemplo(
    resultado: ResultadoDaVarredura,
    apelido_antigo: str,
    apelido_novo: str,
) -> None:
    """Guarda até cinco mudanças para o card final mostrar o antes e o depois."""
    if len(resultado.exemplos) >= LIMITE_DE_EXEMPLOS:
        return
    resultado.exemplos.append(f"`{apelido_antigo}` → `{apelido_novo}`")


async def _corrigir_um_membro(
    membro: discord.Member,
    motivo: str,
    simular: bool,
    resultado: ResultadoDaVarredura,
    id_fivem: str | None,
) -> None:
    """Aplica a regra em um membro e atualiza a contagem do resultado."""
    nomes_de_cargos = cargos_de_hierarquia_do_membro(membro)
    if not nomes_de_cargos:
        resultado.sem_cargo_de_hierarquia += 1
        return

    apelido_novo = apelido_corrigido_do_membro(membro, id_fivem)
    if apelido_novo is None:
        resultado.sem_tag_cadastrada += 1
        return

    apelido_atual = membro.nick or membro.display_name or membro.name
    if apelido_novo == apelido_atual:
        resultado.ja_certos += 1
        return

    if simular:
        resultado.corrigidos += 1
        _guardar_exemplo(resultado, apelido_atual, apelido_novo)
        return

    try:
        await membro.edit(nick=apelido_novo, reason=motivo)
        resultado.corrigidos += 1
        _guardar_exemplo(resultado, apelido_atual, apelido_novo)
    except discord.Forbidden:
        resultado.falhas += 1
        registrador.warning(
            "Sem permissão para corrigir o apelido de %s (%s).",
            membro.id,
            apelido_novo,
        )
    except discord.HTTPException as erro:
        resultado.falhas += 1
        registrador.warning(
            "Falha ao corrigir o apelido de %s: %s",
            membro.id,
            erro,
        )

    await asyncio.sleep(ATRASO_CORRECAO_APELIDOS_SEGUNDOS)


async def corrigir_apelidos_da_guilda(
    guilda: discord.Guild,
    motivo: str,
    simular: bool = False,
    ao_progredir: Callable[[int, int, ResultadoDaVarredura], Awaitable[None]]
    | None = None,
    ids_fivem: dict[int, str] | None = None,
) -> ResultadoDaVarredura:
    """
    Percorre o servidor e deixa o apelido no padrão do cargo mais alto.

    `simular=True` conta o que mudaria sem editar ninguém. `ao_progredir` é
    chamado de tempos em tempos (e no fim) para atualizar o card de progresso.
    `ids_fivem` evita ir ao banco nos testes; sem ele, o mapa é lido do banco.
    """
    resultado = ResultadoDaVarredura()
    membros = [membro for membro in guilda.members if not membro.bot]
    resultado.total_analisados = len(membros)
    if ids_fivem is None:
        ids_fivem = await carregar_ids_fivem_do_banco()
    ultimo_progresso = time.monotonic()

    for indice, membro in enumerate(membros, start=1):
        await _corrigir_um_membro(
            membro,
            motivo,
            simular,
            resultado,
            ids_fivem.get(membro.id),
        )
        if ao_progredir is None:
            continue
        agora = time.monotonic()
        if agora - ultimo_progresso < SEGUNDOS_ENTRE_PROGRESSOS:
            continue
        await ao_progredir(indice, len(membros), resultado)
        ultimo_progresso = agora

    if ao_progredir is not None:
        await ao_progredir(len(membros), len(membros), resultado)

    registrador.info(
        "Varredura de apelidos: %s analisados, %s corrigidos, %s falhas.",
        resultado.total_analisados,
        resultado.corrigidos,
        resultado.falhas,
    )
    return resultado
