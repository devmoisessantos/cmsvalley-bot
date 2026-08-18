# src/plantao/carteira_service.py
"""
Regras da carteira de moedas (plantão).

- Saldo vive em estado_plantao.saldo_moedas
- Extrato em movimentacoes_moedas
- Depósito $ → moedas em pedidos_deposito_moedas
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select

from src.config import (
    CARGOS_HIERARQUIA,
    VALOR_MOEDA_INGAME,
)
from src.database.connection import async_session
from src.database.models import (
    EstadoPlantao,
    MovimentacaoMoeda,
    PedidoDepositoMoeda,
)
from src.utils.formatacao import formatar_dinheiro


def membro_na_hierarquia(membro: discord.Member) -> bool:
    """True se tem algum cargo da hierarquia hospitalar."""
    nomes = {cargo.name for cargo in membro.roles}
    return bool(nomes.intersection(set(CARGOS_HIERARQUIA)))


def cargo_principal_hierarquia(membro: discord.Member) -> str:
    """Maior cargo da lista CARGOS_HIERARQUIA que o membro possui."""
    nomes = {cargo.name for cargo in membro.roles}
    for nome in CARGOS_HIERARQUIA:
        if nome in nomes:
            return nome
    return "—"


def equivalente_em_reais(moedas: int) -> str:
    return formatar_dinheiro(int(moedas) * int(VALOR_MOEDA_INGAME))


async def obter_saldo(discord_id: int) -> int:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao.saldo_moedas).where(
                EstadoPlantao.discord_id == discord_id
            )
        )
        valor = resultado.scalar_one_or_none()
        return int(valor or 0)


async def registrar_movimentacao(
    *,
    discord_id: int,
    tipo: str,
    valor: int,
    saldo_apos: int,
    outro_discord_id: int | None = None,
    referencia: str | None = None,
) -> None:
    async with async_session() as sessao:
        sessao.add(
            MovimentacaoMoeda(
                discord_id=discord_id,
                tipo=tipo,
                valor=int(valor),
                saldo_apos=int(saldo_apos),
                outro_discord_id=outro_discord_id,
                referencia=(referencia or "")[:200] or None,
            )
        )
        await sessao.commit()


async def listar_extrato(discord_id: int, limite: int = 15) -> list[MovimentacaoMoeda]:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(MovimentacaoMoeda)
            .where(MovimentacaoMoeda.discord_id == discord_id)
            .order_by(MovimentacaoMoeda.id.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def transferir_moedas(
    *,
    remetente: discord.Member,
    destinatario: discord.Member,
    quantidade: int,
) -> tuple[bool, str, int, int]:
    """
    Transfere moedas entre membros da hierarquia.
    Retorna (ok, mensagem, saldo_remetente, saldo_destinatario).
    """
    if quantidade <= 0:
        return False, "Informe uma quantidade maior que zero.", 0, 0
    if remetente.id == destinatario.id:
        return False, "Você não pode transferir para si mesmo.", 0, 0
    if destinatario.bot:
        return False, "Não é possível transferir para bots.", 0, 0
    if not membro_na_hierarquia(remetente):
        return False, "Apenas membros da hierarquia podem usar a carteira.", 0, 0
    if not membro_na_hierarquia(destinatario):
        return (
            False,
            "Só é possível transferir para membros da **hierarquia** hospitalar.",
            0,
            0,
        )

    async with async_session() as sessao:
        resultado_rem = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == remetente.id)
        )
        estado_rem = resultado_rem.scalar_one_or_none()
        if estado_rem is None:
            estado_rem = EstadoPlantao(discord_id=remetente.id, saldo_moedas=0)
            sessao.add(estado_rem)
            await sessao.flush()

        saldo_rem = int(estado_rem.saldo_moedas or 0)
        if quantidade > saldo_rem:
            return (
                False,
                f"Saldo insuficiente. Você tem **{saldo_rem}** moeda(s).",
                saldo_rem,
                0,
            )

        resultado_dest = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == destinatario.id)
        )
        estado_dest = resultado_dest.scalar_one_or_none()
        if estado_dest is None:
            estado_dest = EstadoPlantao(discord_id=destinatario.id, saldo_moedas=0)
            sessao.add(estado_dest)
            await sessao.flush()

        estado_rem.saldo_moedas = saldo_rem - quantidade
        estado_dest.saldo_moedas = int(estado_dest.saldo_moedas or 0) + quantidade
        saldo_rem_final = int(estado_rem.saldo_moedas)
        saldo_dest_final = int(estado_dest.saldo_moedas)

        sessao.add(
            MovimentacaoMoeda(
                discord_id=remetente.id,
                tipo="TRANSFERENCIA_ENVIADA",
                valor=-quantidade,
                saldo_apos=saldo_rem_final,
                outro_discord_id=destinatario.id,
                referencia=f"para {destinatario.id}",
            )
        )
        sessao.add(
            MovimentacaoMoeda(
                discord_id=destinatario.id,
                tipo="TRANSFERENCIA_RECEBIDA",
                valor=quantidade,
                saldo_apos=saldo_dest_final,
                outro_discord_id=remetente.id,
                referencia=f"de {remetente.id}",
            )
        )
        await sessao.commit()

    return (
        True,
        f"**{quantidade}** moeda(s) enviadas para {destinatario.mention}.",
        saldo_rem_final,
        saldo_dest_final,
    )


async def criar_pedido_deposito(
    *,
    membro: discord.Member,
    quantidade: int,
    observacao: str | None,
    id_fivem: str | None,
) -> tuple[bool, str, PedidoDepositoMoeda | None]:
    if quantidade <= 0:
        return False, "Informe uma quantidade maior que zero.", None
    if not membro_na_hierarquia(membro):
        return False, "Apenas membros da hierarquia podem solicitar depósito.", None

    valor_ingame = quantidade * int(VALOR_MOEDA_INGAME)
    async with async_session() as sessao:
        pedido = PedidoDepositoMoeda(
            discord_id=membro.id,
            id_fivem=id_fivem,
            quantidade_moedas=quantidade,
            valor_ingame=valor_ingame,
            observacao=(observacao or "")[:500] or None,
            status="PENDENTE",
        )
        sessao.add(pedido)
        await sessao.commit()
        await sessao.refresh(pedido)
        return True, "Pedido criado.", pedido


async def aprovar_deposito(
    *,
    pedido_id: int,
    staff: discord.Member,
) -> tuple[bool, str, PedidoDepositoMoeda | None]:
    async with async_session() as sessao:
        pedido = await sessao.get(PedidoDepositoMoeda, pedido_id)
        if pedido is None:
            return False, "Pedido não encontrado.", None
        if pedido.status != "PENDENTE":
            return False, f"Pedido já está como `{pedido.status}`.", pedido

        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == pedido.discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            estado = EstadoPlantao(discord_id=pedido.discord_id, saldo_moedas=0)
            sessao.add(estado)
            await sessao.flush()

        estado.saldo_moedas = int(estado.saldo_moedas or 0) + int(
            pedido.quantidade_moedas
        )
        saldo_apos = int(estado.saldo_moedas)
        pedido.status = "APROVADO"
        pedido.analisado_por = staff.id
        pedido.atualizado_em = datetime.now(timezone.utc)

        sessao.add(
            MovimentacaoMoeda(
                discord_id=pedido.discord_id,
                tipo="DEPOSITO",
                valor=int(pedido.quantidade_moedas),
                saldo_apos=saldo_apos,
                referencia=f"pedido #{pedido.id}",
            )
        )
        await sessao.commit()
        await sessao.refresh(pedido)
        return True, "Depósito creditado.", pedido


async def recusar_deposito(
    *,
    pedido_id: int,
    staff: discord.Member,
    motivo: str | None = None,
) -> tuple[bool, str, PedidoDepositoMoeda | None]:
    async with async_session() as sessao:
        pedido = await sessao.get(PedidoDepositoMoeda, pedido_id)
        if pedido is None:
            return False, "Pedido não encontrado.", None
        if pedido.status != "PENDENTE":
            return False, f"Pedido já está como `{pedido.status}`.", pedido
        pedido.status = "RECUSADO"
        pedido.analisado_por = staff.id
        pedido.motivo_recusa = (motivo or "Recusado pela equipe")[:500]
        pedido.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()
        await sessao.refresh(pedido)
        return True, "Pedido recusado.", pedido


async def ranking_top_moedas(limite: int = 15) -> list[tuple[int, int]]:
    """
    Lista (discord_id, saldo) ordenada por saldo desc.

    Busca mais linhas do que o limite para o ranking filtrar quem
    ainda faz parte da organização (guild + hierarquia).
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao.discord_id, EstadoPlantao.saldo_moedas)
            .where(EstadoPlantao.saldo_moedas > 0)
            .order_by(EstadoPlantao.saldo_moedas.desc())
            .limit(max(limite * 5, 50))
        )
        return [(int(row[0]), int(row[1] or 0)) for row in resultado.all()]


async def zerar_saldo_moedas(
    discord_id: int,
    *,
    motivo: str = "Saída da organização",
) -> int:
    """
    Zera saldo de moedas e registra AJUSTE_STAFF no extrato.
    Retorna o saldo que foi removido (0 se já estava zerado).
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return 0
        saldo = int(estado.saldo_moedas or 0)
        if saldo <= 0:
            return 0
        estado.saldo_moedas = 0
        estado.ultima_atualizacao = datetime.now(timezone.utc)
        sessao.add(
            MovimentacaoMoeda(
                discord_id=discord_id,
                tipo="AJUSTE_STAFF",
                valor=-saldo,
                saldo_apos=0,
                referencia=(motivo or "Saldo zerado")[:200],
            )
        )
        await sessao.commit()
        return saldo


def membro_elegivel_ranking_moedas(membro: discord.Member | None) -> bool:
    """
    Só entra no ranking quem ainda faz parte da organização:
    - está na guild
    - tem cargo da hierarquia hospitalar, HP S・Valley ou Aprovado
    - não é só Visitante / Exonerado
    """
    if membro is None:
        return False
    nomes = {cargo.name for cargo in membro.roles}
    if "🚫┇Exonerado" in nomes:
        return False
    if nomes.intersection(set(CARGOS_HIERARQUIA)):
        return True
    if "HP S・Valley" in nomes or "Aprovado" in nomes:
        return True
    return False


def rotulo_tipo_movimentacao(tipo: str) -> str:
    mapa = {
        "GANHO_PLANTAO": "Plantão (30 min)",
        "TRANSFERENCIA_ENVIADA": "Transferência enviada",
        "TRANSFERENCIA_RECEBIDA": "Transferência recebida",
        "TROCA_INGAME": "Troca moedas → $",
        "DEPOSITO": "Depósito $ → moedas",
        "AJUSTE_STAFF": "Ajuste staff",
    }
    return mapa.get(tipo, tipo)
