"""
O trabalho de verdade do quadro de hierarquia: contar, montar e publicar.

Como funciona
-------------
`calcular_membros_por_cargo` percorre o servidor e coloca cada membro embaixo
do seu cargo mais alto — nunca em dois cargos ao mesmo tempo, senao a soma do
quadro daria mais gente do que existe.

`atualizar_hierarquia` pega esse resultado, pede os cards para
hierarquia_builder.py e edita as mensagens que ja estao no canal, ao inves de
apagar e mandar de novo. Os ids dessas mensagens ficam na tabela
MensagemHierarquia, para o bot reencontra-las depois de reiniciar.
"""

import logging

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    CARGOS,
    CARGOS_EXCLUIR_HIERARQUIA,
    CARGOS_HIERARQUIA,
)
from src.database.conexao import async_session
from src.database.models import MensagemHierarquia
from src.hierarquia.hierarquia_builder import montar_cards_cargo_paginado
from src.utils.error_handling import ignorar_falha_cosmetica

registrador = logging.getLogger(__name__)


def obter_cargo_mais_alto(
    guild: discord.Guild, roles: list[discord.Role]
) -> discord.Role | None:
    """Dado um conjunto de cargos de um membro, retorna o cargo-mais-alto da hierarquia
    (ou None se o membro estiver excluído ou não tiver nenhum cargo da hierarquia)."""
    cargos_ordenados = [guild.get_role(CARGOS[nome]) for nome in CARGOS_HIERARQUIA]
    cargos_ordenados = [
        cargo_avaliado
        for cargo_avaliado in cargos_ordenados
        if cargo_avaliado is not None
    ]

    cargos_excluidos = [
        guild.get_role(CARGOS[nome]) for nome in CARGOS_EXCLUIR_HIERARQUIA
    ]
    cargos_excluidos = [
        cargo_avaliado
        for cargo_avaliado in cargos_excluidos
        if cargo_avaliado is not None
    ]

    if any(cargo in roles for cargo in cargos_excluidos):
        return None

    cargos_que_possui = [
        cargo_avaliado for cargo_avaliado in cargos_ordenados if cargo_avaliado in roles
    ]
    if not cargos_que_possui:
        return None

    return min(
        cargos_que_possui,
        key=lambda cargo_avaliado: cargos_ordenados.index(cargo_avaliado),
    )


def calcular_membros_por_cargo(guild: discord.Guild) -> dict[int, list[discord.Member]]:
    """
    Descobre quem aparece embaixo de cada cargo no quadro.

    Devolve um dicionario que liga o id do cargo a lista de membros dele. Cada
    membro entra em UM cargo so, o mais alto que possui. Sem essa regra, quem tem
    tres cargos apareceria tres vezes e a soma do quadro daria mais gente do que o
    servidor tem.

    Cargos que estao em CARGOS_HIERARQUIA mas nao existem mais no servidor sao
    descartados, em vez de quebrar a montagem do quadro.
    """
    cargos_ordenados = [guild.get_role(CARGOS[nome]) for nome in CARGOS_HIERARQUIA]
    cargos_ordenados = [
        cargo_avaliado
        for cargo_avaliado in cargos_ordenados
        if cargo_avaliado is not None
    ]

    resultado: dict[int, list[discord.Member]] = {
        cargo.id: [] for cargo in cargos_ordenados
    }

    for membro in guild.members:
        cargo_mais_alto = obter_cargo_mais_alto(guild, membro.roles)
        if cargo_mais_alto is not None:
            resultado[cargo_mais_alto.id].append(membro)

    return resultado


async def atualizar_hierarquia(
    guild: discord.Guild, somente_cargos: set[int] | None = None
):
    """
    Monta e publica o quadro de hierarquia no canal do Discord.

    Edita as mensagens que ja estao no canal em vez de apagar e postar de novo, para
    o canal nao encher de mensagem velha e o historico nao piscar para quem esta
    olhando.

    O parametro `somente_cargos` permite atualizar apenas os cargos que mudaram.
    Quem chama de dentro do ouvinte usa esse filtro, porque reescrever o quadro
    inteiro a cada troca de cargo gastaria o limite de chamadas do Discord sem
    necessidade.

    Se o canal de hierarquia nao for encontrado, registra o erro no log e desiste em
    silencio, sem derrubar o bot.
    """
    canal = guild.get_channel(CANAIS["HIERARQUIA_SUL"])
    if canal is None:
        registrador.error(
            "Canal de hierarquia não encontrado. Confira CANAIS['HIERARQUIA_SUL']."
        )
        return

    membros_por_cargo = calcular_membros_por_cargo(guild)

    for nome_cargo in CARGOS_HIERARQUIA:
        cargo = guild.get_role(CARGOS[nome_cargo])
        if cargo is None:
            continue

        # 👇 NOVO — pula cargos que não foram afetados, se o filtro foi passado
        if somente_cargos is not None and cargo.id not in somente_cargos:
            continue

        membros = membros_por_cargo.get(cargo.id, [])
        cards = montar_cards_cargo_paginado(cargo, membros)

        async with async_session() as session:
            # Busca TODAS as mensagens deste cargo
            resultado = await session.execute(
                select(MensagemHierarquia)
                .where(MensagemHierarquia.cargo_id == cargo.id)
                .order_by(MensagemHierarquia.pagina)
            )
            registros = resultado.scalars().all()

            # Se existem registros, edita as mensagens existentes
            if registros:
                for posicao_do_card, registro in enumerate(registros):
                    if posicao_do_card >= len(cards):
                        # Se tem mais registros que cards, apaga o excesso
                        try:
                            mensagem_em_excesso = await canal.fetch_message(
                                registro.message_id
                            )
                            await mensagem_em_excesso.delete()
                        except discord.NotFound as erro_em_atualizar_hierarquia:
                            # A mensagem ja nao existe mais no canal, entao nao ha
                            # nada para apagar. O registro do banco e removido logo
                            # abaixo de qualquer forma.
                            # Enfeite que falhou: atualizar hierarquia.
                            # A acao principal ja tinha dado certo, entao so registro.
                            ignorar_falha_cosmetica(
                                erro_em_atualizar_hierarquia,
                                o_que_falhou="atualizar hierarquia",
                            )
                        except discord.Forbidden:
                            logging.warning(
                                "Sem permissao para apagar a mensagem %s da "
                                "hierarquia no canal %s.",
                                registro.message_id,
                                canal.id,
                            )
                        except discord.HTTPException as falha_do_discord:
                            logging.warning(
                                "Falha ao apagar a mensagem %s da hierarquia: %s",
                                registro.message_id,
                                falha_do_discord,
                            )
                        await session.delete(registro)
                        continue

                    try:
                        mensagem = await canal.fetch_message(registro.message_id)
                        await mensagem.edit(
                            view=_embrulhar_em_view(cards[posicao_do_card])
                        )
                    except discord.NotFound:
                        # Mensagem foi apagada, cria nova
                        nova_mensagem = await canal.send(
                            view=_embrulhar_em_view(cards[posicao_do_card])
                        )
                        registro.message_id = nova_mensagem.id
                        registro.canal_id = canal.id

                # Se tem mais cards que registros, cria os novos
                for posicao_do_card in range(len(registros), len(cards)):
                    nova_mensagem = await canal.send(
                        view=_embrulhar_em_view(cards[posicao_do_card])
                    )
                    session.add(
                        MensagemHierarquia(
                            cargo_id=cargo.id,
                            pagina=posicao_do_card + 1,
                            canal_id=canal.id,
                            message_id=nova_mensagem.id,
                        )
                    )

                await session.commit()
                continue  # Vai para o próximo cargo

            # Não existem registros, cria tudo do zero
            for posicao_do_card, card in enumerate(cards):
                nova_mensagem = await canal.send(view=_embrulhar_em_view(card))
                session.add(
                    MensagemHierarquia(
                        cargo_id=cargo.id,
                        pagina=posicao_do_card + 1,
                        canal_id=canal.id,
                        message_id=nova_mensagem.id,
                    )
                )

            await session.commit()


def _embrulhar_em_view(container: discord.ui.Container) -> discord.ui.LayoutView:
    """
    Container sozinho não pode ser enviado direto — precisa estar dentro de uma
    LayoutView.
    """
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view
