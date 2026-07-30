import discord
from datetime import datetime, timezone
from sqlalchemy import select

from src.config import (
    SEGUNDOS_PARA_MOEDA, 
    VALOR_MOEDA_INGAME, 
    CARGOS, 
    CARGOS_HIERARQUIA
)
from src.config import obter_todos_ids_canais_plantao
from src.services.identidade_service import resolver_id_fivem
from src.services.plantao_logger import registrar_evento_plantao, obter_id_fivem_de_recrutamento
from src.database.connection import async_session
from src.database.models import EstadoPlantao

from src.utils.formatacao import formatar_dinheiro


def garantir_aware(dt: datetime) -> datetime:
    """Se o datetime veio sem timezone (naive), assume que já era UTC e anexa isso."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def membro_pode_informar_id_manualmente(membro: discord.Member) -> bool:
    """True se o membro tiver algum cargo da hierarquia (Visitante já está fora dessa lista)."""
    ids_hierarquia = {CARGOS[nome] for nome in CARGOS_HIERARQUIA if nome in CARGOS}
    return any(cargo.id in ids_hierarquia for cargo in membro.roles)


async def _obter_ou_criar_estado(session, discord_id: int) -> EstadoPlantao:
    resultado = await session.execute(
        select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
    )
    estado = resultado.scalar_one_or_none()

    if estado is None:
        estado = EstadoPlantao(discord_id=discord_id)
        session.add(estado)

    return estado


def _membro_esta_em_call_valida(membro: discord.Member) -> discord.VoiceChannel | None:
    """Se o membro está atualmente numa call configurada como válida, retorna o canal. Senão, None."""
    if membro.voice is None or membro.voice.channel is None:
        return None

    if membro.voice.channel.id in obter_todos_ids_canais_plantao():
        return membro.voice.channel

    return None


async def ligar_servico(membro: discord.Member, id_fivem: str) -> str:
    """Liga o serviço. id_fivem já deve vir resolvido pelo chamador
    (via resolver_id_fivem ou digitado no modal) — essa função não valida mais isso."""
    async with async_session() as session:
        estado = await _obter_ou_criar_estado(session, membro.id)

        if estado.toggle_ligado:
            await session.commit()
            return "❌ Você já está em serviço."

        # 👇 Trava de validação — antes de qualquer outra coisa
        id_fivem_resolvido = await resolver_id_fivem(membro.id)
        if id_fivem_resolvido is None:
            await session.commit()  # nada mudou, mas fecha a sessão limpa
            return (
                "❌ Você não está registrado como membro aprovado do hospital "
                "(nem na Whitelist, nem em Recrutamento aprovado). "
                "Não é possível iniciar o plantão."
            )

        # Busca e "congela" o id_fivem no momento em que liga o serviço
        estado.id_fivem = await obter_id_fivem_de_recrutamento(membro.id)

        estado.id_fivem = id_fivem_resolvido
        estado.toggle_ligado = True
        estado.lembrete_1_enviado = False
        estado.lembrete_2_enviado = False

        canal_atual = _membro_esta_em_call_valida(membro)
        if canal_atual is not None:
            agora = datetime.now(timezone.utc)
            estado.em_call_valida = True
            estado.call_entrada_em = agora
            estado.segmento_iniciado_em = agora
            estado.canal_atual_id = canal_atual.id
            estado.ocioso_desde = None  # já entrou contando, não está ocioso
        else:
            estado.ocioso_desde = datetime.now(timezone.utc)  # ligou mas ainda fora de call

        id_fivem_atual = estado.id_fivem  # guarda antes do commit fechar a sessão
        await session.commit()

    await registrar_evento_plantao(membro.guild, membro.id, "TOGGLE_ON", id_fivem_atual)
    if canal_atual is not None:
        await registrar_evento_plantao(
            membro.guild, 
            membro.id, 
            "ENTROU_CALL", 
            id_fivem_atual,
            campos_extra={
                "Saldo Atual": f"{estado.saldo_moedas} moedas",
                "Já Conectou em Call": "Sim" if canal_atual is not None else "Não",
            },
        )

    return "✅ Você entrou em serviço! Conecte-se a uma das calls disponíveis para começar a contar tempo."


async def desligar_servico(membro: discord.Member) -> str:
    async with async_session() as session:
        estado = await _obter_ou_criar_estado(session, membro.id)

        if not estado.toggle_ligado:
            await session.commit()
            return "❌ Você não está em serviço."

        estava_ocioso_desde = estado.ocioso_desde

        if estado.em_call_valida:
            await _finalizar_periodo_em_call(estado, membro.guild)

        estado.toggle_ligado = False
        estado.ocioso_desde = None
        estado.lembrete_1_enviado = False
        estado.lembrete_2_enviado = False
        saldo_final = estado.saldo_moedas  # 👈 captura antes do commit
        id_fivem_atual = estado.id_fivem
        await session.commit()

    if estava_ocioso_desde is not None:
        duracao = int((datetime.now(timezone.utc) - garantir_aware(estava_ocioso_desde)).total_seconds())
        await registrar_evento_plantao(membro.guild, membro.id, "OCIOSO_ENCERRADO", id_fivem_atual,
                                        duracao_segundos=duracao,
                                        detalhes="Encerrado por saída manual do serviço")

    await registrar_evento_plantao(
        membro.guild, membro.id, "TOGGLE_OFF", id_fivem_atual,
        campos_extra={"Saldo Final": f"{saldo_final} moedas ({formatar_dinheiro(saldo_final * VALOR_MOEDA_INGAME)})"},
    )

    return "✅ Você saiu de serviço. Cronômetro encerrado."


async def _finalizar_periodo_em_call(estado: EstadoPlantao, guild: discord.Guild):
    """Fecha o segmento de call atual: loga a duração, credita moedas se aplicável, e reinicia o estado ocioso."""
    if estado.call_entrada_em is None:
        return

    entrada_total = garantir_aware(estado.call_entrada_em)
    decorrido_total = (datetime.now(timezone.utc) - entrada_total).total_seconds()

    inicio_segmento = garantir_aware(estado.segmento_iniciado_em) if estado.segmento_iniciado_em else entrada_total
    decorrido_segmento = int((datetime.now(timezone.utc) - inicio_segmento).total_seconds())

    canal_anterior_id = estado.canal_atual_id
    discord_id = estado.discord_id

    estado.segundos_acumulados += int(decorrido_total)

    moedas_ganhas = 0
    while estado.segundos_acumulados >= SEGUNDOS_PARA_MOEDA:
        estado.segundos_acumulados -= SEGUNDOS_PARA_MOEDA
        estado.saldo_moedas += 1
        moedas_ganhas += 1

    estado.em_call_valida = False
    estado.call_entrada_em = None
    estado.segmento_iniciado_em = None
    estado.canal_atual_id = None
    estado.ocioso_desde = datetime.now(timezone.utc)
    estado.lembrete_1_enviado = False
    estado.lembrete_2_enviado = False

    # _finalizar_periodo_em_call
    await registrar_evento_plantao(guild, discord_id, "SAIU_CALL", estado.id_fivem,
                                canal_id=canal_anterior_id, duracao_segundos=decorrido_segmento)

    if moedas_ganhas > 0:
        await registrar_evento_plantao(
            guild, discord_id, "MOEDA_CREDITADA", estado.id_fivem,
            campos_extra={
                "Moedas Ganhas": str(moedas_ganhas),
                "Saldo Total": f"{estado.saldo_moedas} moedas ({formatar_dinheiro(estado.saldo_moedas * VALOR_MOEDA_INGAME)})",
            },
        )
