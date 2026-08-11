"""Lógica de catálogo, posse e solicitação de cursos."""

from __future__ import annotations

import logging
import math

import discord
from sqlalchemy import select

from src.config import (
    CURSOS,
    VALOR_MOEDA_INGAME,
)
from src.database.connection import async_session
from src.database.models import (
    EstadoPlantao,
    SolicitacaoCurso,
    agora,
)
from src.utils.error_handling import enviar_erro_para_log_erros
from src.utils.formatacao import formatar_reais

logger = logging.getLogger(__name__)


def listar_cursos_ordenados() -> list[tuple[str, dict]]:
    """Lista (chave, dados) — práticos 1.0, 2.0, depois função/diretoria."""
    ordem_nivel = {"1.0": 0, "2.0": 1, "funcao": 2, "diretoria": 3}
    itens = list(CURSOS.items())
    itens.sort(key=lambda par: (ordem_nivel.get(par[1].get("nivel"), 9), par[1]["nome"]))
    return itens


def obter_curso(chave: str) -> dict | None:
    return CURSOS.get(chave)


def moedas_necessarias_para_curso(chave: str) -> int:
    """Quantas moedas de plantão equivalem ao valor in-game (arredonda para cima)."""
    dados = obter_curso(chave)
    if not dados:
        return 0
    valor = int(dados.get("valor_ingame") or 0)
    if valor <= 0:
        return 0
    return max(1, math.ceil(valor / VALOR_MOEDA_INGAME))


def membro_tem_curso(membro: discord.Member, chave: str) -> bool:
    """Curso concluído = possui o cargo Discord do catálogo."""
    dados = obter_curso(chave)
    if not dados:
        return False
    cargo_id = int(dados["cargo_id"])
    return any(cargo.id == cargo_id for cargo in membro.roles)


def listar_cursos_que_faltam(
    membro: discord.Member,
    chaves: list[str],
) -> list[str]:
    return [chave for chave in chaves if not membro_tem_curso(membro, chave)]


def rotulo_curso(chave: str) -> str:
    dados = obter_curso(chave)
    if not dados:
        return chave
    return f"{dados.get('emoji', '')} {dados['nome']}".strip()


async def consultar_saldo_moedas(discord_id: int) -> int:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return 0
        return int(estado.saldo_moedas or 0)


async def debitar_moedas_curso(
    discord_id: int,
    quantidade: int,
) -> tuple[bool, int, str]:
    """
    Debita moedas do plantão.
    Retorna (ok, saldo_restante, mensagem_erro).
    """
    if quantidade <= 0:
        return True, await consultar_saldo_moedas(discord_id), ""

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return False, 0, "Você ainda não tem saldo de plantão registrado."
        saldo = int(estado.saldo_moedas or 0)
        if saldo < quantidade:
            return (
                False,
                saldo,
                f"Saldo insuficiente. Você tem **{saldo}** moeda(s) e precisa de **{quantidade}**.",
            )
        estado.saldo_moedas = saldo - quantidade
        await sessao.commit()
        return True, int(estado.saldo_moedas), ""


async def registrar_solicitacao_curso(
    *,
    discord_id: int,
    chave_curso: str,
    forma_pagamento: str,
    moedas_debitadas: int,
    valor_ingame: int,
) -> SolicitacaoCurso:
    async with async_session() as sessao:
        registro = SolicitacaoCurso(
            discord_id=discord_id,
            chave_curso=chave_curso,
            valor_ingame=valor_ingame,
            moedas_debitadas=moedas_debitadas,
            forma_pagamento=forma_pagamento,
            status="PAGO" if forma_pagamento == "MOEDAS" and moedas_debitadas > 0 else (
                "PAGO" if forma_pagamento == "GRATUITO" else "PENDENTE"
            ),
            criado_em=agora(),
            atualizado_em=agora(),
        )
        sessao.add(registro)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def marcar_mensagem_solicitacao_curso(
    solicitacao_id: int,
    canal_id: int,
    mensagem_id: int,
) -> None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return
        registro.mensagem_canal_id = canal_id
        registro.mensagem_id = mensagem_id
        registro.atualizado_em = agora()
        await sessao.commit()


def texto_resumo_pagamento(
    chave: str,
    forma: str,
    moedas: int,
    saldo_restante: int | None = None,
) -> list[str]:
    dados = obter_curso(chave) or {}
    linhas = [
        f"**Curso:** {rotulo_curso(chave)}",
        f"**Valor in-game:** {formatar_reais(int(dados.get('valor_ingame') or 0))}",
        f"**Forma:** `{forma}`",
    ]
    if moedas:
        linhas.append(f"**Moedas debitadas:** `{moedas}`")
    if saldo_restante is not None:
        linhas.append(f"**Saldo restante:** `{saldo_restante}`")
    return linhas
