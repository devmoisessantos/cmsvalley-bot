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
from sqlalchemy import func, or_, select

from src.config import CARGOS
from src.cursos.cursos_service import parse_chaves_json
from src.database.conexao import async_session
from src.database.models import (
    Chamada,
    Laudo,
    Recrutamento,
    SolicitacaoCurso,
    Ticket,
)
from src.plantao.ranking_plantao_service import obter_segundos_plantao_totais
from src.promocoes.promocoes_service import (
    cargo_mais_alto_do_membro,
    id_cargo_por_nome,
    listar_cargos_destino_para_membro,
    membro_tem_cargo_nome,
    obter_trilha_por_destino_e_origem,
)
from src.utils.ephemeral_botoes import linha_ephemeral
from src.utils.error_handling import LoggingViewMixin
from src.utils.formatacao import formatar_hms

try:
    from src.config import METAS_POR_CARGO
except ImportError:
    METAS_POR_CARGO = {}

logger = logging.getLogger(__name__)

# Nomes oficiais — iguais a CARGOS_HIERARQUIA / config
CARGO_PARAMEDICO = "🚑・Paramédico"
CARGO_DOUTOR = "🥼・Doutor"
CARGO_PSICOLOGO = "🩺・Psicólogo"
CARGO_RECRUTADOR = "✈️・Recrutador"
CARGO_INSTRUTOR = "🥼・Instrutor"
CARGO_INSTRUTOR_RESGATE = "🚑・Instrutor Resgate"

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
    Conta laudos, recrutamentos aprovados, chamadas, cursos aplicados e
    tickets finalizados.

    Mesmos critérios do checklist de promoção
    (``_contar_metas_do_membro`` em promocoes_service):

    - Cursos: cada **chave** do pacote (``chaves_cursos_json``), não o
      pedido. Ex.: pacote com 6 cursos = 6, não 1.
    - Instrutor: ``instrutor_id`` ou ``aplicado_por`` (legado / decisão).
    - Status: APROVADO, CONCLUIDO, FINALIZADO.
    - Tickets: ``staff_finalizou_id`` + status ``finalizado``.
    """
    contagens = {
        "laudos": 0,
        "recrutamentos": 0,
        "chamadas": 0,
        "cursos_aplicados": 0,
        "tickets": 0,
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

        # Tickets finalizados pela staff (conta para meta de tickets)
        resultado = await sessao.execute(
            select(func.count())
            .select_from(Ticket)
            .where(
                Ticket.staff_finalizou_id == discord_id,
                Ticket.status == "finalizado",
            )
        )
        contagens["tickets"] = int(resultado.scalar_one() or 0)

        # Mesma regra de promoções: conta chaves do pacote, não o pedido.
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(
                SolicitacaoCurso.status.in_(["APROVADO", "CONCLUIDO", "FINALIZADO"]),
                or_(
                    SolicitacaoCurso.instrutor_id == discord_id,
                    SolicitacaoCurso.aplicado_por == discord_id,
                ),
            )
        )
        total_cursos = 0
        for registro in resultado.scalars().all():
            chaves = parse_chaves_json(
                registro.chaves_cursos_json,
                registro.chave_curso,
            )
            total_cursos += len(chaves)
        contagens["cursos_aplicados"] = total_cursos

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


def _cargos_acima_na_hierarquia(membro: discord.Member) -> list[str]:
    """
    Cargos da hierarquia estritamente acima do mais alto do membro.

    Só inclui nomes que existem em METAS_POR_CARGO (úteis como referência
    de meta). Ordem: do mais próximo para o mais alto.
    """
    try:
        from src.config import CARGOS_HIERARQUIA
    except ImportError:
        return []

    cargo_atual = cargo_mais_alto_do_membro(membro)
    if not cargo_atual or cargo_atual not in CARGOS_HIERARQUIA:
        return []

    indice_atual = CARGOS_HIERARQUIA.index(cargo_atual)
    # Hierarquia é do mais alto (0) para o mais baixo.
    acima = CARGOS_HIERARQUIA[:indice_atual]
    # Reverte para ficar do mais próximo (logo acima) ao mais alto.
    acima_proximo_primeiro = list(reversed(acima))

    resultado: list[str] = []
    for nome in acima_proximo_primeiro:
        if nome not in METAS_POR_CARGO:
            continue
        if membro_tem_cargo_nome(membro, nome):
            continue
        resultado.append(nome)
    return resultado


def listar_cargos_referencia_para_membro(membro: discord.Member) -> list[str]:
    """
    Cargos acima do atual do membro — candidatos a cargo de referência.

    1) Destinos das trilhas de promoção a partir do cargo mais alto
       (próximo passo formal de carreira) — primeiro.
    2) Instrutor Resgate: trata como Instrutor para achar destinos.
    3) Sempre completa com cargos da hierarquia acima (com meta), para
       o select listar **todos** os degraus acima, não só o primeiro.
    """
    vistos: set[str] = set()
    lista: list[str] = []

    for nome in listar_cargos_destino_para_membro(membro):
        if nome not in vistos:
            vistos.add(nome)
            lista.append(nome)

    # Instrutor Resgate não entra em AREAS_MEDICAS; trata como Instrutor
    # para achar trilhas de área → área / Supervisor.
    if membro_tem_cargo_nome(membro, CARGO_INSTRUTOR_RESGATE):
        try:
            from src.config import TRILHAS_PROMOCAO
        except ImportError:
            TRILHAS_PROMOCAO = []
        for trilha in TRILHAS_PROMOCAO:
            origens = list(trilha.get("de_cargos") or [])
            de_cargo = trilha.get("de_cargo") or ""
            if de_cargo:
                origens.append(de_cargo)
            if (
                CARGO_INSTRUTOR not in origens
                and CARGO_INSTRUTOR_RESGATE not in origens
            ):
                continue
            destino = trilha.get("para_cargo") or ""
            if not destino or membro_tem_cargo_nome(membro, destino):
                continue
            if destino not in vistos:
                vistos.add(destino)
                lista.append(destino)

    # Sempre mescla hierarquia acima (próximo → mais alto)
    for nome in _cargos_acima_na_hierarquia(membro):
        if nome not in vistos:
            vistos.add(nome)
            lista.append(nome)

    return lista


def _metas_para_cargo_referencia(
    membro: discord.Member,
    cargo_ref: str | None,
) -> dict[str, Any]:
    """
    Metas do cargo de referência.

    Preferência:
    1) Trilha real até o destino (plantão acumulado + produção da trilha)
    2) Fallback: METAS_POR_CARGO do cargo
    """
    if not cargo_ref:
        return {}

    trilha = obter_trilha_por_destino_e_origem(cargo_ref, membro)
    if trilha:
        return {
            "segundos_minimos_plantao": int(
                trilha.get("segundos_minimos_plantao") or 0
            ),
            "meta_laudos": int(trilha.get("meta_laudos") or 0),
            "meta_recrutamentos": int(trilha.get("meta_recrutamentos") or 0),
            "meta_chamadas": int(trilha.get("meta_chamadas") or 0),
            "meta_cursos_aplicados": int(trilha.get("meta_cursos_aplicados") or 0),
            "meta_tickets": int(trilha.get("meta_tickets") or 0),
            "fonte": "trilha",
            "trilha_rotulo": trilha.get("rotulo") or cargo_ref,
        }

    base = dict(METAS_POR_CARGO.get(cargo_ref) or {})
    base["fonte"] = "metas_por_cargo"
    base["trilha_rotulo"] = None
    return base


def _cargo_de_referencia(
    membro: discord.Member,
    cargo_escolhido: str | None = None,
) -> str | None:
    """
    Cargo de referência = próximo cargo acima (destino de promoção).

    Se ``cargo_escolhido`` for um destino válido para o membro, usa ele.
    Caso contrário, usa o primeiro destino disponível.
    """
    destinos = listar_cargos_referencia_para_membro(membro)
    if cargo_escolhido and cargo_escolhido in destinos:
        return cargo_escolhido
    if destinos:
        return destinos[0]
    return None


async def montar_relatorio_do_membro(
    membro: discord.Member,
    cargo_referencia: str | None = None,
) -> dict[str, Any]:
    """
    Relatório completo de um membro para o Responsável HP.

    Inclui produção bruta, cargo de referência (próximo cargo de promoção)
    e quanto falta para a meta desse cargo (quando existir em
    METAS_POR_CARGO).
    """
    producao = await contar_producao_do_membro(membro.id)
    destinos = listar_cargos_referencia_para_membro(membro)
    cargo_ref = _cargo_de_referencia(membro, cargo_referencia)
    cargo_atual = cargo_mais_alto_do_membro(membro)
    metas = _metas_para_cargo_referencia(membro, cargo_ref)

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
        "avatar_url": (membro.display_avatar.url if membro.display_avatar else None),
        "cargos": _nomes_cargo_do_membro(membro),
        "cargo_atual": cargo_atual,
        "cargo_referencia": cargo_ref,
        "cargos_referencia_disponiveis": destinos,
        "metas_fonte": metas.get("fonte"),
        "trilha_rotulo": metas.get("trilha_rotulo"),
        "segundos_plantao": producao["segundos_plantao"],
        "plantao_formatado": formatar_hms(producao["segundos_plantao"]),
        "laudos": _linha_meta("meta_laudos", producao["laudos"]),
        "recrutamentos": _linha_meta("meta_recrutamentos", producao["recrutamentos"]),
        "chamadas": _linha_meta("meta_chamadas", producao["chamadas"]),
        "cursos_aplicados": _linha_meta(
            "meta_cursos_aplicados", producao["cursos_aplicados"]
        ),
        "tickets": _linha_meta("meta_tickets", producao["tickets"]),
        "meta_plantao_segundos": int(metas.get("segundos_minimos_plantao") or 0),
    }


def _linha_producao_texto(
    rotulo: str,
    atual: int,
    meta: int,
    atingiu: bool,
) -> str:
    """Uma linha de produção com status visual."""
    if meta > 0:
        marca = "✅" if atingiu else "❌"
        return f"{marca} **{rotulo}:** `{atual}` / meta `{meta}`"
    return f"• **{rotulo}:** `{atual}`"


def formatar_relatorio_membro_em_linhas(
    relatorio: dict[str, Any],
) -> list[str]:
    """Transforma o relatório em linhas legíveis (fallback texto)."""
    linhas: list[str] = []
    linhas.append(f"**Membro:** {relatorio['mencao']} (`{relatorio['discord_id']}`)")
    cargos = list(relatorio.get("cargos") or [])
    if cargos:
        linhas.append("**Cargos:**")
        for i in range(0, len(cargos), 4):
            fatia = cargos[i : i + 4]
            linhas.append(", ".join(f"`{nome}`" for nome in fatia))
    else:
        linhas.append("**Cargos:** nenhum cargo do hospital mapeado")

    cargo_atual = relatorio.get("cargo_atual")
    if cargo_atual:
        linhas.append(f"**Cargo atual (mais alto):** `{cargo_atual}`")

    cargo_ref = relatorio.get("cargo_referencia")
    if cargo_ref:
        linhas.append(f"**Cargo de referência (próximo / metas):** `{cargo_ref}`")
    else:
        linhas.append("**Cargo de referência:** nenhum destino de promoção disponível")

    meta_plantao = int(relatorio.get("meta_plantao_segundos") or 0)
    plantao = relatorio.get("plantao_formatado") or "0s"
    segundos = int(relatorio.get("segundos_plantao") or 0)
    if meta_plantao > 0:
        ok_plantao = segundos >= meta_plantao
        marca = "✅" if ok_plantao else "❌"
        linhas.append(
            f"{marca} **Plantão:** `{plantao}` (meta `{formatar_hms(meta_plantao)}`)"
        )
    else:
        linhas.append(f"• **Plantão:** `{plantao}`")

    for chave, rotulo in (
        ("laudos", "Laudos"),
        ("recrutamentos", "Recrutamentos"),
        ("chamadas", "Chamadas"),
        ("cursos_aplicados", "Cursos aplicados"),
        ("tickets", "Tickets finalizados"),
    ):
        bloco = relatorio.get(chave) or {}
        atual = int(bloco.get("atual") or 0)
        meta = int(bloco.get("meta") or 0)
        atingiu = bool(bloco.get("atingiu"))
        linhas.append(_linha_producao_texto(rotulo, atual, meta, atingiu))

    return linhas


def _formatar_cargos_em_linhas(
    cargos: list[str],
    *,
    por_linha: int = 4,
) -> str:
    """
    Lista todos os cargos, quebrando em linhas de ``por_linha`` itens.

    Ex.: 4–5 cargos por linha para não estourar a largura do card.
    """
    if not cargos:
        return "_nenhum cargo do hospital mapeado_"
    linhas: list[str] = []
    for i in range(0, len(cargos), por_linha):
        fatia = cargos[i : i + por_linha]
        linhas.append(", ".join(f"`{n}`" for n in fatia))
    return "\n".join(linhas)


def montar_texto_corpo_relatorio(relatorio: dict[str, Any]) -> str:
    """Corpo formatado do card de avaliação (sem cabeçalho)."""
    partes: list[str] = []

    cargo_atual = relatorio.get("cargo_atual")
    if cargo_atual:
        partes.append(f"`📌` **Cargo atual:** `{cargo_atual}`")
    else:
        partes.append("`📌` **Cargo atual:** _não mapeado na hierarquia_")

    cargo_ref = relatorio.get("cargo_referencia")
    if cargo_ref:
        trilha_rotulo = relatorio.get("trilha_rotulo")
        fonte = relatorio.get("metas_fonte")
        extra = ""
        if trilha_rotulo:
            extra = f" · trilha `{trilha_rotulo}`"
        elif fonte:
            extra = f" · metas `{fonte}`"
        partes.append(f"`🎯` **Referência (próximo):** `{cargo_ref}`{extra}")
    else:
        partes.append("`🎯` **Referência:** _sem destino de promoção disponível_")

    cargos = list(relatorio.get("cargos") or [])
    partes.append("`🏷️` **Cargos:**")
    partes.append(_formatar_cargos_em_linhas(cargos, por_linha=4))

    partes.append("")
    partes.append("### Produção × metas")

    meta_plantao = int(relatorio.get("meta_plantao_segundos") or 0)
    plantao = relatorio.get("plantao_formatado") or "0s"
    segundos = int(relatorio.get("segundos_plantao") or 0)
    if meta_plantao > 0:
        marca = "✅" if segundos >= meta_plantao else "❌"
        partes.append(
            f"{marca} **Plantão:** `{plantao}` / meta `{formatar_hms(meta_plantao)}`"
        )
    else:
        partes.append(f"• **Plantão:** `{plantao}`")

    for chave, rotulo in (
        ("laudos", "Laudos"),
        ("recrutamentos", "Recrutamentos"),
        ("chamadas", "Chamadas"),
        ("cursos_aplicados", "Cursos aplicados"),
        ("tickets", "Tickets finalizados"),
    ):
        bloco = relatorio.get(chave) or {}
        atual = int(bloco.get("atual") or 0)
        meta = int(bloco.get("meta") or 0)
        atingiu = bool(bloco.get("atingiu"))
        partes.append(_linha_producao_texto(rotulo, atual, meta, atingiu))

    return "\n".join(partes)


class RelatorioMembroHpView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Card de avaliação HP de um membro.

    Visual no padrão do gerenciar-membros: thumbnail do avatar,
    corpo estruturado, select para trocar o cargo de referência
    (próximos cargos acima do atual) e botão Fechar (ephemeral).
    timeout=None — a mensagem some pelo delay do helper ou pelo Fechar.
    """

    def __init__(
        self,
        membro: discord.Member,
        relatorio: dict[str, Any],
    ):
        super().__init__(timeout=None)
        self.membro = membro
        self.relatorio = relatorio
        self._montar()

    def _montar(self) -> None:
        self.clear_items()
        rel = self.relatorio
        nome = rel.get("nome") or self.membro.display_name
        mencao = rel.get("mencao") or self.membro.mention
        avatar_url = rel.get("avatar_url") or (
            self.membro.display_avatar.url if self.membro.display_avatar else None
        )

        cabecalho = (
            f"# 📋 Avaliação HP — {nome}\n> {mencao} · `{rel.get('discord_id')}`"
        )

        componentes: list = []
        if avatar_url:
            componentes.append(
                discord.ui.Section(
                    cabecalho,
                    accessory=discord.ui.Thumbnail(avatar_url),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(cabecalho))

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(discord.ui.TextDisplay(montar_texto_corpo_relatorio(rel)))
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        destinos = list(rel.get("cargos_referencia_disponiveis") or [])
        cargo_ref = rel.get("cargo_referencia")

        if destinos:
            opcoes = []
            for nome_cargo in destinos[:25]:
                opcoes.append(
                    discord.SelectOption(
                        label=nome_cargo[:100],
                        value=nome_cargo,
                        default=(nome_cargo == cargo_ref),
                        description="Metas deste cargo de promoção"[:100],
                    )
                )
            linha = discord.ui.ActionRow()
            seletor = discord.ui.Select(
                placeholder="Cargo de referência (próximo)…",
                options=opcoes,
                min_values=1,
                max_values=1,
            )
            seletor.callback = self._trocar_referencia
            linha.add_item(seletor)
            componentes.append(linha)
            componentes.append(
                discord.ui.TextDisplay(
                    "-# Selecione o **próximo cargo** para recalcular as metas."
                )
            )
        else:
            componentes.append(
                discord.ui.TextDisplay(
                    "-# Nenhum destino de promoção cadastrado a partir "
                    "do cargo atual deste membro."
                )
            )

        # Fechar ephemeral (helper padrão)
        componentes.append(linha_ephemeral(fechar=True))

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _trocar_referencia(self, interacao: discord.Interaction) -> None:
        """Recalcula o relatório com o cargo de referência escolhido."""
        valores = list(interacao.data.get("values") or []) if interacao.data else []
        escolhido = valores[0] if valores else None
        if not escolhido:
            await interacao.response.defer()
            return

        await interacao.response.defer()
        try:
            novo = await montar_relatorio_do_membro(
                self.membro,
                cargo_referencia=escolhido,
            )
            self.relatorio = novo
            self._montar()
            await interacao.edit_original_response(view=self)
        except Exception as erro:
            logger.exception(
                "Falha ao trocar cargo de referência HP para %s: %s",
                self.membro.id,
                erro,
            )
            # Mantém a view atual; não quebra a mensagem.


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

    for membro in membros[:limite_membros]:
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
        f"**Ranking pela meta principal** (`{relatorio.get('meta_principal')}`):"
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
