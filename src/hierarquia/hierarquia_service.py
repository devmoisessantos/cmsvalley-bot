"""
O trabalho de verdade do quadro de hierarquia: contar, montar e publicar.

Como funciona
-------------
`calcular_membros_por_cargo` percorre o servidor e coloca cada membro embaixo
do seu cargo mais alto — nunca em dois cargos ao mesmo tempo, senao a soma do
quadro daria mais gente do que existe.

`atualizar_hierarquia` atualiza o quadro hospitalar e o quadro GATE. Pede os
cards para hierarquia_builder.py e edita as mensagens que ja estao no canal,
ao inves de apagar e mandar de novo. Os ids dessas mensagens ficam na tabela
MensagemHierarquia, para o bot reencontra-las depois de reiniciar.
"""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    CARGOS,
    CARGOS_EXCLUIR_HIERARQUIA,
    CARGOS_HIERARQUIA,
    HIERARQUIA_GATE,
)
from src.database.conexao import async_session
from src.database.models import MensagemHierarquia
from src.hierarquia.hierarquia_builder import montar_cards_cargo_paginado
from src.utils.error_handling import ignorar_falha_cosmetica

registrador = logging.getLogger(__name__)


def _resolver_cargos_ordenados(
    guild: discord.Guild,
    nomes_da_hierarquia: list[str],
) -> list[discord.Role]:
    """Converte nomes de cargo em roles existentes no servidor, na ordem dada."""
    cargos_ordenados: list[discord.Role] = []
    for nome in nomes_da_hierarquia:
        cargo = guild.get_role(CARGOS.get(nome, 0) or 0)
        if cargo is not None:
            cargos_ordenados.append(cargo)
    return cargos_ordenados


def obter_cargo_mais_alto(
    guild: discord.Guild, roles: list[discord.Role]
) -> discord.Role | None:
    """
    Cargo mais alto da hierarquia hospitalar.

    Quem tem cargo em CARGOS_EXCLUIR_HIERARQUIA (GATE CMS Valley) nao entra
    neste quadro — comportamento legado do painel hospitalar.
    """
    cargos_ordenados = _resolver_cargos_ordenados(guild, CARGOS_HIERARQUIA)
    cargos_excluidos = _resolver_cargos_ordenados(
        guild, CARGOS_EXCLUIR_HIERARQUIA
    )
    if any(cargo in roles for cargo in cargos_excluidos):
        return None

    cargos_que_possui = [
        cargo for cargo in cargos_ordenados if cargo in roles
    ]
    if not cargos_que_possui:
        return None

    return min(
        cargos_que_possui,
        key=lambda cargo: cargos_ordenados.index(cargo),
    )


def obter_cargo_mais_alto_gate(
    guild: discord.Guild, roles: list[discord.Role]
) -> discord.Role | None:
    """
    Cargo mais alto da hierarquia GATE.

    So cargos de HIERARQUIA_GATE contam. Membro sem cargo GATE nao aparece.
    """
    cargos_ordenados = _resolver_cargos_ordenados(guild, HIERARQUIA_GATE)
    cargos_que_possui = [
        cargo for cargo in cargos_ordenados if cargo in roles
    ]
    if not cargos_que_possui:
        return None

    return min(
        cargos_que_possui,
        key=lambda cargo: cargos_ordenados.index(cargo),
    )


def calcular_membros_por_cargo(
    guild: discord.Guild,
    nomes_da_hierarquia: list[str] | None = None,
    *,
    usar_gate: bool = False,
) -> dict[int, list[discord.Member]]:
    """
    Descobre quem aparece embaixo de cada cargo no quadro.

    Cada membro entra em UM cargo so, o mais alto que possui na lista.
    """
    if usar_gate:
        nomes = list(HIERARQUIA_GATE)
        resolver = obter_cargo_mais_alto_gate
    else:
        nomes = list(nomes_da_hierarquia or CARGOS_HIERARQUIA)
        resolver = obter_cargo_mais_alto

    cargos_ordenados = _resolver_cargos_ordenados(guild, nomes)
    resultado: dict[int, list[discord.Member]] = {
        cargo.id: [] for cargo in cargos_ordenados
    }

    for membro in guild.members:
        cargo_mais_alto = resolver(guild, membro.roles)
        if cargo_mais_alto is not None:
            resultado[cargo_mais_alto.id].append(membro)

    return resultado


async def atualizar_hierarquia(
    guild: discord.Guild, somente_cargos: set[int] | None = None
):
    """
    Atualiza o quadro hospitalar (HIERARQUIA_SUL).

    Usado pelo listener automatico e pelo subcomando hospital.
    `somente_cargos` limita a atualizacao aos cargos que mudaram.
    """
    await _atualizar_quadro(
        guild=guild,
        nomes_da_hierarquia=CARGOS_HIERARQUIA,
        chave_canal="HIERARQUIA_SUL",
        usar_gate=False,
        somente_cargos=somente_cargos,
    )


async def atualizar_hierarquia_gate(
    guild: discord.Guild, somente_cargos: set[int] | None = None
):
    """
    Atualiza o quadro GATE (HIERARQUIA_GATE).

    So membros com cargo GATE entram. Usado pelo listener e pelo
    subcomando `/atualizar-hierarquia gate`.
    """
    await _atualizar_quadro(
        guild=guild,
        nomes_da_hierarquia=HIERARQUIA_GATE,
        chave_canal="HIERARQUIA_GATE",
        usar_gate=True,
        somente_cargos=somente_cargos,
    )


async def _atualizar_quadro(
    guild: discord.Guild,
    nomes_da_hierarquia: list[str],
    chave_canal: str,
    usar_gate: bool,
    somente_cargos: set[int] | None,
) -> None:
    """Atualiza um quadro (hospital ou GATE) no canal configurado."""
    id_do_canal = CANAIS.get(chave_canal)
    if not id_do_canal:
        registrador.error(
            "Canal de hierarquia não configurado: CANAIS['%s'].",
            chave_canal,
        )
        return

    canal = guild.get_channel(id_do_canal)
    if canal is None:
        registrador.error(
            "Canal de hierarquia não encontrado. Confira CANAIS['%s'].",
            chave_canal,
        )
        return

    membros_por_cargo = calcular_membros_por_cargo(
        guild, nomes_da_hierarquia, usar_gate=usar_gate
    )

    for nome_cargo in nomes_da_hierarquia:
        cargo = guild.get_role(CARGOS.get(nome_cargo, 0) or 0)
        if cargo is None:
            continue

        if somente_cargos is not None and cargo.id not in somente_cargos:
            continue

        membros = membros_por_cargo.get(cargo.id, [])
        cards = montar_cards_cargo_paginado(cargo, membros)

        try:
            await _publicar_cards_do_cargo(canal, cargo, cards)
        except Exception as erro:
            registrador.exception(
                "Falha ao atualizar hierarquia do cargo %s (%s) em %s: %s",
                nome_cargo,
                cargo.id,
                chave_canal,
                erro,
            )


async def _publicar_cards_do_cargo(
    canal: discord.abc.Messageable,
    cargo: discord.Role,
    cards: list[discord.ui.Container],
) -> None:
    """
    Sincroniza as mensagens de um cargo com a lista de cards.

    Edita as que já existem, cria as que faltam e apaga o excesso.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(MensagemHierarquia)
            .where(MensagemHierarquia.cargo_id == cargo.id)
            .order_by(MensagemHierarquia.pagina)
        )
        registros = list(resultado.scalars().all())

        if registros:
            for posicao_do_card, registro in enumerate(registros):
                if posicao_do_card >= len(cards):
                    await _apagar_mensagem_em_excesso(canal, registro)
                    await session.delete(registro)
                    continue

                try:
                    mensagem = await canal.fetch_message(registro.message_id)
                    await mensagem.edit(
                        view=_embrulhar_em_view(cards[posicao_do_card])
                    )
                except discord.NotFound:
                    nova_mensagem = await canal.send(
                        view=_embrulhar_em_view(cards[posicao_do_card])
                    )
                    registro.message_id = nova_mensagem.id
                    registro.canal_id = canal.id
                except discord.Forbidden:
                    registrador.warning(
                        "Sem permissão para editar a mensagem %s da "
                        "hierarquia no canal %s.",
                        registro.message_id,
                        getattr(canal, "id", "?"),
                    )
                except discord.HTTPException as falha_do_discord:
                    registrador.warning(
                        "Falha ao editar a mensagem %s da hierarquia: %s",
                        registro.message_id,
                        falha_do_discord,
                    )

            for posicao_do_card in range(len(registros), len(cards)):
                nova_mensagem = await canal.send(
                    view=_embrulhar_em_view(cards[posicao_do_card])
                )
                session.add(
                    MensagemHierarquia(
                        cargo_id=cargo.id,
                        pagina=posicao_do_card + 1,
                        canal_id=getattr(canal, "id", 0),
                        message_id=nova_mensagem.id,
                    )
                )

            await session.commit()
            return

        for posicao_do_card, card in enumerate(cards):
            nova_mensagem = await canal.send(view=_embrulhar_em_view(card))
            session.add(
                MensagemHierarquia(
                    cargo_id=cargo.id,
                    pagina=posicao_do_card + 1,
                    canal_id=getattr(canal, "id", 0),
                    message_id=nova_mensagem.id,
                )
            )

        await session.commit()


async def _apagar_mensagem_em_excesso(
    canal: discord.abc.Messageable,
    registro: MensagemHierarquia,
) -> None:
    """Tenta apagar no Discord a mensagem que sobrou depois da paginação encolher."""
    try:
        mensagem_em_excesso = await canal.fetch_message(registro.message_id)
        await mensagem_em_excesso.delete()
    except discord.NotFound as erro_em_atualizar_hierarquia:
        ignorar_falha_cosmetica(
            erro_em_atualizar_hierarquia,
            o_que_falhou="atualizar hierarquia",
        )
    except discord.Forbidden:
        registrador.warning(
            "Sem permissão para apagar mensagem em excesso da hierarquia "
            "(message_id=%s).",
            registro.message_id,
        )


def _embrulhar_em_view(card: discord.ui.Container) -> discord.ui.LayoutView:
    """Coloca o container do card dentro de uma LayoutView pronta para enviar."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(card)
    return view
