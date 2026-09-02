"""
Agrega produção do hospital para o Responsável HP.

Por que existe
--------------
Na hora de promover ou indicar Responsável de área, o dono precisa ver
tudo que cada membro gerou: laudos, recrutamentos, chamadas, cursos e
horas de plantão. Este serviço junta esses números num só lugar, sem
o dono ter que abrir cinco painéis diferentes.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from sqlalchemy import func, select

from src.config import (
    CARGO_DOUTOR,
    CARGO_INSTRUTOR,
    CARGO_INSTRUTOR_RESGATE,
    CARGO_PARAMEDICO,
    CARGO_PSICOLOGO,
    CARGO_RECRUTADOR,
    CARGOS,
    METAS_POR_CARGO,
)
from src.database.conexao import async_session
from src.database.models import (
    Chamada,
    Laudo,
    Recrutamento,
    SolicitacaoCurso,
)
from src.plantao.ranking_plantao_service import obter_segundos_plantao_totais
from src.promocoes.promocoes_service import (
    id_cargo_por_nome,
    membro_tem_cargo_nome,
)
from src.utils.formatacao import formatar_hms

logger = logging.getLogger(__name__)

# Áreas usadas no ranking de destaque (Responsável de área)
AREAS_PARA_DESTAQUE: list[tuple[str, str]] = [
    ("doutor", CARGO_DOUTOR),
    ("psicologo", CARGO_PSICOLOGO),
    ("recrutador", CARGO_RECRUTADOR),
    ("instrutor", CARGO_INSTRUTOR),
]

# Chave da meta que pesa mais em cada área
META_PRINCIPAL_DA_AREA: dict[str, str] = {
    "doutor": "meta_chamadas",
    "psicologo": "meta_laudos",
    "recrutador": "meta_recrutamentos",
    "instrutor": "meta_cursos_aplicados",
}


async def contar_producao_do_membro(discord_id: int) -> dict[str, int]:
    """
    Conta laudos, recrutamentos aprovados, chamadas e cursos aplicados.

    Usa os mesmos critérios do checklist de promoção, para o número
    bater com o que a trilha exige.
    """
    contagens = {
        "laudos": 0,
        "recrutamentos": 0,
        "chamadas": 0,
        "cursos_aplicados": 0,
        "segundos_plantao": 0,
    }

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.count())
            .select_from(Laudo)
            .where(Laudo.discord_id_psicologo == discord_id)
        )
        contagens["laudos"] = int(resultado.scalar_one() or 0)

        resultado = await sessao.execute(
            select(func.count())
            .select_from(Recrutamento)
            .where(
                Recrutamento.discord_id_recrutador == discord_id,
                Recrutamento.status == "APROVADO",
            )
        )
        contagens["recrutamentos"] = int(resultado.scalar_one() or 0)

        resultado = await sessao.execute(
            select(func.count())
            .select_from(Chamada)
            .where(Chamada.doutor_id == discord_id)
        )
        contagens["chamadas"] = int(resultado.scalar_one() or 0)

        resultado = await sessao.execute(
            select(func.count())
            .select_from(SolicitacaoCurso)
            .where(
                SolicitacaoCurso.instrutor_id == discord_id,
                SolicitacaoCurso.status.in_(
                    ["APROVADO", "CONCLUIDO", "FINALIZADO"]
                ),
            )
        )
        contagens["cursos_aplicados"] = int(resultado.scalar_one() or 0)

    contagens["segundos_plantao"] = int(
        await obter_segundos_plantao_totais(discord_id) or 0
    )
    return contagens


def _nomes_cargo_do_membro(membro: discord.Member) -> list[str]:
    """Lista os nomes de cargo do config que o membro possui agora."""
    nomes: list[str] = []
    for nome_cargo, cargo_id in CARGOS.items():
        if cargo_id is None:
            continue
        if any(cargo.id == int(cargo_id) for cargo in membro.roles):
            nomes.append(nome_cargo)
    return nomes


def _cargo_de_referencia(membro: discord.Member) -> str | None:
    """
    Escolhe o cargo mais alto entre os de área / diretoria para meta.

    Serve só para mostrar a meta de referência no relatório; não decide
    promoção sozinho.
    """
    ordem_preferida = [
        CARGO_INSTRUTOR,
        CARGO_INSTRUTOR_RESGATE,
        CARGO_RECRUTADOR,
        CARGO_PSICOLOGO,
        CARGO_DOUTOR,
        CARGO_PARAMEDICO,
    ]
    for nome in ordem_preferida:
        if membro_tem_cargo_nome(membro, nome):
            return nome
    return None


async def montar_relatorio_do_membro(
    membro: discord.Member,
) -> dict[str, Any]:
    """
    Relatório completo de um membro para o Responsável HP.

    Inclui produção bruta, cargo de referência e quanto falta para a
    meta desse cargo (quando existir em METAS_POR_CARGO).
    """
    producao = await contar_producao_do_membro(membro.id)
    cargo_ref = _cargo_de_referencia(membro)
    metas = dict(METAS_POR_CARGO.get(cargo_ref) or {}) if cargo_ref else {}

    def _linha_meta(chave_meta: str, valor_atual: int) -> dict[str, Any]:
        exigido = int(metas.get(chave_meta) or 0)
        return {
            "atual": valor_atual,
            "meta": exigido,
            "atingiu": (valor_atual >= exigido) if exigido > 0 else True,
        }

    return {
        "discord_id": membro.id,
        "nome": membro.display_name,
        "mencao": membro.mention,
        "cargos": _nomes_cargo_do_membro(membro),
        "cargo_referencia": cargo_ref,
        "segundos_plantao": producao["segundos_plantao"],
        "plantao_formatado": formatar_hms(producao["segundos_plantao"]),
        "laudos": _linha_meta("meta_laudos", producao["laudos"]),
        "recrutamentos": _linha_meta(
            "meta_recrutamentos", producao["recrutamentos"]
        ),
        "chamadas": _linha_meta("meta_chamadas", producao["chamadas"]),
        "cursos_aplicados": _linha_meta(
            "meta_cursos_aplicados", producao["cursos_aplicados"]
        ),
        "meta_plantao_segundos": int(
            metas.get("segundos_minimos_plantao") or 0
        ),
    }


def formatar_relatorio_membro_em_linhas(
    relatorio: dict[str, Any],
) -> list[str]:
    """Transforma o relatório em linhas legíveis para CardView."""
    linhas: list[str] = []
    linhas.append(f"**Membro:** {relatorio['mencao']} (`{relatorio['discord_id']}`)")
    cargos = relatorio.get("cargos") or []
    if cargos:
        lista_cargos = ", ".join(f"`{nome}`" for nome in cargos[:8])
        linhas.append(f"**Cargos:** {lista_cargos}")
    else:
        linhas.append("**Cargos:** nenhum cargo do hospital mapeado")

    cargo_ref = relatorio.get("cargo_referencia")
    if cargo_ref:
        linhas.append(f"**Cargo de referência (metas):** `{cargo_ref}`")

    meta_plantao = int(relatorio.get("meta_plantao_segundos") or 0)
    plantao = relatorio.get("plantao_formatado") or "0s"
    if meta_plantao > 0:
        linhas.append(
            f"**Plantão:** `{plantao}` "
            f"(meta `{formatar_hms(meta_plantao)}`)"
        )
    else:
        linhas.append(f"**Plantão:** `{plantao}`")

    for chave, rotulo in (
        ("laudos", "Laudos"),
        ("recrutamentos", "Recrutamentos"),
        ("chamadas", "Chamadas"),
        ("cursos_aplicados", "Cursos aplicados"),
    ):
        bloco = relatorio.get(chave) or {}
        atual = int(bloco.get("atual") or 0)
        meta = int(bloco.get("meta") or 0)
        if meta > 0:
            marca = "ok" if bloco.get("atingiu") else "falta"
            linhas.append(
                f"**{rotulo}:** `{atual}` / meta `{meta}` ({marca})"
            )
        else:
            linhas.append(f"**{rotulo}:** `{atual}`")

    return linhas


async def listar_membros_da_area(
    guilda: discord.Guild,
    nome_cargo_area: str,
) -> list[discord.Member]:
    """
    Membros online no cache da guilda que possuem o cargo da área.

    Só usa o cache do Discord (sem fetch extra), para não travar o
    comando quando a guilda é grande.
    """
    cargo_id = id_cargo_por_nome(nome_cargo_area)
    if cargo_id is None:
        return []
    cargo = guilda.get_role(int(cargo_id))
    if cargo is None:
        return []
    return [membro for membro in cargo.members if not membro.bot]


async def montar_relatorio_da_area(
    guilda: discord.Guild,
    chave_area: str,
    nome_cargo_area: str,
    *,
    limite_membros: int = 25,
) -> dict[str, Any]:
    """
    Soma a produção de todos os membros da área e lista o top.

    O ranking interno usa a meta principal da área (ex.: laudos para
    psicólogo) para sugerir quem se destacou.
    """
    membros = await listar_membros_da_area(guilda, nome_cargo_area)
    chave_meta = META_PRINCIPAL_DA_AREA.get(chave_area, "meta_chamadas")

    linhas_membros: list[dict[str, Any]] = []
    totais = {
        "laudos": 0,
        "recrutamentos": 0,
        "chamadas": 0,
        "cursos_aplicados": 0,
        "segundos_plantao": 0,
    }

    for membro in membros[: limite_membros]:
        producao = await contar_producao_do_membro(membro.id)
        totais["laudos"] += producao["laudos"]
        totais["recrutamentos"] += producao["recrutamentos"]
        totais["chamadas"] += producao["chamadas"]
        totais["cursos_aplicados"] += producao["cursos_aplicados"]
        totais["segundos_plantao"] += producao["segundos_plantao"]

        mapa_meta = {
            "meta_laudos": producao["laudos"],
            "meta_recrutamentos": producao["recrutamentos"],
            "meta_chamadas": producao["chamadas"],
            "meta_cursos_aplicados": producao["cursos_aplicados"],
        }
        linhas_membros.append(
            {
                "discord_id": membro.id,
                "nome": membro.display_name,
                "mencao": membro.mention,
                "score": int(mapa_meta.get(chave_meta) or 0),
                "producao": producao,
            }
        )

    linhas_membros.sort(key=lambda item: item["score"], reverse=True)

    return {
        "chave_area": chave_area,
        "cargo_area": nome_cargo_area,
        "meta_principal": chave_meta,
        "quantidade_membros": len(membros),
        "membros_listados": len(linhas_membros),
        "totais": totais,
        "ranking": linhas_membros,
    }


def formatar_relatorio_area_em_linhas(
    relatorio: dict[str, Any],
    *,
    top: int = 10,
) -> list[str]:
    """Linhas do card de área para o Responsável HP."""
    totais = relatorio.get("totais") or {}
    linhas: list[str] = []
    linhas.append(f"**Área:** `{relatorio.get('cargo_area')}`")
    linhas.append(
        f"**Membros com o cargo:** `{relatorio.get('quantidade_membros')}` "
        f"(listados `{relatorio.get('membros_listados')}`)"
    )
    linhas.append(
        f"**Totais da área** — "
        f"laudos `{totais.get('laudos', 0)}`, "
        f"recrutamentos `{totais.get('recrutamentos', 0)}`, "
        f"chamadas `{totais.get('chamadas', 0)}`, "
        f"cursos `{totais.get('cursos_aplicados', 0)}`, "
        f"plantão `{formatar_hms(int(totais.get('segundos_plantao') or 0))}`"
    )
    linhas.append(
        f"**Ranking pela meta principal** "
        f"(`{relatorio.get('meta_principal')}`):"
    )

    ranking = list(relatorio.get("ranking") or [])[:top]
    if not ranking:
        linhas.append("Nenhum membro encontrado nesta área.")
        return linhas

    for indice, item in enumerate(ranking, start=1):
        producao = item.get("producao") or {}
        linhas.append(
            f"`{indice}.` {item['mencao']} — "
            f"score `{item['score']}` | "
            f"L `{producao.get('laudos', 0)}` "
            f"R `{producao.get('recrutamentos', 0)}` "
            f"C `{producao.get('chamadas', 0)}` "
            f"Cu `{producao.get('cursos_aplicados', 0)}` | "
            f"`{formatar_hms(int(producao.get('segundos_plantao') or 0))}`"
        )
    return linhas


async def sugerir_destaque_por_area(
    guilda: discord.Guild,
) -> list[dict[str, Any]]:
    """
    Para cada área, aponta o membro com maior score da meta principal.

    Ajuda o Responsável HP a indicar quem vira Responsável Doutor,
    Psicólogo, Recrutamento ou Instrutor.
    """
    sugestoes: list[dict[str, Any]] = []
    for chave_area, nome_cargo in AREAS_PARA_DESTAQUE:
        relatorio = await montar_relatorio_da_area(
            guilda,
            chave_area,
            nome_cargo,
            limite_membros=40,
        )
        ranking = list(relatorio.get("ranking") or [])
        lider = ranking[0] if ranking else None
        sugestoes.append(
            {
                "chave_area": chave_area,
                "cargo_area": nome_cargo,
                "meta_principal": relatorio.get("meta_principal"),
                "lider": lider,
                "quantidade_membros": relatorio.get("quantidade_membros"),
            }
        )
    return sugestoes


def formatar_destaques_em_linhas(
    sugestoes: list[dict[str, Any]],
) -> list[str]:
    """Linhas do card de destaques por área."""
    linhas: list[str] = [
        "Indicação automática pelo maior score da meta principal da área.",
        "Use como apoio — a decisão final continua sendo do Responsável HP.",
        "",
    ]
    for item in sugestoes:
        lider = item.get("lider")
        if lider is None:
            linhas.append(
                f"**{item.get('cargo_area')}** — sem membros com o cargo "
                f"({item.get('quantidade_membros')} pessoas)."
            )
            continue
        producao = lider.get("producao") or {}
        linhas.append(
            f"**{item.get('cargo_area')}** "
            f"(meta `{item.get('meta_principal')}`) → "
            f"{lider['mencao']} score `{lider['score']}` | "
            f"L `{producao.get('laudos', 0)}` "
            f"R `{producao.get('recrutamentos', 0)}` "
            f"C `{producao.get('chamadas', 0)}` "
            f"Cu `{producao.get('cursos_aplicados', 0)}`"
        )
    return linhas
