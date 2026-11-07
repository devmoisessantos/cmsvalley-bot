# src/plantao/financeiro/financeiro_admin_service.py
"""
Ações administrativas da carteira: ajuste com motivo, recálculo e correção.

Toda alteração de saldo passa por extrato (AJUSTE_STAFF) e, quando há guild,
por log visual de auditoria.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from sqlalchemy import select

from src.config import VALOR_MOEDA_INGAME
from src.database.conexao import async_session
from src.database.models import EstadoPlantao, MovimentacaoMoeda
from src.plantao.financeiro.carteira.carteira_service import (
    cotacao_moeda_do_membro,
    creditar_lotes,
    debitar_lotes,
    registrar_movimentacao,
    zerar_lotes,
)
from src.plantao.financeiro.financeiro_service import analisar_membro_completo
from src.plantao.financeiro.regras_moeda_service import segundos_para_moeda
from src.utils.error_handling import capturar_erro_e_logar

registrador = logging.getLogger(__name__)


@dataclass
class ResultadoAjusteSaldo:
    """Resultado de um ajuste manual de saldo."""

    ok: bool
    mensagem: str
    saldo_antes: int
    saldo_depois: int
    delta: int


@dataclass
class ResultadoRecalculo:
    """Resultado da simulação ou aplicação do recálculo por logs."""

    ok: bool
    mensagem: str
    moedas_esperadas_brutas: int
    moedas_no_extrato_plantao: int
    saldo_atual: int
    saldo_sugerido: int
    delta_sugerido: int
    avisos: list[str]


async def ajustar_saldo_com_motivo(
    *,
    discord_id: int,
    novo_saldo: int,
    motivo: str,
    executor: discord.Member,
    cotacao: int | None = None,
) -> ResultadoAjusteSaldo:
    """
    Define o saldo absoluto do membro com motivo obrigatório.

    Registra AJUSTE_STAFF no extrato e tenta postar auditoria visual.
    """
    motivo_limpo = (motivo or "").strip()
    if len(motivo_limpo) < 5:
        return ResultadoAjusteSaldo(
            ok=False,
            mensagem="Informe um motivo com pelo menos 5 caracteres.",
            saldo_antes=0,
            saldo_depois=0,
            delta=0,
        )

    if novo_saldo < 0:
        return ResultadoAjusteSaldo(
            ok=False,
            mensagem="O saldo não pode ser negativo.",
            saldo_antes=0,
            saldo_depois=0,
            delta=0,
        )

    cotacao_usar = int(cotacao) if cotacao and cotacao > 0 else int(VALOR_MOEDA_INGAME)
    if executor.guild is not None:
        membro_alvo = executor.guild.get_member(discord_id)
        if membro_alvo is not None and cotacao is None:
            cotacao_usar = cotacao_moeda_do_membro(membro_alvo)

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            estado = EstadoPlantao(discord_id=discord_id, saldo_moedas=0)
            sessao.add(estado)
            await sessao.flush()

        saldo_antes = int(estado.saldo_moedas or 0)
        saldo_depois = int(novo_saldo)
        delta = saldo_depois - saldo_antes

        if delta > 0:
            await creditar_lotes(sessao, discord_id, delta, cotacao_usar)
        elif delta < 0:
            ok_debito, _valor = await debitar_lotes(
                sessao, discord_id, -delta, saldo_antes
            )
            if not ok_debito:
                await zerar_lotes(sessao, discord_id)
                if saldo_depois > 0:
                    await creditar_lotes(sessao, discord_id, saldo_depois, cotacao_usar)
        elif saldo_depois == 0:
            await zerar_lotes(sessao, discord_id)

        estado.saldo_moedas = saldo_depois
        await sessao.commit()

    referencia = (f"ajuste staff por {executor.id}: {motivo_limpo}")[:200]

    try:
        if delta != 0:
            await registrar_movimentacao(
                discord_id=discord_id,
                tipo="AJUSTE_STAFF",
                valor=delta,
                saldo_apos=saldo_depois,
                referencia=referencia,
                guild=executor.guild,
            )
    except Exception as erro_extrato:
        await capturar_erro_e_logar(
            erro_extrato,
            contexto=(
                f"extrato do ajuste de saldo do membro {discord_id} por {executor.id}"
            ),
        )

    if executor.guild is not None:
        try:
            from src.plantao.auditoria_service import registrar_auditoria_admin

            await registrar_auditoria_admin(
                executor.guild,
                executor=executor,
                alvo=executor.guild.get_member(discord_id),
                acao="AJUSTE_SALDO_MOEDAS",
                detalhes=(
                    f"saldo {saldo_antes} → {saldo_depois} (delta {delta:+d}). "
                    f"Motivo: {motivo_limpo}"
                ),
            )
        except Exception as erro_auditoria:
            await capturar_erro_e_logar(
                erro_auditoria,
                contexto="auditoria visual do ajuste de saldo de moedas",
            )

    sinal = f"{delta:+d}" if delta != 0 else "0"
    return ResultadoAjusteSaldo(
        ok=True,
        mensagem=(
            f"Saldo de <@{discord_id}>: `{saldo_antes}` → `{saldo_depois}` "
            f"(delta {sinal}). Motivo registrado no extrato."
        ),
        saldo_antes=saldo_antes,
        saldo_depois=saldo_depois,
        delta=delta,
    )


async def simular_recalculo_por_logs(discord_id: int) -> ResultadoRecalculo:
    """
    Compara moedas de plantão no extrato com a estimativa bruta dos logs.

    Não aplica correção — só calcula o saldo sugerido.
    """
    analise = await analisar_membro_completo(discord_id)
    # Sugestão conservadora: não apaga outras movimentações (transferências etc.)
    # saldo_sugerido = saldo_atual - moedas_plantao_extrato + esperadas_brutas
    # (limitado a não ficar negativo)
    delta = analise.moedas_esperadas_brutas - analise.moedas_ganhas_plantao_extrato
    saldo_sugerido = max(0, analise.saldo_atual + delta)

    avisos = list(analise.avisos)
    avisos.append(
        "Estimativa bruta não aplica teto diário/semanal por dia civil — "
        "revise antes de confirmar a correção."
    )
    avisos.append(
        f"Intervalo atual: 1 moeda / {segundos_para_moeda()} s de call válida."
    )

    return ResultadoRecalculo(
        ok=True,
        mensagem="Simulação pronta. Confirme só se a diferença fizer sentido.",
        moedas_esperadas_brutas=analise.moedas_esperadas_brutas,
        moedas_no_extrato_plantao=analise.moedas_ganhas_plantao_extrato,
        saldo_atual=analise.saldo_atual,
        saldo_sugerido=saldo_sugerido,
        delta_sugerido=saldo_sugerido - analise.saldo_atual,
        avisos=avisos,
    )


async def aplicar_recalculo_por_logs(
    *,
    discord_id: int,
    executor: discord.Member,
    motivo: str,
) -> ResultadoAjusteSaldo:
    """
    Aplica o saldo sugerido pelo recálculo, com motivo obrigatório.
    """
    simulacao = await simular_recalculo_por_logs(discord_id)
    if not simulacao.ok:
        return ResultadoAjusteSaldo(
            ok=False,
            mensagem=simulacao.mensagem,
            saldo_antes=0,
            saldo_depois=0,
            delta=0,
        )

    motivo_final = (
        f"recálculo por logs: {motivo.strip()} "
        f"(esperado bruto {simulacao.moedas_esperadas_brutas}, "
        f"extrato plantão {simulacao.moedas_no_extrato_plantao})"
    )
    return await ajustar_saldo_com_motivo(
        discord_id=discord_id,
        novo_saldo=simulacao.saldo_sugerido,
        motivo=motivo_final,
        executor=executor,
    )


async def zerar_segundos_acumulados_excesso(
    discord_id: int,
    executor: discord.Member,
) -> tuple[bool, str]:
    """
    Remove blocos completos travados em segundos_acumulados.

    Mantém só o resto incompleto da próxima moeda.
    """
    limite = segundos_para_moeda()
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return False, "Membro sem registro de plantão."

        antes = int(estado.segundos_acumulados or 0)
        if antes < limite:
            return True, (f"Nada a limpar: acumulado `{antes}s` (abaixo de {limite}s).")

        depois = antes % limite
        estado.segundos_acumulados = depois
        await sessao.commit()

    if executor.guild is not None:
        try:
            from src.plantao.auditoria_service import registrar_auditoria_admin

            await registrar_auditoria_admin(
                executor.guild,
                executor=executor,
                alvo=executor.guild.get_member(discord_id),
                acao="LIMPAR_SEGUNDOS_ACUMULADOS",
                detalhes=f"{antes}s → {depois}s (blocos completos descartados)",
            )
        except Exception as erro_auditoria:
            await capturar_erro_e_logar(
                erro_auditoria,
                contexto="auditoria ao limpar segundos_acumulados",
            )

    return True, (
        f"Acumulado de <@{discord_id}>: `{antes}s` → `{depois}s` "
        f"(blocos ≥ {limite}s removidos)."
    )


async def corrigir_todos_acumulados_excesso(
    executor: discord.Member,
    *,
    apenas_discord_ids: list[int] | None = None,
    limite: int = 80,
) -> tuple[int, int, list[str]]:
    """
    Limpa blocos completos de segundos_acumulados em lote.

    Se `apenas_discord_ids` for passado, só esses membros.
    Senão, todos com acumulado >= 1 bloco.

    Retorna (ok_count, falha_count, linhas de relatório).
    Não credita moeda — só descarta bloco completo parado.
    """
    from src.plantao.financeiro.financeiro_service import (
        listar_alertas_acumulado_travado,
    )

    if apenas_discord_ids is not None:
        ids = list(dict.fromkeys(int(i) for i in apenas_discord_ids))[:limite]
    else:
        alertas = await listar_alertas_acumulado_travado(limite=limite)
        ids = [a.discord_id for a in alertas]

    ok_count = 0
    falha_count = 0
    linhas: list[str] = []
    for discord_id in ids:
        ok, msg = await zerar_segundos_acumulados_excesso(discord_id, executor)
        if ok:
            ok_count += 1
            linhas.append(f"OK · {msg}")
        else:
            falha_count += 1
            linhas.append(f"FALHA · {msg}")
    return ok_count, falha_count, linhas


async def corrigir_extrato_lotes_membro(
    discord_id: int,
    executor: discord.Member,
) -> tuple[bool, str]:
    """
    Alinha o extrato do membro ao padrão +1 e limpa resíduos do bug.

    O que faz (não muda o saldo atual):
    1. Cada GANHO_PLANTAO com valor ≥ 5 vira N linhas de +1
       (mesma cotação, cargo, referência e janela de tempo).
    2. Remove blocos completos de segundos_acumulados (só o resto).
    3. Registra no extrato uma linha de alinhamento (AJUSTE_STAFF 0)
       e auditoria visual.

    Depois disso a cobrança possível por lote some e o membro
    deixa de aparecer como irregular por crédito em lote.
    Caso = resolvido / conferido / pendência tratada.
    """
    from datetime import timedelta, timezone

    from src.plantao.financeiro.financeiro_service import (
        extrair_cotacao_da_referencia,
    )
    from src.plantao.financeiro.moeda_helper import LOTE_MINIMO_SUSPEITO

    limite_lote = int(LOTE_MINIMO_SUSPEITO)
    linhas_criadas = 0
    lotes_quebrados = 0

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(MovimentacaoMoeda)
            .where(MovimentacaoMoeda.discord_id == discord_id)
            .order_by(MovimentacaoMoeda.criado_em.asc(), MovimentacaoMoeda.id.asc())
        )
        movimentacoes = list(resultado.scalars().all())

        for mov in list(movimentacoes):
            tipo = (mov.tipo or "").upper()
            ref = mov.referencia or ""
            ref_lower = ref.lower()
            e_plantao = (
                tipo == "GANHO_PLANTAO"
                or "plantão" in ref_lower
                or "plantao" in ref_lower
            )
            quantidade = int(mov.valor or 0)
            if not e_plantao or quantidade < limite_lote:
                continue

            cotacao = getattr(mov, "cotacao", None)
            if cotacao is None or int(cotacao or 0) <= 0:
                cotacao = extrair_cotacao_da_referencia(ref)
            cargo = getattr(mov, "cargo_no_credito", None)
            criado = mov.criado_em
            if criado is not None and criado.tzinfo is None:
                criado = criado.replace(tzinfo=timezone.utc)

            saldo_final_lote = int(mov.saldo_apos or 0)
            saldo_antes_lote = saldo_final_lote - quantidade
            # Remove a linha em lote
            await sessao.delete(mov)
            lotes_quebrados += 1

            for indice in range(quantidade):
                momento = criado
                if momento is not None:
                    # Espalha 1s entre linhas para ordem estável no extrato
                    momento = momento + timedelta(seconds=indice)
                sessao.add(
                    MovimentacaoMoeda(
                        discord_id=discord_id,
                        tipo="GANHO_PLANTAO",
                        valor=1,
                        saldo_apos=saldo_antes_lote + indice + 1,
                        referencia=(
                            f"+1 plantão (alinhamento extrato) · "
                            f"lote #{mov.id or 0} corrigido"
                        )[:200],
                        cotacao=int(cotacao) if cotacao and int(cotacao) > 0 else None,
                        cargo_no_credito=(str(cargo)[:80] if cargo else None),
                        criado_em=momento,
                    )
                )
                linhas_criadas += 1

        # Limpa acumulado excessivo no mesmo commit
        limite = segundos_para_moeda()
        resultado_estado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado_estado.scalar_one_or_none()
        acumulado_antes = 0
        acumulado_depois = 0
        if estado is not None:
            acumulado_antes = int(estado.segundos_acumulados or 0)
            if acumulado_antes >= limite:
                acumulado_depois = acumulado_antes % limite
                estado.segundos_acumulados = acumulado_depois
            else:
                acumulado_depois = acumulado_antes

        # Marca caso resolvido no extrato (saldo inalterado)
        saldo_atual = int(estado.saldo_moedas or 0) if estado else 0
        sessao.add(
            MovimentacaoMoeda(
                discord_id=discord_id,
                tipo="AJUSTE_STAFF",
                valor=0,
                saldo_apos=saldo_atual,
                referencia=(
                    f"Alinhamento financeiro · extrato normalizado (+1) · "
                    f"{lotes_quebrados} lote(s) · por {executor.id}"
                )[:200],
            )
        )

        await sessao.commit()

    if executor.guild is not None:
        try:
            from src.plantao.auditoria_service import registrar_auditoria_admin

            await registrar_auditoria_admin(
                executor.guild,
                executor=executor,
                alvo=executor.guild.get_member(discord_id),
                acao="ALINHAMENTO_EXTRATO_PLANTAO",
                detalhes=(
                    f"lotes_quebrados={lotes_quebrados} "
                    f"linhas_+1={linhas_criadas} "
                    f"acumulado {acumulado_antes}s→{acumulado_depois}s"
                ),
            )
        except Exception as erro_auditoria:
            await capturar_erro_e_logar(
                erro_auditoria,
                contexto="auditoria alinhamento extrato plantão",
            )

    if lotes_quebrados == 0 and acumulado_antes < segundos_para_moeda():
        return True, (
            f"<@{discord_id}>: extrato já estava no padrão (+1) e "
            f"acumulado sem bloco completo. Nada a corrigir."
        )

    return True, (
        f"Alinhamento de <@{discord_id}> concluído: "
        f"`{lotes_quebrados}` lote(s) → `{linhas_criadas}` linha(s) +1 · "
        f"acumulado `{acumulado_antes}s` → `{acumulado_depois}s` · "
        f"saldo **não** alterado · cobrança por lote zerada no painel."
    )
