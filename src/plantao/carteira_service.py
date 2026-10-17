# src/plantao/carteira_service.py
"""
Regras da carteira de moedas (plantão).

- Saldo vive em estado_plantao.saldo_moedas
- Valor histórico vive em lotes_moedas (cotação congelada no crédito)
- Extrato em movimentacoes_moedas
- Depósito $ → moedas em pedidos_deposito_moedas

Troca e desconto consomem lotes da menor cotação para a maior.
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import (
    CARGOS_HIERARQUIA,
    VALOR_MOEDA_INGAME,
    VALOR_MOEDA_POR_CARGO,
)
from src.database.conexao import async_session
from src.database.models import (
    EstadoPlantao,
    LoteMoeda,
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


def cotacao_moeda_do_cargo(nome_cargo: str | None) -> int:
    """
    Valor in-game de 1 moeda para o cargo informado.

    Sem cargo ou cargo fora da tabela → cotação mínima (Enfermeiro).
    """
    if not nome_cargo or nome_cargo == "—":
        return int(VALOR_MOEDA_INGAME)
    return int(VALOR_MOEDA_POR_CARGO.get(nome_cargo, VALOR_MOEDA_INGAME))


def cotacao_moeda_do_membro(membro: discord.Member | None) -> int:
    """Cotação da moeda pelo maior cargo hospitalar do membro."""
    if membro is None:
        return int(VALOR_MOEDA_INGAME)
    nome = cargo_principal_hierarquia(membro)
    return cotacao_moeda_do_cargo(nome)


def equivalente_em_reais(
    moedas: int,
    membro: discord.Member | None = None,
    cotacao: int | None = None,
) -> str:
    """
    Converte moedas em dinheiro in-game.

    Prioridade da cotação: argumento ``cotacao`` → cargo do ``membro`` →
    fallback mínimo do config.
    """
    if cotacao is not None:
        valor_unitario = int(cotacao)
    elif membro is not None:
        valor_unitario = cotacao_moeda_do_membro(membro)
    else:
        valor_unitario = int(VALOR_MOEDA_INGAME)
    return formatar_dinheiro(int(moedas) * valor_unitario)


def valor_ingame_de_moedas(
    moedas: int,
    membro: discord.Member | None = None,
    cotacao: int | None = None,
) -> int:
    """Mesma regra de ``equivalente_em_reais``, mas devolve inteiro."""
    if cotacao is not None:
        valor_unitario = int(cotacao)
    elif membro is not None:
        valor_unitario = cotacao_moeda_do_membro(membro)
    else:
        valor_unitario = int(VALOR_MOEDA_INGAME)
    return int(moedas) * valor_unitario


# ---------------------------------------------------------------------------
# Lotes de moedas (cotação congelada)
# ---------------------------------------------------------------------------


def _simular_debito_lotes(
    lotes: list[tuple[int, int]],
    quantidade: int,
) -> int:
    """
    Calcula o valor in-game de debitar ``quantidade`` moedas dos lotes,
    consumindo da menor cotação para a maior.

    ``lotes`` = lista de (cotacao, quantidade), já ordenada por cotacao ASC.
    Não altera nada — só simula.
    """
    restante = int(quantidade)
    valor = 0
    for cotacao, qtd in lotes:
        if restante <= 0:
            break
        usar = min(int(qtd), restante)
        valor += usar * int(cotacao)
        restante -= usar
    return valor


async def _listar_lotes_ordenados(
    sessao: AsyncSession,
    discord_id: int,
) -> list[LoteMoeda]:
    """Lotes do membro ordenados da menor cotação para a maior."""
    resultado = await sessao.execute(
        select(LoteMoeda)
        .where(LoteMoeda.discord_id == discord_id)
        .where(LoteMoeda.quantidade > 0)
        .order_by(LoteMoeda.cotacao.asc())
    )
    return list(resultado.scalars().all())


async def _garantir_lotes_alinhados(
    sessao: AsyncSession,
    discord_id: int,
    saldo_moedas: int,
) -> list[LoteMoeda]:
    """
    Garante que a soma dos lotes bate com o saldo.

    Saldo legado (sem lote) vira um lote na cotação mínima (Enfermeiro).
    Se os lotes tiverem mais que o saldo, corta do maior para o menor.
    """
    lotes = await _listar_lotes_ordenados(sessao, discord_id)
    soma = sum(int(lote.quantidade) for lote in lotes)
    saldo = max(0, int(saldo_moedas))

    if soma == saldo:
        return lotes

    if soma < saldo:
        falta = saldo - soma
        cotacao_legado = int(VALOR_MOEDA_INGAME)
        lote_legado = None
        for lote in lotes:
            if int(lote.cotacao) == cotacao_legado:
                lote_legado = lote
                break
        if lote_legado is None:
            lote_legado = LoteMoeda(
                discord_id=discord_id,
                cotacao=cotacao_legado,
                quantidade=falta,
            )
            sessao.add(lote_legado)
            await sessao.flush()
        else:
            lote_legado.quantidade = int(lote_legado.quantidade) + falta
        return await _listar_lotes_ordenados(sessao, discord_id)

    # lotes > saldo: remove do maior cotação para o menor
    excesso = soma - saldo
    lotes_desc = sorted(lotes, key=lambda lote: int(lote.cotacao), reverse=True)
    for lote in lotes_desc:
        if excesso <= 0:
            break
        qtd = int(lote.quantidade)
        tirar = min(qtd, excesso)
        lote.quantidade = qtd - tirar
        excesso -= tirar
        if lote.quantidade <= 0:
            await sessao.delete(lote)
    await sessao.flush()
    return await _listar_lotes_ordenados(sessao, discord_id)


async def creditar_lotes(
    sessao: AsyncSession,
    discord_id: int,
    quantidade: int,
    cotacao: int,
) -> None:
    """
    Soma moedas no lote da cotação informada (cria o lote se não existir).

    Não mexe em ``estado_plantao.saldo_moedas`` — quem chama atualiza o saldo.
    """
    qtd = int(quantidade)
    cot = int(cotacao)
    if qtd <= 0 or cot <= 0:
        return

    resultado = await sessao.execute(
        select(LoteMoeda)
        .where(LoteMoeda.discord_id == discord_id)
        .where(LoteMoeda.cotacao == cot)
    )
    lote = resultado.scalar_one_or_none()
    if lote is None:
        sessao.add(
            LoteMoeda(
                discord_id=discord_id,
                cotacao=cot,
                quantidade=qtd,
            )
        )
    else:
        lote.quantidade = int(lote.quantidade or 0) + qtd
    await sessao.flush()


async def debitar_lotes(
    sessao: AsyncSession,
    discord_id: int,
    quantidade: int,
    saldo_moedas: int,
) -> tuple[bool, int]:
    """
    Consome moedas dos lotes da menor cotação para a maior.

    Retorna (ok, valor_ingame_debitado).
    Não mexe em ``estado_plantao.saldo_moedas`` — quem chama atualiza o saldo.
    """
    qtd = int(quantidade)
    if qtd <= 0:
        return True, 0

    lotes = await _garantir_lotes_alinhados(sessao, discord_id, saldo_moedas)
    soma = sum(int(lote.quantidade) for lote in lotes)
    if soma < qtd:
        return False, 0

    restante = qtd
    valor = 0
    for lote in lotes:
        if restante <= 0:
            break
        disponivel = int(lote.quantidade)
        usar = min(disponivel, restante)
        valor += usar * int(lote.cotacao)
        lote.quantidade = disponivel - usar
        restante -= usar
        if lote.quantidade <= 0:
            await sessao.delete(lote)

    await sessao.flush()
    return True, valor


async def transferir_lotes(
    sessao: AsyncSession,
    origem_id: int,
    destino_id: int,
    quantidade: int,
    saldo_origem: int,
) -> tuple[bool, int]:
    """
    Move moedas entre membros preservando a cotação de cada lote.

    Consome da origem (menor → maior) e credita no destino com a mesma cotação.
    Retorna (ok, valor_ingame_movido).
    """
    qtd = int(quantidade)
    if qtd <= 0:
        return True, 0

    lotes = await _garantir_lotes_alinhados(sessao, origem_id, saldo_origem)
    soma = sum(int(lote.quantidade) for lote in lotes)
    if soma < qtd:
        return False, 0

    restante = qtd
    valor = 0
    # (cotacao, quantidade) a creditar no destino
    para_destino: list[tuple[int, int]] = []
    for lote in lotes:
        if restante <= 0:
            break
        disponivel = int(lote.quantidade)
        usar = min(disponivel, restante)
        valor += usar * int(lote.cotacao)
        para_destino.append((int(lote.cotacao), usar))
        lote.quantidade = disponivel - usar
        restante -= usar
        if lote.quantidade <= 0:
            await sessao.delete(lote)

    for cotacao, q in para_destino:
        await creditar_lotes(sessao, destino_id, q, cotacao)

    await sessao.flush()
    return True, valor


async def zerar_lotes(sessao: AsyncSession, discord_id: int) -> int:
    """Apaga todos os lotes do membro. Retorna o valor in-game que existia."""
    lotes = await _listar_lotes_ordenados(sessao, discord_id)
    valor = sum(int(lote.quantidade) * int(lote.cotacao) for lote in lotes)
    for lote in lotes:
        await sessao.delete(lote)
    await sessao.flush()
    return valor


async def obter_valor_total_lotes(discord_id: int) -> int:
    """Soma quantidade × cotação de todos os lotes do membro (com alinhamento)."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao.saldo_moedas).where(
                EstadoPlantao.discord_id == discord_id
            )
        )
        saldo = int(resultado.scalar_one_or_none() or 0)
        if saldo <= 0:
            return 0
        lotes = await _garantir_lotes_alinhados(sessao, discord_id, saldo)
        valor = sum(int(lote.quantidade) * int(lote.cotacao) for lote in lotes)
        await sessao.commit()
        return valor


async def simular_valor_debito(discord_id: int, quantidade: int) -> int:
    """
    Quanto em $ sairia se debitasse ``quantidade`` moedas agora
    (menor cotação → maior), sem alterar o banco.
    """
    qtd = int(quantidade)
    if qtd <= 0:
        return 0
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao.saldo_moedas).where(
                EstadoPlantao.discord_id == discord_id
            )
        )
        saldo = int(resultado.scalar_one_or_none() or 0)
        if saldo <= 0:
            return 0
        lotes = await _garantir_lotes_alinhados(sessao, discord_id, saldo)
        await sessao.commit()
        pares = [(int(lote.cotacao), int(lote.quantidade)) for lote in lotes]
        return _simular_debito_lotes(pares, min(qtd, saldo))


async def listar_resumo_lotes(
    discord_id: int,
) -> list[tuple[int, int]]:
    """Lista (cotacao, quantidade) dos lotes do membro, menor → maior."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao.saldo_moedas).where(
                EstadoPlantao.discord_id == discord_id
            )
        )
        saldo = int(resultado.scalar_one_or_none() or 0)
        if saldo <= 0:
            return []
        lotes = await _garantir_lotes_alinhados(sessao, discord_id, saldo)
        await sessao.commit()
        return [(int(lote.cotacao), int(lote.quantidade)) for lote in lotes]


async def obter_saldo(discord_id: int) -> int:
    """Obtém o saldo armazenado, tratando membros sem estado de plantão como zero."""
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
    """Registra no banco uma alteração já calculada no saldo de moedas.

    Guarda o saldo resultante, a contraparte e uma referência curta quando existirem,
    formando o extrato auditável. Não altera o saldo: quem chama deve fazê-lo na mesma
    regra de negócio para que o histórico não substitua a operação financeira real.
    """
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
    """
    Devolve as movimentações mais recentes na ordem adequada para exibir o extrato.
    """
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

        ok_lotes, valor_movido = await transferir_lotes(
            sessao,
            remetente.id,
            destinatario.id,
            quantidade,
            saldo_rem,
        )
        if not ok_lotes:
            return (
                False,
                f"Saldo insuficiente. Você tem **{saldo_rem}** moeda(s).",
                saldo_rem,
                0,
            )

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
                referencia=(
                    f"para {destinatario.id} · "
                    f"{formatar_dinheiro(valor_movido)}"
                )[:200],
            )
        )
        sessao.add(
            MovimentacaoMoeda(
                discord_id=destinatario.id,
                tipo="TRANSFERENCIA_RECEBIDA",
                valor=quantidade,
                saldo_apos=saldo_dest_final,
                outro_discord_id=remetente.id,
                referencia=(
                    f"de {remetente.id} · "
                    f"{formatar_dinheiro(valor_movido)}"
                )[:200],
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
    """Cria no banco um pedido pendente, sem creditar moedas antecipadamente.

    Calcula o valor in-game pela cotação configurada e preserva uma observação limitada
    antes de gravar o pedido. A tupla informa se a solicitação foi aceita, explica a
    decisão e traz o pedido criado, permitindo que a interface aguarde a staff.
    """
    if quantidade <= 0:
        return False, "Informe uma quantidade maior que zero.", None
    if not membro_na_hierarquia(membro):
        return False, "Apenas membros da hierarquia podem solicitar depósito.", None

    valor_ingame = valor_ingame_de_moedas(quantidade, membro=membro)
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
    """Credita um pedido pendente uma única vez e registra sua origem no extrato.

    Atualiza no banco o saldo, o status e o responsável pela análise dentro da mesma
    transação. Assim, um botão repetido ou uma análise posterior não concede moedas
    duas vezes; a tupla devolvida informa o resultado e o pedido atualizado.
    """
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

        qtd = int(pedido.quantidade_moedas)
        valor_pedido = int(pedido.valor_ingame or 0)
        # Cotação efetiva do depósito (valor já calculado no pedido)
        cotacao_deposito = (
            max(1, valor_pedido // qtd) if qtd > 0 else int(VALOR_MOEDA_INGAME)
        )
        await creditar_lotes(sessao, pedido.discord_id, qtd, cotacao_deposito)
        estado.saldo_moedas = int(estado.saldo_moedas or 0) + qtd
        saldo_apos = int(estado.saldo_moedas)
        pedido.status = "APROVADO"
        pedido.analisado_por = staff.id
        pedido.atualizado_em = datetime.now(timezone.utc)

        sessao.add(
            MovimentacaoMoeda(
                discord_id=pedido.discord_id,
                tipo="DEPOSITO",
                valor=qtd,
                saldo_apos=saldo_apos,
                referencia=(
                    f"pedido #{pedido.id} · "
                    f"{formatar_dinheiro(valor_pedido)}"
                )[:200],
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
    """Fecha um pedido pendente como recusado, sem alterar o saldo do membro.

    Persiste no banco a pessoa que analisou, o motivo limitado e o horário da decisão.
    O estado pendente é exigido para impedir que uma ação duplicada sobrescreva uma
    aprovação ou uma recusa anterior.
    """
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
        valor_zerado = await zerar_lotes(sessao, discord_id)
        estado.saldo_moedas = 0
        estado.ultima_atualizacao = datetime.now(timezone.utc)
        sessao.add(
            MovimentacaoMoeda(
                discord_id=discord_id,
                tipo="AJUSTE_STAFF",
                valor=-saldo,
                saldo_apos=0,
                referencia=(
                    f"{motivo or 'Saldo zerado'} · "
                    f"{formatar_dinheiro(valor_zerado)}"
                )[:200],
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
    """
    Traduz códigos conhecidos do extrato sem esconder novos tipos ainda não mapeados.
    """
    mapa = {
        "GANHO_PLANTAO": "Plantão (30 min)",
        "TRANSFERENCIA_ENVIADA": "Transferência enviada",
        "TRANSFERENCIA_RECEBIDA": "Transferência recebida",
        "TROCA_INGAME": "Troca moedas → $",
        "DEPOSITO": "Depósito $ → moedas",
        "AJUSTE_STAFF": "Ajuste staff",
    }
    return mapa.get(tipo, tipo)
