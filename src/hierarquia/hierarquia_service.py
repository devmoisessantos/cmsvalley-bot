import discord
from sqlalchemy import select

from src.config import CARGOS, CARGOS_HIERARQUIA, CANAIS
from src.database.connection import async_session
from src.database.models import MensagemHierarquia
from src.hierarquia.builder import montar_card_cargo, montar_cards_cargo_paginado


from src.config import CARGOS, CARGOS_HIERARQUIA, CARGOS_EXCLUIR_HIERARQUIA


def obter_cargo_mais_alto(guild: discord.Guild, roles: list[discord.Role]) -> discord.Role | None:
    """Dado um conjunto de cargos de um membro, retorna o cargo-mais-alto da hierarquia
    (ou None se o membro estiver excluído ou não tiver nenhum cargo da hierarquia)."""
    cargos_ordenados = [guild.get_role(CARGOS[nome]) for nome in CARGOS_HIERARQUIA]
    cargos_ordenados = [c for c in cargos_ordenados if c is not None]

    cargos_excluidos = [guild.get_role(CARGOS[nome]) for nome in CARGOS_EXCLUIR_HIERARQUIA]
    cargos_excluidos = [c for c in cargos_excluidos if c is not None]

    if any(cargo in roles for cargo in cargos_excluidos):
        return None

    cargos_que_possui = [c for c in cargos_ordenados if c in roles]
    if not cargos_que_possui:
        return None

    return min(cargos_que_possui, key=lambda c: cargos_ordenados.index(c))


def calcular_membros_por_cargo(guild: discord.Guild) -> dict[int, list[discord.Member]]:
    cargos_ordenados = [guild.get_role(CARGOS[nome]) for nome in CARGOS_HIERARQUIA]
    cargos_ordenados = [c for c in cargos_ordenados if c is not None]

    resultado: dict[int, list[discord.Member]] = {cargo.id: [] for cargo in cargos_ordenados}

    for membro in guild.members:
        cargo_mais_alto = obter_cargo_mais_alto(guild, membro.roles)
        if cargo_mais_alto is not None:
            resultado[cargo_mais_alto.id].append(membro)

    return resultado


async def atualizar_hierarquia(guild: discord.Guild, somente_cargos: set[int] | None = None):
    canal = guild.get_channel(CANAIS["HIERARQUIA_SUL"])
    if canal is None:
        print("Canal de hierarquia não encontrado. Confira CANAIS['HIERARQUIA_SUL'].")
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
                for i, registro in enumerate(registros):
                    if i >= len(cards):
                        # Se tem mais registros que cards, apaga o excesso
                        try:
                            msg = await canal.fetch_message(registro.message_id)
                            await msg.delete()
                        except:
                            pass
                        await session.delete(registro)
                        continue
                    
                    try:
                        msg = await canal.fetch_message(registro.message_id)
                        await msg.edit(view=_embrulhar_em_view(cards[i]))
                    except discord.NotFound:
                        # Mensagem foi apagada, cria nova
                        nova_msg = await canal.send(view=_embrulhar_em_view(cards[i]))
                        registro.message_id = nova_msg.id
                        registro.canal_id = canal.id
                
                # Se tem mais cards que registros, cria os novos
                for i in range(len(registros), len(cards)):
                    nova_msg = await canal.send(view=_embrulhar_em_view(cards[i]))
                    session.add(MensagemHierarquia(
                        cargo_id=cargo.id,
                        pagina=i + 1,
                        canal_id=canal.id,
                        message_id=nova_msg.id
                    ))
                
                await session.commit()
                continue  # Vai para o próximo cargo
            
            # Não existem registros, cria tudo do zero
            for i, card in enumerate(cards):
                nova_msg = await canal.send(view=_embrulhar_em_view(card))
                session.add(MensagemHierarquia(
                    cargo_id=cargo.id,
                    pagina=i + 1,
                    canal_id=canal.id,
                    message_id=nova_msg.id
                ))
            
            await session.commit()


def _embrulhar_em_view(container: discord.ui.Container) -> discord.ui.LayoutView:
    """Container sozinho não pode ser enviado direto — precisa estar dentro de uma LayoutView."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


