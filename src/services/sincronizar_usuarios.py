"""Sincroniza a tabela ``usuarios`` com os membros atuais do Discord.

Por que existe
--------------
O sistema novo só criava linha em ``usuarios`` quando alguém passava pelo
recrutamento formal. Muita gente já estava no servidor (cargo HP S・Valley,
Aprovado, hierarquia) sem registro — e várias buscas do bot dependem dessa
tabela.

O que este módulo faz
---------------------
Percorre ``guild.members`` (ignora bots) e para cada pessoa:

1. Garante que existe uma linha em ``usuarios``
2. Atualiza ``nickname_atual`` com o nome de exibição atual
3. Infere ``status`` / ``ja_foi_aprovado`` a partir dos cargos Discord
4. Preenche ``id_fivem`` se estiver vazio (apelido → EstadoPlantao → Recrutamento)

Regras de cuidado
-----------------
- Nunca **rebaixa** quem já está ``APROVADO`` no banco
- ``ja_foi_aprovado`` uma vez ``True`` permanece ``True``
- ``id_fivem`` só é preenchido se estiver vazio (não sobrescreve dado existente)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import discord
from sqlalchemy import select

from src.config import CARGOS, CARGOS_HIERARQUIA
from src.database.connection import async_session
from src.database.models import EstadoPlantao, Recrutamento, Usuario
from src.plantao.ocr.scraping_membros import extrair_id_do_apelido


# ── Inferência de status a partir dos cargos ──────────────────────────────


def _ids_cargos_aprovados() -> set[int]:
    """Cargos que indicam membro já aprovado / da hierarquia médica."""
    nomes = {
        "HP S・Valley",
        "Aprovado",
        *CARGOS_HIERARQUIA,
    }
    return {CARGOS[nome] for nome in nomes if nome in CARGOS}


def _inferir_status_pelos_cargos(membro: discord.Member) -> tuple[str, bool]:
    """Devolve (status, ja_foi_aprovado) olhando só os cargos Discord.

    Prioridade:
      HP S・Valley / Aprovado / hierarquia  → APROVADO
      ESTUDANTE ou PROVA                   → ESTUDANTE
      resto                                → VISITANTE
    """
    ids_do_membro = {cargo.id for cargo in membro.roles}

    if ids_do_membro & _ids_cargos_aprovados():
        return "APROVADO", True

    id_estudante = CARGOS.get("ESTUDANTE")
    id_prova = CARGOS.get("PROVA")
    if (id_estudante and id_estudante in ids_do_membro) or (
        id_prova and id_prova in ids_do_membro
    ):
        return "ESTUDANTE", False

    return "VISITANTE", False


def _escolher_status_final(
    status_banco: str | None,
    ja_foi_aprovado_banco: bool,
    status_inferido: str,
    aprovado_inferido: bool,
) -> tuple[str, bool]:
    """Não rebaixa APROVADO; preserva ja_foi_aprovado se já for True."""
    ja_foi = ja_foi_aprovado_banco or aprovado_inferido

    if status_banco == "APROVADO" or status_inferido == "APROVADO":
        return "APROVADO", True

    # Banco tinha algo "maior" que VISITANTE? mantém o melhor entre os dois
    ordem = {"VISITANTE": 0, "ESTUDANTE": 1, "APROVADO": 2}
    atual = ordem.get(status_banco or "VISITANTE", 0)
    novo = ordem.get(status_inferido, 0)
    if novo >= atual:
        return status_inferido, ja_foi
    return status_banco or "VISITANTE", ja_foi


# ── Busca de id_fivem auxiliar ────────────────────────────────────────────


async def _id_fivem_do_estado_plantao(discord_id: int) -> str | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao.id_fivem).where(
                EstadoPlantao.discord_id == discord_id,
                EstadoPlantao.id_fivem.is_not(None),
            )
        )
        valor = resultado.scalar_one_or_none()
        return str(valor) if valor else None


async def _id_fivem_do_recrutamento(discord_id: int) -> str | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Recrutamento.id_fivem)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.id_fivem.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        valor = resultado.scalar_one_or_none()
        return str(valor) if valor else None


async def _resolver_id_fivem(
    membro: discord.Member, id_ja_salvo: str | None
) -> str | None:
    """Só preenche se ainda não houver id no banco."""
    if id_ja_salvo:
        return id_ja_salvo

    nome_exibido = membro.nick or membro.display_name or membro.name
    do_apelido = extrair_id_do_apelido(nome_exibido)
    if do_apelido:
        return do_apelido

    do_plantao = await _id_fivem_do_estado_plantao(membro.id)
    if do_plantao:
        return do_plantao

    return await _id_fivem_do_recrutamento(membro.id)


# ── Resultado da sincronização ────────────────────────────────────────────


@dataclass
class ResultadoSincronizacaoUsuarios:
    """Contadores para o card de resposta do comando."""

    total_membros: int = 0
    criados: int = 0
    atualizados: int = 0
    inalterados: int = 0
    com_id_fivem_preenchido: int = 0
    aprovados: int = 0
    estudantes: int = 0
    visitantes: int = 0
    erros: list[str] = field(default_factory=list)

    def linhas_resumo(self) -> list[str]:
        return [
            f"Membros humanos no servidor: **{self.total_membros}**",
            f"Criados no banco: **{self.criados}**",
            f"Atualizados: **{self.atualizados}**",
            f"Já estavam corretos: **{self.inalterados}**",
            f"id_fivem preenchido nesta rodada: **{self.com_id_fivem_preenchido}**",
            f"Status APROVADO: **{self.aprovados}**",
            f"Status ESTUDANTE: **{self.estudantes}**",
            f"Status VISITANTE: **{self.visitantes}**",
        ]


# ── Sincronização principal ───────────────────────────────────────────────


async def sincronizar_usuarios_do_servidor(
    guild: discord.Guild,
) -> ResultadoSincronizacaoUsuarios:
    """Varre o servidor e alimenta/atualiza a tabela ``usuarios``."""
    resultado = ResultadoSincronizacaoUsuarios()

    membros_humanos = [m for m in guild.members if not m.bot]
    resultado.total_membros = len(membros_humanos)

    for membro in membros_humanos:
        try:
            mudou = await _sincronizar_um_membro(membro, resultado)
            if mudou is True:
                resultado.atualizados += 1
            elif mudou is False:
                resultado.inalterados += 1
            # mudou is None → foi criado (já contado em criados)
        except Exception as erro:
            resultado.erros.append(f"{membro.id}: {erro}")

    return resultado


async def _sincronizar_um_membro(
    membro: discord.Member,
    resultado: ResultadoSincronizacaoUsuarios,
) -> bool | None:
    """
    Returns
    -------
    True  → registro existia e foi alterado
    False → registro existia e nada mudou
    None  → registro foi criado agora
    """
    status_inferido, aprovado_inferido = _inferir_status_pelos_cargos(membro)
    nickname = (membro.nick or membro.display_name or membro.name)[:100]

    # Lê o estado atual (sessão curta) — evita sessão aberta durante outras queries
    async with async_session() as sessao:
        consulta = await sessao.execute(
            select(Usuario).where(Usuario.discord_id == membro.id)
        )
        usuario_existente = consulta.scalar_one_or_none()
        era_novo = usuario_existente is None
        status_banco = usuario_existente.status if usuario_existente else None
        ja_foi_banco = (
            bool(usuario_existente.ja_foi_aprovado) if usuario_existente else False
        )
        id_fivem_antes = usuario_existente.id_fivem if usuario_existente else None
        nickname_antes = (
            usuario_existente.nickname_atual if usuario_existente else None
        )

    status_final, ja_foi_final = _escolher_status_final(
        status_banco=status_banco,
        ja_foi_aprovado_banco=ja_foi_banco,
        status_inferido=status_inferido,
        aprovado_inferido=aprovado_inferido,
    )
    id_fivem = await _resolver_id_fivem(membro, id_fivem_antes)

    alterou = era_novo or (
        nickname_antes != nickname
        or status_banco != status_final
        or ja_foi_banco != ja_foi_final
        or (not id_fivem_antes and bool(id_fivem))
    )

    async with async_session() as sessao:
        if era_novo:
            usuario = Usuario(discord_id=membro.id)
            sessao.add(usuario)
            resultado.criados += 1
        else:
            consulta = await sessao.execute(
                select(Usuario).where(Usuario.discord_id == membro.id)
            )
            usuario = consulta.scalar_one()

        usuario.nickname_atual = nickname
        usuario.status = status_final
        usuario.ja_foi_aprovado = ja_foi_final
        if id_fivem and not usuario.id_fivem:
            usuario.id_fivem = id_fivem
            resultado.com_id_fivem_preenchido += 1

        await sessao.commit()

    if status_final == "APROVADO":
        resultado.aprovados += 1
    elif status_final == "ESTUDANTE":
        resultado.estudantes += 1
    else:
        resultado.visitantes += 1

    if era_novo:
        return None
    return alterou


async def garantir_usuario_basico(membro: discord.Member) -> Usuario:
    """Garante uma linha mínima em ``usuarios`` (ex.: no on_member_join).

    Não faz a varredura completa — só cria se não existir e preenche o básico.
    """
    async with async_session() as sessao:
        consulta = await sessao.execute(
            select(Usuario).where(Usuario.discord_id == membro.id)
        )
        usuario = consulta.scalar_one_or_none()
        if usuario is not None:
            return usuario

        status, ja_foi = _inferir_status_pelos_cargos(membro)
        nickname = (membro.nick or membro.display_name or membro.name)[:100]
        id_fivem = extrair_id_do_apelido(nickname)

        usuario = Usuario(
            discord_id=membro.id,
            nickname_atual=nickname,
            status=status,
            ja_foi_aprovado=ja_foi,
            id_fivem=id_fivem,
        )
        sessao.add(usuario)
        await sessao.commit()
        await sessao.refresh(usuario)
        return usuario
