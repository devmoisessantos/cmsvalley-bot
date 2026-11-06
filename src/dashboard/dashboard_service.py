"""
Regras de negócio do domínio dashboard.

- Snapshot real de cargos/canais/guild
- Montagem do corpo do sinal do bot
- Publicação / atualização do painel de KPIs
- Execução dos comandos da fila do site
"""

from __future__ import annotations

import logging
import os
from typing import Any

import discord
from sqlalchemy import select

from src.config import CANAIS, DASHBOARD_CANAL_PADRAO
from src.dashboard.dashboard_catalogo import montar_catalogo_para_api
from src.dashboard.dashboard_cliente import ClienteDashboard
from src.dashboard.dashboard_logger import (
    log_comando_fila,
    log_metas_publicadas,
)
from src.dashboard.dashboard_panel import (
    montar_card_de_anuncio,
    montar_painel_de_kpis,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado

registrador = logging.getLogger(__name__)

NOME_PAINEL_KPIS = "dashboard_kpis"
VERSAO_BOT = "cmsvalley-bot 2.4.0"


def id_canal_padrao() -> int:
    """Canal onde embeds e KPIs do dashboard são publicados."""
    if DASHBOARD_CANAL_PADRAO:
        return int(DASHBOARD_CANAL_PADRAO)
    return int(CANAIS.get("CANAL_DASHBOARD") or 0)


def resolver_canal(
    bot: discord.Client,
    canal_id: int | str | None = None,
) -> discord.abc.Messageable | None:
    """Resolve um canal por id (payload ou padrão)."""
    bruto = canal_id or id_canal_padrao()
    if not bruto:
        return None
    try:
        numero = int(bruto)
    except (TypeError, ValueError):
        return None
    return bot.get_channel(numero)


def montar_snapshot_da_guild(
    bot: discord.Client,
) -> tuple[list[dict], list[dict], dict]:
    """
    Cargos e canais REAIS do servidor (nome, id, cor, posição).

    A API usa isso como fallback quando a REST do Discord falha.
    """
    guilda = _resolver_guilda(bot)
    if guilda is None:
        return [], [], {}

    cargos = []
    for cargo in guilda.roles:
        cor = f"#{cargo.colour.value:06x}" if cargo.colour.value else ""
        cargos.append(
            {
                "id": str(cargo.id),
                "name": cargo.name,
                "color": cor,
                "position": cargo.position,
                "hoist": bool(cargo.hoist),
                "mentionable": bool(cargo.mentionable),
                "managed": bool(cargo.managed),
                "memberCount": len(cargo.members),
            }
        )

    canais = []
    for canal in guilda.channels:
        canais.append(
            {
                "id": str(canal.id),
                "name": canal.name,
                "type": getattr(canal.type, "value", canal.type),
                "position": canal.position,
                "parent_id": (
                    str(canal.category_id) if canal.category_id else None
                ),
            }
        )

    info = {
        "id": str(guilda.id),
        "name": guilda.name,
        "icon": guilda.icon.key if guilda.icon else None,
        "memberCount": guilda.member_count or len(guilda.members),
        "description": guilda.description or "",
    }
    return cargos, canais, info


def _resolver_guilda(bot: discord.Client) -> discord.Guild | None:
    """Escolhe a guilda principal (GUILD_ID ou a primeira disponível)."""
    alvo = (os.environ.get("GUILD_ID") or "").strip()
    if alvo.isdigit():
        guilda = bot.get_guild(int(alvo))
        if guilda is not None:
            return guilda
    if bot.guilds:
        return bot.guilds[0]
    return None


def montar_corpo_do_sinal(bot: discord.Client) -> dict[str, Any]:
    """
    Payload do POST /internal/sinal-do-bot.

    Inclui status técnico + snapshot de cargos/canais quando possível.
    """
    latencia = int((bot.latency or 0) * 1000)
    total_membros = sum(g.member_count or 0 for g in bot.guilds)
    corpo: dict[str, Any] = {
        "latencia_ms": latencia,
        "guilds": len(bot.guilds),
        "membros": total_membros,
        "cogs": len(bot.cogs),
        "versao": VERSAO_BOT,
        "catalogo": montar_catalogo_para_api(),
    }
    try:
        cargos, canais, info = montar_snapshot_da_guild(bot)
        if cargos:
            corpo["cargos"] = cargos
            corpo["canais"] = canais
            corpo["guild_info"] = info
    except Exception as falha:  # noqa: BLE001
        registrador.debug("snapshot de guild indisponível: %s", falha)
    return corpo


async def _guardar_referencia_do_painel(
    canal_id: int,
    message_id: int,
) -> None:
    """Grava ou atualiza o message_id do painel de KPIs."""
    async with async_session() as sessao:
        try:
            consulta = await sessao.execute(
                select(PainelPostado).where(
                    PainelPostado.nome_painel == NOME_PAINEL_KPIS
                )
            )
            registro = consulta.scalar_one_or_none()
            if registro is None:
                registro = PainelPostado(
                    nome_painel=NOME_PAINEL_KPIS,
                    canal_id=canal_id,
                    message_id=message_id,
                )
                sessao.add(registro)
            else:
                registro.canal_id = canal_id
                registro.message_id = message_id
            await sessao.commit()
        except Exception as falha:  # noqa: BLE001
            await sessao.rollback()
            registrador.warning(
                "Não consegui gravar referência do painel de KPIs: %s",
                falha,
            )


async def _buscar_referencia_do_painel() -> tuple[int, int] | None:
    """Devolve (canal_id, message_id) do painel de KPIs, se existir."""
    async with async_session() as sessao:
        consulta = await sessao.execute(
            select(PainelPostado).where(
                PainelPostado.nome_painel == NOME_PAINEL_KPIS
            )
        )
        registro = consulta.scalar_one_or_none()
        if registro is None:
            return None
        return int(registro.canal_id), int(registro.message_id)


async def publicar_painel_de_metas(
    bot: discord.Client,
    cliente: ClienteDashboard,
    *,
    periodo: str = "month",
) -> int:
    """
    Busca overview na API e publica/edita o painel de KPIs no Discord.

    Devolve quantas metas foram renderizadas.
    """
    dados = await cliente.buscar_overview(periodo)
    if not dados:
        raise RuntimeError("overview indisponível na API do dashboard")

    canal_id = dados.get("goals_channel_id") or id_canal_padrao()
    canal = resolver_canal(bot, canal_id)
    if canal is None:
        registrador.info(
            "Canal do dashboard não configurado; metas não postadas. "
            "Defina DASHBOARD_CANAL_PADRAO ou CANAIS['CANAL_DASHBOARD']."
        )
        return 0

    metas = list(dados.get("goals") or [])
    ao_vivo = dados.get("live_now")
    view = montar_painel_de_kpis(
        metas,
        ao_vivo=int(ao_vivo) if ao_vivo is not None else None,
        periodo_rotulo="mês" if periodo == "month" else periodo,
    )

    referencia = await _buscar_referencia_do_painel()
    mensagem = None
    if referencia is not None:
        canal_salvo, mensagem_id = referencia
        if canal_salvo == getattr(canal, "id", 0):
            try:
                mensagem = await canal.fetch_message(mensagem_id)
                await mensagem.edit(view=view)
            except (discord.NotFound, discord.HTTPException):
                mensagem = None

    if mensagem is None:
        mensagem = await canal.send(view=view)
        await _guardar_referencia_do_painel(
            int(getattr(canal, "id", 0)),
            int(mensagem.id),
        )

    guilda = getattr(canal, "guild", None)
    if guilda is not None:
        await log_metas_publicadas(guilda, len(metas))

    return len(metas)


async def enviar_card_do_site(
    bot: discord.Client,
    payload: dict,
) -> None:
    """Publica no Discord o card pedido pelo site (send_embed)."""
    canal = resolver_canal(bot, payload.get("channel_id"))
    if canal is None:
        raise RuntimeError(
            "canal não encontrado para card "
            "(defina DASHBOARD_CANAL_PADRAO ou channel_id no payload)"
        )

    cor_texto = payload.get("color") or "#2ee6c5"
    try:
        cor = discord.Color.from_str(str(cor_texto))
    except (ValueError, TypeError):
        cor = discord.Color.teal()

    view = montar_card_de_anuncio(
        titulo=str(payload.get("title") or "Dashboard CMS Valley"),
        descricao=str(payload.get("description") or ""),
        campos=list(payload.get("fields") or []),
        rodape=str(payload["footer"]) if payload.get("footer") else None,
        cor=cor,
    )
    await canal.send(view=view)


async def enviar_anuncio_do_site(
    bot: discord.Client,
    payload: dict,
) -> None:
    """Envia texto puro no canal (comando announce)."""
    canal = resolver_canal(bot, payload.get("channel_id"))
    if canal is None:
        raise RuntimeError("canal não encontrado para anúncio")
    conteudo = str(payload.get("content") or "").strip()
    if not conteudo:
        raise RuntimeError("anúncio sem conteúdo")
    await canal.send(conteudo)


async def executar_comando_da_fila(
    bot: discord.Client,
    cliente: ClienteDashboard,
    comando: dict,
) -> None:
    """
    Executa um item da fila /internal/comandos e confirma (ack) na API.
    """
    tipo = comando.get("tipo")
    payload = comando.get("payload") or {}
    comando_id = comando.get("id")
    resultado = ""
    erro = False

    try:
        if tipo == "send_embed":
            await enviar_card_do_site(bot, payload)
            resultado = "card enviado"
        elif tipo == "announce":
            await enviar_anuncio_do_site(bot, payload)
            resultado = "anúncio enviado"
        elif tipo in ("sync_now", "goals_changed"):
            cargos = await sincronizar_metas_por_cargo_do_dashboard(cliente)
            quantidade = await publicar_painel_de_metas(bot, cliente)
            if tipo == "sync_now":
                resultado = (
                    f"painel sincronizado ({quantidade}); "
                    f"metas por cargo ({cargos})"
                )
            else:
                resultado = (
                    f"metas por cargo aplicadas ({cargos}); "
                    f"painel ({quantidade})"
                )
        elif tipo == "settings_changed":
            resultado = "configurações recebidas"
        else:
            resultado = f"tipo desconhecido: {tipo}"
    except Exception as falha:  # noqa: BLE001
        erro = True
        resultado = str(falha)
        registrador.exception(
            "Falha ao executar comando dashboard #%s",
            comando_id,
        )

    if comando_id is not None:
        await cliente.confirmar_comando(int(comando_id), resultado, erro)

    guilda = _resolver_guilda(bot)
    if guilda is not None:
        await log_comando_fila(
            guilda,
            str(tipo or "?"),
            resultado,
            erro=erro,
        )



async def sincronizar_metas_por_cargo_do_dashboard(
    cliente: ClienteDashboard,
) -> int:
    """
    Puxa metas_por_cargo da API e aplica em METAS_POR_CARGO + TRILHAS_PROMOCAO.

    Fallback: se a API não responder, o bot continua com config.py.
    Retorna quantos cargos foram atualizados.
    """
    from src.config import (
        CARGO_DOUTOR,
        CARGO_INSTRUTOR,
        CARGO_PSICOLOGO,
        CARGO_RECRUTADOR,
        METAS_POR_CARGO,
        TRILHAS_PROMOCAO,
    )

    linhas = await cliente.buscar_metas_por_cargo()
    if not linhas:
        registrador.warning(
            "Metas por cargo vazias ou API indisponível — mantendo config.py."
        )
        return 0

    chaves_producao = (
        "meta_laudos",
        "meta_recrutamentos",
        "meta_chamadas",
        "meta_cursos_aplicados",
        "meta_tickets",
    )
    atualizados = 0
    for linha in linhas:
        nome = linha.get("cargo_nome")
        if not nome:
            continue
        if nome not in METAS_POR_CARGO:
            METAS_POR_CARGO[nome] = {
                "segundos_minimos_plantao": 0,
                "meta_laudos": 0,
                "meta_recrutamentos": 0,
                "meta_chamadas": 0,
                "meta_cursos_aplicados": 0,
                "meta_tickets": 0,
                "exige_avaliacao_hp": False,
            }
        METAS_POR_CARGO[nome]["segundos_minimos_plantao"] = int(
            linha.get("segundos_minimos_plantao") or 0
        )
        for chave in chaves_producao:
            METAS_POR_CARGO[nome][chave] = int(linha.get(chave) or 0)
        METAS_POR_CARGO[nome]["exige_avaliacao_hp"] = bool(
            linha.get("exige_avaliacao_hp") or False
        )
        atualizados += 1

    # Atualiza trilhas que cobram produção (não zera área→área / primeira área)
    for trilha in TRILHAS_PROMOCAO:
        if trilha.get("primeira_area"):
            continue
        observacao = (trilha.get("observacao") or "").lower()
        if "entre áreas" in observacao or "área → área" in observacao:
            continue
        if trilha.get("exige_todas_as_areas"):
            # Supervisor: soma das quatro especialidades
            trilha["meta_laudos"] = int(
                METAS_POR_CARGO.get(CARGO_PSICOLOGO, {}).get("meta_laudos") or 0
            )
            trilha["meta_recrutamentos"] = int(
                METAS_POR_CARGO.get(CARGO_RECRUTADOR, {}).get("meta_recrutamentos")
                or 0
            )
            trilha["meta_chamadas"] = int(
                METAS_POR_CARGO.get(CARGO_DOUTOR, {}).get("meta_chamadas") or 0
            )
            trilha["meta_cursos_aplicados"] = int(
                METAS_POR_CARGO.get(CARGO_INSTRUTOR, {}).get("meta_cursos_aplicados")
                or 0
            )
            continue
        destino = trilha.get("para_cargo")
        if not destino or destino not in METAS_POR_CARGO:
            continue
        base = METAS_POR_CARGO[destino]
        for chave in chaves_producao:
            trilha[chave] = int(base.get(chave) or 0)
        trilha["exige_avaliacao_hp"] = bool(base.get("exige_avaliacao_hp") or False)

    registrador.info(
        "Metas por cargo sincronizadas do dashboard (%s cargos).",
        atualizados,
    )
    return atualizados
