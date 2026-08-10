"""Regras de negócio do monitoramento de baú."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from sqlalchemy import and_, func, select

from src.bau.bau_parse import LogBauParseado, parsear_mensagem_log_bau
from src.config import (
    ALIASES_ITENS_BAU,
    HORAS_RESET_CICLO_BAU,
    LIMITES_BAU_CAMADA_1,
    LIMITES_BAU_CAMADA_2,
    PRAZO_DEVOLUCAO_BAU_MINUTOS,
    TIMEZONE_LOCAL,
    VERBAIS_PARA_ADV1_BAU,
)
from src.database.connection import async_session
from src.database.models import (
    AdvertenciaVerbalBau,
    CasoBau,
    ContadorItemBau,
    agora,
)

# Lock por id_fivem para não corromper contador em logs simultâneos
_travas_por_id: dict[str, asyncio.Lock] = {}


def _trava_do_id(id_fivem: str) -> asyncio.Lock:
    if id_fivem not in _travas_por_id:
        _travas_por_id[id_fivem] = asyncio.Lock()
    return _travas_por_id[id_fivem]


def chave_ciclo_atual(referencia: datetime | None = None) -> str:
    """Ex.: 2026-08-10_11 — ciclo vigente nas horas 0/11/17."""
    fuso = ZoneInfo(TIMEZONE_LOCAL)
    momento = (referencia or datetime.now(timezone.utc)).astimezone(fuso)
    horas = sorted(HORAS_RESET_CICLO_BAU)
    hora_ciclo = horas[0]
    for hora in horas:
        if momento.hour >= hora:
            hora_ciclo = hora
    return f"{momento.year:04d}-{momento.month:02d}-{momento.day:02d}_{hora_ciclo:02d}"


async def resolver_discord_id(id_fivem: str) -> int | None:
    """Resolve Discord ID a partir do passaporte FiveM (Usuario → Plantão → Recrutamento)."""
    from src.database.models import EstadoPlantao, Recrutamento, Usuario

    id_texto = str(id_fivem).strip()
    async with async_session() as sessao:
        resultado_usuario = await sessao.execute(
            select(Usuario.discord_id).where(Usuario.id_fivem == id_texto).limit(1)
        )
        discord_id = resultado_usuario.scalar_one_or_none()
        if discord_id:
            return int(discord_id)

        resultado_plantao = await sessao.execute(
            select(EstadoPlantao.discord_id)
            .where(EstadoPlantao.id_fivem == id_texto)
            .limit(1)
        )
        discord_id = resultado_plantao.scalar_one_or_none()
        if discord_id:
            return int(discord_id)

        resultado_rec = await sessao.execute(
            select(Recrutamento.discord_id_candidato)
            .where(
                Recrutamento.id_fivem == id_texto,
                Recrutamento.discord_id_candidato.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        discord_id = resultado_rec.scalar_one_or_none()
        if discord_id:
            return int(discord_id)
    return None


async def aplicar_movimento_item(
    *,
    id_fivem: str,
    nome_cidade: str,
    item_canonico: str,
    delta: int,
    ciclo: str,
) -> int:
    """Soma (ou subtrai) no contador do ciclo. Retorna quantidade líquida após update."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(ContadorItemBau).where(
                ContadorItemBau.id_fivem == id_fivem,
                ContadorItemBau.item_canonico == item_canonico,
                ContadorItemBau.ciclo_chave == ciclo,
            )
        )
        contador = resultado.scalar_one_or_none()
        if contador is None:
            contador = ContadorItemBau(
                id_fivem=id_fivem,
                nome_cidade=nome_cidade,
                item_canonico=item_canonico,
                quantidade=0,
                ciclo_chave=ciclo,
            )
            sessao.add(contador)
            await sessao.flush()

        nova = contador.quantidade + delta
        if nova < 0:
            nova = 0
        contador.quantidade = nova
        contador.nome_cidade = nome_cidade or contador.nome_cidade
        contador.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()
        return nova


async def buscar_caso_aberto(id_fivem: str, item_canonico: str) -> CasoBau | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(CasoBau)
            .where(
                CasoBau.id_fivem == id_fivem,
                CasoBau.item_canonico == item_canonico,
                CasoBau.status.in_(("AGUARDANDO", "GRAVE")),
            )
            .order_by(CasoBau.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def abrir_ou_atualizar_caso(
    *,
    id_fivem: str,
    nome_cidade: str,
    discord_id: int | None,
    item_canonico: str,
    quantidade: int,
    e_grave: bool,
) -> tuple[CasoBau, bool]:
    """
    Retorna (caso, criado_agora).
    Se já existe caso aberto do mesmo ID+item, só atualiza quantidade/grave.
    """
    existente = await buscar_caso_aberto(id_fivem, item_canonico)
    prazo = datetime.now(timezone.utc) + timedelta(minutes=PRAZO_DEVOLUCAO_BAU_MINUTOS)

    async with async_session() as sessao:
        if existente is not None:
            caso = await sessao.get(CasoBau, existente.id)
            caso.quantidade_atual = quantidade
            caso.nome_cidade = nome_cidade or caso.nome_cidade
            if discord_id:
                caso.discord_id = discord_id
            if e_grave:
                caso.e_grave = True
                caso.status = "GRAVE"
            caso.atualizado_em = datetime.now(timezone.utc)
            await sessao.commit()
            await sessao.refresh(caso)
            return caso, False

        caso = CasoBau(
            id_fivem=id_fivem,
            nome_cidade=nome_cidade,
            discord_id=discord_id,
            item_canonico=item_canonico,
            quantidade_atual=quantidade,
            status="GRAVE" if e_grave else "AGUARDANDO",
            e_grave=e_grave,
            expira_em=prazo,
            criado_em=datetime.now(timezone.utc),
            atualizado_em=datetime.now(timezone.utc),
        )
        sessao.add(caso)
        await sessao.commit()
        await sessao.refresh(caso)
        return caso, True


async def marcar_dm_resultado(caso_id: int, *, falhou: bool) -> None:
    async with async_session() as sessao:
        caso = await sessao.get(CasoBau, caso_id)
        if caso is None:
            return
        caso.dm_falhou = falhou
        caso.dm_enviada_em = datetime.now(timezone.utc)
        caso.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()


async def salvar_message_alerta(caso_id: int, message_id: int) -> None:
    async with async_session() as sessao:
        caso = await sessao.get(CasoBau, caso_id)
        if caso is None:
            return
        caso.canal_alerta_message_id = message_id
        await sessao.commit()


async def resolver_caso(
    caso_id: int,
    *,
    por_discord_id: int | None,
    status: str = "RESOLVIDO",
    motivo_ignore: str | None = None,
) -> CasoBau | None:
    async with async_session() as sessao:
        caso = await sessao.get(CasoBau, caso_id)
        if caso is None:
            return None
        caso.status = status
        caso.resolvido_por = por_discord_id
        caso.resolvido_em = datetime.now(timezone.utc)
        caso.atualizado_em = datetime.now(timezone.utc)
        if motivo_ignore:
            caso.motivo_ignore = motivo_ignore[:500]
        await sessao.commit()
        await sessao.refresh(caso)
        return caso


async def contar_verbais(id_fivem: str) -> int:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.count())
            .select_from(AdvertenciaVerbalBau)
            .where(
                AdvertenciaVerbalBau.id_fivem == id_fivem,
                AdvertenciaVerbalBau.tipo == "VERBAL",
            )
        )
        return int(resultado.scalar_one() or 0)


async def aplicar_verbal_automatica(caso: CasoBau) -> tuple[str, AdvertenciaVerbalBau]:
    """
    Aplica verbal. Na 3ª, registra escalada ADV1 e retorna tipo aplicado.
    Retorna (tipo_aplicado, registro).
    """
    qtd_verbais = await contar_verbais(caso.id_fivem)
    tipo = "VERBAL"
    motivo = (
        f"Excesso de baú — item `{caso.item_canonico}` "
        f"qtd {caso.quantidade_atual} — prazo de devolução esgotado."
    )

    if qtd_verbais + 1 >= VERBAIS_PARA_ADV1_BAU:
        tipo = "ADV1_ESCALADA"
        motivo = (
            f"3ª advertência verbal de baú (item `{caso.item_canonico}`). "
            "Escalada automática para ADV 1 — diretoria deve avaliar."
        )

    async with async_session() as sessao:
        registro = AdvertenciaVerbalBau(
            id_fivem=caso.id_fivem,
            discord_id=caso.discord_id,
            nome_cidade=caso.nome_cidade,
            caso_id=caso.id,
            item_canonico=caso.item_canonico,
            motivo=motivo[:500],
            tipo=tipo,
            automatica=True,
            criada_em=datetime.now(timezone.utc),
        )
        sessao.add(registro)
        caso_db = await sessao.get(CasoBau, caso.id)
        if caso_db is not None:
            caso_db.status = "PUNIDO"
            caso_db.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()
        await sessao.refresh(registro)
        return tipo, registro


async def listar_casos_expirados() -> list[CasoBau]:
    agora_utc = datetime.now(timezone.utc)
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(CasoBau).where(
                CasoBau.status.in_(("AGUARDANDO", "GRAVE")),
                CasoBau.expira_em.is_not(None),
                CasoBau.expira_em <= agora_utc,
            )
        )
        return list(resultado.scalars().all())


async def liberar_limite_manual(
    *,
    id_fivem: str,
    item_canonico: str,
    executor_id: int,
) -> str:
    """Zera contador do item no ciclo e resolve casos abertos desse item."""
    ciclo = chave_ciclo_atual()
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(ContadorItemBau).where(
                ContadorItemBau.id_fivem == id_fivem,
                ContadorItemBau.item_canonico == item_canonico,
                ContadorItemBau.ciclo_chave == ciclo,
            )
        )
        contador = resultado.scalar_one_or_none()
        if contador is not None:
            contador.quantidade = 0
            contador.atualizado_em = datetime.now(timezone.utc)

        casos = await sessao.execute(
            select(CasoBau).where(
                CasoBau.id_fivem == id_fivem,
                CasoBau.item_canonico == item_canonico,
                CasoBau.status.in_(("AGUARDANDO", "GRAVE")),
            )
        )
        for caso in casos.scalars().all():
            caso.status = "IGNORADO"
            caso.motivo_ignore = f"Liberação manual por <@{executor_id}>"
            caso.resolvido_por = executor_id
            caso.resolvido_em = datetime.now(timezone.utc)

        await sessao.commit()
    return (
        f"Limite liberado para passaporte `{id_fivem}` / item `{item_canonico}` "
        f"no ciclo `{ciclo}`."
    )


async def processar_log_parseado(
    log: LogBauParseado,
) -> list[dict]:
    """
    Aplica movimentos e devolve lista de eventos para o listener reagir
    (avisos a abrir, itens desconhecidos, casos atualizados).
    """
    eventos: list[dict] = []
    if log.acao == "DESCONHECIDA":
        eventos.append({"tipo": "acao_desconhecida", "log": log})
        return eventos

    ciclo = chave_ciclo_atual()
    discord_id = await resolver_discord_id(log.id_fivem)
    delta_sinal = 1 if log.acao == "PEGOU" else -1

    async with _trava_do_id(log.id_fivem):
        for item in log.itens:
            if item.item_canonico is None:
                eventos.append(
                    {
                        "tipo": "item_desconhecido",
                        "nome": item.nome_bruto,
                        "id_fivem": log.id_fivem,
                    }
                )
                continue

            delta = delta_sinal * item.quantidade
            quantidade = await aplicar_movimento_item(
                id_fivem=log.id_fivem,
                nome_cidade=log.nome_cidade,
                item_canonico=item.item_canonico,
                delta=delta,
                ciclo=ciclo,
            )

            limite_1 = LIMITES_BAU_CAMADA_1.get(item.item_canonico)
            limite_2 = LIMITES_BAU_CAMADA_2.get(item.item_canonico)
            if limite_1 is None:
                continue

            # Devolução que volta abaixo do limite 1 → fecha caso aberto
            if delta < 0 and quantidade < limite_1:
                caso_aberto = await buscar_caso_aberto(
                    log.id_fivem, item.item_canonico
                )
                if caso_aberto is not None:
                    await resolver_caso(
                        caso_aberto.id,
                        por_discord_id=None,
                        status="RESOLVIDO",
                    )
                    eventos.append(
                        {
                            "tipo": "caso_resolvido_auto",
                            "caso_id": caso_aberto.id,
                            "item": item.item_canonico,
                            "quantidade": quantidade,
                        }
                    )
                continue

            if quantidade < limite_1:
                continue

            e_grave = limite_2 is not None and quantidade >= limite_2
            caso, criado = await abrir_ou_atualizar_caso(
                id_fivem=log.id_fivem,
                nome_cidade=log.nome_cidade,
                discord_id=discord_id,
                item_canonico=item.item_canonico,
                quantidade=quantidade,
                e_grave=e_grave,
            )
            eventos.append(
                {
                    "tipo": "caso_novo" if criado else "caso_atualizado",
                    "caso": caso,
                    "quantidade": quantidade,
                    "limite_1": limite_1,
                    "limite_2": limite_2,
                    "e_grave": e_grave,
                    "discord_id": discord_id,
                }
            )

    return eventos


def parsear_conteudo(conteudo: str) -> LogBauParseado | None:
    return parsear_mensagem_log_bau(conteudo, ALIASES_ITENS_BAU)
