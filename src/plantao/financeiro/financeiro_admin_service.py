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
from src.database.models import EstadoPlantao
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
                    await creditar_lotes(
                        sessao, discord_id, saldo_depois, cotacao_usar
                    )
        elif saldo_depois == 0:
            await zerar_lotes(sessao, discord_id)

        estado.saldo_moedas = saldo_depois
        await sessao.commit()

    referencia = (
        f"ajuste staff por {executor.id}: {motivo_limpo}"
    )[:200]

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
                f"extrato do ajuste de saldo do membro {discord_id} "
                f"por {executor.id}"
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
    delta = (
        analise.moedas_esperadas_brutas - analise.moedas_ganhas_plantao_extrato
    )
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
            return True, (
                f"Nada a limpar: acumulado `{antes}s` "
                f"(abaixo de {limite}s)."
            )

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
