"""Lógica de fechamento financeiro pós-ranking e troca de moedas."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.config import (
    AREAS_FINANCEIRAS,
    CANAIS,
    DIRETOR_CONTROLE_FINANCEIRO_IDS,
    VALOR_UNITARIO_RANKING,
)
from src.database.conexao import async_session
from src.database.models import SolicitacaoTrocaMoedas
from src.financas.financas_views import (
    ViewBotaoPagamentoFinancas,
    ViewSolicitacaoFinancasCard,
)
from src.membros.membros_service import resolver_id_fivem_do_membro
from src.utils.error_handling import enviar_erro_para_log_erros
from src.utils.formatacao import (
    formatar_data_curta,
    formatar_reais,
)

logger = logging.getLogger(__name__)


def _medalha(posicao: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(posicao, "🏅")


def _formatar_data_curta(data_e_hora: datetime) -> str:
    """Data curta DD/MM. Delega para o formatador comum do projeto."""
    return formatar_data_curta(data_e_hora)


def _formatar_ids_fivem(ids: list[str]) -> str:
    if len(ids) == 1:
        return ids[0]
    if len(ids) == 2:
        return f"{ids[0]} e {ids[1]}"
    return ", ".join(ids[:-1]) + f" e {ids[-1]}"


def _obter_canal_financas(guild: discord.Guild) -> discord.abc.Messageable | None:
    canal_id = CANAIS.get("CANAL_FINANCAS") or 0
    if not canal_id:
        logger.warning("CANAL_FINANCAS não configurado no config")
        return None
    canal = guild.get_channel(canal_id)
    if canal is None:
        logger.warning("Canal finanças id=%s não encontrado na guild", canal_id)
    return canal


async def montar_linhas_pagamento(
    contagem: dict[int, int],
    *,
    valor_unitario: int = VALOR_UNITARIO_RANKING,
) -> tuple[list[str], int, int]:
    """
    A partir de {discord_id: quantidade}, monta linhas com ID FiveM e valor cru.
    Retorna (linhas_texto, total_unidades, total_pago).
    """
    if not contagem:
        return [], 0, 0

    por_qtd: dict[int, list[int]] = defaultdict(list)
    for discord_id, quantidade in contagem.items():
        por_qtd[int(quantidade)].append(int(discord_id))

    linhas: list[str] = []
    posicao = 1
    total_unidades = 0

    for quantidade in sorted(por_qtd.keys(), reverse=True):
        discord_ids = sorted(por_qtd[quantidade])
        ids_fivem: list[str] = []
        for discord_id in discord_ids:
            fid = await resolver_id_fivem_do_membro(discord_id)
            ids_fivem.append(str(fid) if fid else f"?{discord_id}")

        medalha = _medalha(posicao)
        valor_linha = quantidade * valor_unitario
        total_unidades += quantidade * len(discord_ids)

        if len(ids_fivem) == 1:
            linhas.append(f"{medalha} {ids_fivem[0]} • 💰 {valor_linha}")
        else:
            linhas.append(
                f"{medalha} {_formatar_ids_fivem(ids_fivem)}\n"
                f"↳ {quantidade}x • 💰 {valor_linha}"
            )
        posicao += len(discord_ids)

    total_pago = total_unidades * valor_unitario
    return linhas, total_unidades, total_pago


def montar_texto_solicitacao_area(
    *,
    chave_area: str,
    periodo_txt: str,
    total_pago: int,
    total_unidades: int,
    observacao_extra: str | None = None,
) -> str:
    """Monta a solicitação que resume o pagamento de uma área para finanças.

    Busca os dados da área configurada e preserva valores de referência quando
    ela for desconhecida, para que o texto continue auditável. A observação
    opcional permite anexar contexto sem alterar o cálculo do fechamento.
    """
    area = AREAS_FINANCEIRAS.get(chave_area, {})
    titulo_area = area.get("titulo", chave_area.upper())
    unidade_plural = area.get("unidade_plural", "itens")
    cargo_id = area.get("cargo_id")
    responsavel_id = area.get("responsavel_discord_id")
    responsavel_fid = area.get("responsavel_fid") or "—"

    responsavel_txt = f"<@{responsavel_id}>" if responsavel_id else "_a definir_"
    area_txt = f"<@&{cargo_id}>" if cargo_id else titulo_area

    obs = observacao_extra or (
        f"_Ranking {titulo_area} — {total_unidades} {unidade_plural} válidos_"
    )

    return (
        "# 💰・SOLICITAÇÃO - PAGAMENTO DE ÁREA\n"
        "**=============================**\n"
        f"• 👨‍⚕️ **Responsável:** {responsavel_txt}\n"
        f"• 🆔 **FID:** {responsavel_fid}\n"
        f"• 🎯 **Área Médica:** {area_txt}\n"
        f"• 💰 **Valor Semanal:** {formatar_reais(total_pago)}\n"
        f"• 📅 **Período:** {periodo_txt}\n"
        f"• 🧾 **Observações (se houver):** {obs}\n"
        "**=============================**"
    )


def montar_texto_controle_dm(
    *,
    chave_area: str,
    periodo_txt: str,
    linhas_pagamento: list[str],
    total_unidades: int,
    total_pago: int,
    canal_financas_id: int | None,
) -> str:
    """Monta o controle detalhado enviado por DM à diretoria financeira.

    Inclui as linhas de pagamento, totais e um atalho ao canal público para
    que a conferência não dependa de procurar mensagens no servidor.
    """
    area = AREAS_FINANCEIRAS.get(chave_area, {})
    titulo_area = area.get("titulo", chave_area.upper())
    unidade_plural = area.get("unidade_plural", "itens")

    corpo = "\n\n".join(linhas_pagamento) if linhas_pagamento else "_Nenhum pagamento._"
    link_canal = f"<#{canal_financas_id}>" if canal_financas_id else "_canal finanças_"

    return (
        f"# 📋 **CONTROLE DE PAGAMENTO — {titulo_area}**\n"
        f"# 📅 **Período: {periodo_txt}**\n\n"
        f"{corpo}\n\n"
        f"# 📌 **TOTAL GERAL**\n"
        f"👥 **{unidade_plural.capitalize()} a pagar:** {total_unidades}\n"
        f"💰 **Valor total:** {total_pago}\n"
        f"📤 **Solicitação em:** {link_canal}"
    )


def montar_texto_lista_pagamento_publica(
    *,
    chave_area: str,
    periodo_txt: str,
    linhas_pagamento: list[str],
    total_pago: int,
) -> str:
    """Monta a lista pública de pagamentos com período e total consolidado.

    Usa as linhas já agrupadas pelo ranking para manter os valores conferíveis e
    inclui o horário da geração, distinguindo esta lista de outras publicações.
    """
    area = AREAS_FINANCEIRAS.get(chave_area, {})
    titulo_area = area.get("titulo", chave_area.upper())
    corpo = "\n\n".join(linhas_pagamento) if linhas_pagamento else "_Nenhum._"
    agora_ts = int(datetime.now(timezone.utc).timestamp())
    return (
        f"# 🏆 **PAGAMENTO RANKING DE {titulo_area}**\n"
        f"# 📅 **Período**\n"
        f"**{periodo_txt}**\n\n"
        f"{corpo}\n\n"
        f"# 📌 **GERAL**\n"
        f"💰 **TOTAL a ser pago:** {total_pago}\n"
        f"-# CENTRO MÉDICO SUL VALLEY • <t:{agora_ts}:f>"
    )


async def processar_fechamento_ranking(
    bot: discord.Client,
    guild: discord.Guild,
    *,
    chave_area: str,
    contagem: dict[int, int],
    inicio: datetime,
    fim: datetime,
    total_unidades: int | None = None,
    total_pago: int | None = None,
) -> None:
    """
    Após postar o ranking oficial:
    1) Solicitação no canal de finanças (com botão Pagamento realizado)
    2) Lista detalhada (IDs FiveM) no mesmo canal
    3) DM de controle para DIRETOR_CONTROLE_FINANCEIRO_IDS
    """
    try:
        await _processar_fechamento_ranking_interno(
            bot,
            guild,
            chave_area=chave_area,
            contagem=contagem,
            inicio=inicio,
            fim=fim,
            total_unidades=total_unidades,
            total_pago=total_pago,
        )
    except Exception as erro:
        logger.exception("Fechamento ranking %s falhou", chave_area)
        await enviar_erro_para_log_erros(
            guild,
            f"Fechamento financeiro — {chave_area}",
            erro,
            contexto="processar_fechamento_ranking",
        )


async def _processar_fechamento_ranking_interno(
    bot: discord.Client,
    guild: discord.Guild,
    *,
    chave_area: str,
    contagem: dict[int, int],
    inicio: datetime,
    fim: datetime,
    total_unidades: int | None = None,
    total_pago: int | None = None,
) -> None:
    if not contagem:
        logger.info("Fechamento %s: contagem vazia — nada a postar", chave_area)
        return

    valor_unitario = VALOR_UNITARIO_RANKING
    linhas, unidades_calc, pago_calc = await montar_linhas_pagamento(
        contagem, valor_unitario=valor_unitario
    )
    unidades = total_unidades if total_unidades is not None else unidades_calc
    pago = total_pago if total_pago is not None else pago_calc

    periodo_txt = f"{_formatar_data_curta(inicio)} a {_formatar_data_curta(fim)}"
    canal = _obter_canal_financas(guild)
    canal_id = CANAIS.get("CANAL_FINANCAS") or 0

    texto_solicitacao = montar_texto_solicitacao_area(
        chave_area=chave_area,
        periodo_txt=periodo_txt,
        total_pago=pago,
        total_unidades=unidades,
    )
    texto_lista = montar_texto_lista_pagamento_publica(
        chave_area=chave_area,
        periodo_txt=periodo_txt,
        linhas_pagamento=linhas,
        total_pago=pago,
    )
    texto_dm = montar_texto_controle_dm(
        chave_area=chave_area,
        periodo_txt=periodo_txt,
        linhas_pagamento=linhas,
        total_unidades=unidades,
        total_pago=pago,
        canal_financas_id=canal_id or None,
    )

    if canal is not None:
        # Ranking ainda usa texto em content + botão (lista longa / agrupamentos)
        await canal.send(
            content=texto_solicitacao,
            view=ViewBotaoPagamentoFinancas(ja_pago=False),
        )
        await canal.send(
            content=texto_lista,
            view=ViewBotaoPagamentoFinancas(ja_pago=False),
        )
        logger.info(
            "Finanças: solicitação + lista %s postadas em #%s",
            chave_area,
            getattr(canal, "name", canal_id),
        )
    else:
        logger.warning("CANAL_FINANCAS ausente — fechamento %s sem post", chave_area)

    from src.utils.notificacao import notificar_dm_controle_financeiro

    for diretor_id in DIRETOR_CONTROLE_FINANCEIRO_IDS:
        try:
            usuario = await bot.fetch_user(int(diretor_id))
        except (discord.NotFound, discord.HTTPException) as erro:
            logger.warning("Fetch diretor financeiro %s falhou: %s", diretor_id, erro)
            continue
        enviou = await notificar_dm_controle_financeiro(
            usuario,
            texto=texto_dm,
            guilda=guild,
        )
        if not enviou:
            logger.warning("DM controle financeiro %s não enviada", diretor_id)


async def gravar_solicitacao_troca_moedas(
    *,
    discord_id_beneficiario: int,
    id_fivem: str | None,
    quantidade_moedas: int,
    valor_ingame: int,
    canal_id: int,
    mensagem_id: int,
    titulo: str,
    corpo: str,
) -> SolicitacaoTrocaMoedas | None:
    """
    Persiste a solicitação ligada à mensagem do canal de finanças.

    Assim o bot recupera o beneficiário depois de um restart, só com o
    message_id da interação do botão.
    """
    try:
        async with async_session() as sessao:
            registro = SolicitacaoTrocaMoedas(
                discord_id_beneficiario=discord_id_beneficiario,
                id_fivem=id_fivem,
                quantidade_moedas=quantidade_moedas,
                valor_ingame=valor_ingame,
                canal_id=canal_id,
                mensagem_id=mensagem_id,
                titulo=titulo,
                corpo=corpo,
                status="pendente",
            )
            sessao.add(registro)
            await sessao.commit()
            await sessao.refresh(registro)
            return registro
    except SQLAlchemyError as erro_do_banco:
        logger.exception(
            "Falha ao gravar solicitação de troca de moedas (msg=%s): %s",
            mensagem_id,
            erro_do_banco,
        )
        return None


async def buscar_solicitacao_por_mensagem(
    mensagem_id: int,
) -> SolicitacaoTrocaMoedas | None:
    """Localiza a solicitação pelo id da mensagem no canal de finanças."""
    try:
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(SolicitacaoTrocaMoedas).where(
                    SolicitacaoTrocaMoedas.mensagem_id == mensagem_id
                )
            )
            return resultado.scalar_one_or_none()
    except SQLAlchemyError as erro_do_banco:
        logger.exception(
            "Falha ao buscar solicitação de troca (msg=%s): %s",
            mensagem_id,
            erro_do_banco,
        )
        return None


async def marcar_solicitacao_como_paga(
    mensagem_id: int,
    *,
    pago_por_id: int,
) -> SolicitacaoTrocaMoedas | None:
    """Atualiza status para pago e grava quem confirmou."""
    try:
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(SolicitacaoTrocaMoedas).where(
                    SolicitacaoTrocaMoedas.mensagem_id == mensagem_id
                )
            )
            registro = resultado.scalar_one_or_none()
            if registro is None:
                return None
            registro.status = "pago"
            registro.pago_por_id = pago_por_id
            registro.pago_em = datetime.now(timezone.utc)
            await sessao.commit()
            await sessao.refresh(registro)
            return registro
    except SQLAlchemyError as erro_do_banco:
        logger.exception(
            "Falha ao marcar solicitação paga (msg=%s): %s",
            mensagem_id,
            erro_do_banco,
        )
        return None


async def publicar_solicitacao_troca_moedas(
    guild: discord.Guild,
    *,
    membro: discord.Member,
    id_fivem: str | None,
    quantidade_moedas: int,
    valor_ingame: int,
) -> bool:
    """Publica troca de moedas no canal de finanças e grava no banco."""
    from src.plantao.plantao_service import montar_corpo_solicitacao_troca_moedas

    canal = _obter_canal_financas(guild)
    if canal is None:
        await enviar_erro_para_log_erros(
            guild,
            "Troca de moedas — canal finanças não encontrado",
            RuntimeError(
                f"CANAL_FINANCAS={CANAIS.get('CANAL_FINANCAS')} não resolvido na guild"
            ),
            contexto="publicar_solicitacao_troca_moedas",
            usuario=membro,
        )
        return False

    titulo, corpo = montar_corpo_solicitacao_troca_moedas(
        membro=membro,
        id_fivem=id_fivem,
        quantidade_moedas=quantidade_moedas,
        valor_ingame=valor_ingame,
    )

    view_card = ViewSolicitacaoFinancasCard(
        titulo=titulo,
        corpo=corpo,
        guild=guild,
        cor=discord.Color.dark_gold(),
        ja_pago=False,
        discord_id_beneficiario=membro.id,
    )

    try:
        mensagem = await canal.send(view=view_card)
    except Exception as erro:
        logger.exception("Falha ao postar troca de moedas em finanças")
        await enviar_erro_para_log_erros(
            guild,
            "Troca de moedas — falha ao postar no canal de finanças",
            erro,
            contexto="publicar_solicitacao_troca_moedas.send",
            usuario=membro,
        )
        return False

    registro = await gravar_solicitacao_troca_moedas(
        discord_id_beneficiario=membro.id,
        id_fivem=str(id_fivem) if id_fivem is not None else None,
        quantidade_moedas=quantidade_moedas,
        valor_ingame=valor_ingame,
        canal_id=mensagem.channel.id,
        mensagem_id=mensagem.id,
        titulo=titulo,
        corpo=corpo,
    )
    if registro is None:
        await enviar_erro_para_log_erros(
            guild,
            "Troca de moedas — postou no canal mas falhou ao gravar no banco",
            RuntimeError(f"mensagem_id={mensagem.id} sem registro persistido"),
            contexto="publicar_solicitacao_troca_moedas.gravar",
            usuario=membro,
        )
        # O post no canal já saiu; ainda assim devolve True para o membro
        # não achar que as moedas foram estornadas. A staff vê o log de erros.
    return True
