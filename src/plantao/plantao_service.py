"""
O coracao do plantao: cronometrar o tempo e creditar as moedas.

Como o tempo e contado
----------------------
O plantao nao e um cronometro so. Ele e feito de pedacos: a pessoa entra na
call, sai, volta. `_acumular_segmento_atual` fecha o pedaco que estava aberto e
soma no total, para que sair da call nao apague o tempo ja trabalhado.

Quem conta tempo e quem nao conta
---------------------------------
As funcoes `_membro_surdo`, `_membro_mutado_e_surdo` e
`_membro_conta_tempo_moeda` respondem essa pergunta. Quem esta na call com o
fone desligado nao esta atendendo ninguem, e por isso nao ganha moeda.

`garantir_aware` conserta datas que vieram do banco sem fuso horario. Comparar
uma data com fuso com outra sem fuso quebra em Python, e esse erro aparecia na
virada do dia.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord
from sqlalchemy import select

from src.config import (
    CARGOS,
    CARGOS_DOUTOR_OU_ACIMA,
    CARGOS_HIERARQUIA,
    HORA_LIMITE_MOEDA_SABADO,
    SEGUNDOS_PARA_MOEDA,
    TETO_MOEDAS_POR_DIA,
    TETO_MOEDAS_POR_SEMANA,
    TIMEZONE_LOCAL,
    VALOR_MOEDA_INGAME,
    obter_todos_ids_canais_plantao,
)
from src.database.conexao import async_session
from src.database.models import EstadoPlantao
from src.plantao.carteira_service import (
    cotacao_moeda_do_membro,
    valor_ingame_de_moedas,
)
from src.plantao.plantao_logger import registrar_evento_plantao
from src.utils.error_handling import capturar_erro_e_logar
from src.utils.formatacao import formatar_dinheiro, formatar_reais


def garantir_aware(data_e_hora: datetime) -> datetime:
    """Se o datetime veio sem timezone (naive), assume que já era UTC e anexa isso."""
    if data_e_hora.tzinfo is None:
        return data_e_hora.replace(tzinfo=timezone.utc)
    return data_e_hora


def membro_e_doutor_ou_acima(membro: discord.Member) -> bool:
    """
    Confere os IDs de cargos aptos a iniciar chamadas, sem depender da posição visual.
    """
    ids_permitidos = {CARGOS[nome] for nome in CARGOS_DOUTOR_OU_ACIMA if nome in CARGOS}
    return any(cargo.id in ids_permitidos for cargo in membro.roles)


def _membro_mutado_e_surdo(membro: discord.Member) -> bool:
    """True se o membro estiver mudo E surdo (self ou aplicado pelo servidor)."""
    voice = membro.voice
    if voice is None:
        return False
    mudo = voice.self_mute or voice.mute
    surdo = voice.self_deaf or voice.deaf
    return mudo and surdo


def _membro_surdo(membro: discord.Member) -> bool:
    """True se estiver surdo (self ou server) — sozinho ou com mudo."""
    voice = membro.voice
    if voice is None:
        return False
    return bool(voice.self_deaf or voice.deaf)


def _membro_conta_tempo_moeda(membro: discord.Member) -> bool:
    """
    Tempo de plantão que gera moeda:
    - precisa estar em call válida (checado pelo chamador)
    - mutado sozinho → CONTA
    - surdo (com ou sem mudo) → NÃO CONTA
    - ocioso / fora de call → NÃO CONTA (checado pelo chamador)
    """
    return not _membro_surdo(membro)


def _acumular_segmento_atual(estado: EstadoPlantao) -> int:
    """
    Soma ao saldo de segundos o trecho aberto (segmento_iniciado_em → agora)
    e zera o ponteiro do segmento. Retorna segundos adicionados.
    """
    if estado.segmento_iniciado_em is None:
        return 0
    inicio = garantir_aware(estado.segmento_iniciado_em)
    decorrido = int((datetime.now(timezone.utc) - inicio).total_seconds())
    if decorrido > 0:
        estado.segundos_acumulados = int(estado.segundos_acumulados or 0) + decorrido
    estado.segmento_iniciado_em = None
    return max(0, decorrido)


def _agora_local() -> datetime:
    """Relógio do hospital (fuso do config), para tetos e bloqueio de domingo/sábado."""
    return datetime.now(ZoneInfo(TIMEZONE_LOCAL))


def _chave_semana_iso(data_local: datetime) -> str:
    """Identificador estável da semana civil (ISO), ex.: 2026-W39."""
    ano, semana, _ = data_local.isocalendar()
    return f"{ano}-W{semana:02d}"


def _janela_permite_moeda(agora_local: datetime) -> bool:
    """
    Domingo: não gera moeda.
    Sábado a partir de HORA_LIMITE_MOEDA_SABADO: não gera moeda.
    Horas de plantão continuam em qualquer dia.
    """
    # weekday: segunda=0 … domingo=6
    if agora_local.weekday() == 6:
        return False
    if agora_local.weekday() == 5 and agora_local.hour >= HORA_LIMITE_MOEDA_SABADO:
        return False
    return True


def _garantir_contadores_de_teto(
    estado: EstadoPlantao,
    agora_local: datetime,
) -> None:
    """Zera contadores diário/semanal quando o dia ou a semana viram."""
    data_hoje = agora_local.date().isoformat()
    if estado.data_moedas_dia != data_hoje:
        estado.data_moedas_dia = data_hoje
        estado.moedas_ganhas_no_dia = 0

    chave_semana = _chave_semana_iso(agora_local)
    if estado.chave_semana_moedas != chave_semana:
        estado.chave_semana_moedas = chave_semana
        estado.moedas_ganhas_na_semana = 0


def _creditar_moedas_de_acumulado(
    estado: EstadoPlantao,
) -> int:
    """
    Converte segundos_acumulados em moedas (1 / SEGUNDOS_PARA_MOEDA).

    Respeita:
    - domingo sem moeda; sábado só antes do meio-dia
    - teto diário e semanal

    Os segundos além do teto **não são descartados**: ficam no acumulado
    para o próximo dia/semana em que ainda houver cota. Horas de ranking
    vêm dos logs de plantão, não deste contador.
    """
    agora_local = _agora_local()
    if not _janela_permite_moeda(agora_local):
        return 0

    _garantir_contadores_de_teto(estado, agora_local)

    moedas_ganhas = 0
    while int(estado.segundos_acumulados or 0) >= SEGUNDOS_PARA_MOEDA:
        ja_no_dia = int(estado.moedas_ganhas_no_dia or 0)
        ja_na_semana = int(estado.moedas_ganhas_na_semana or 0)
        if ja_no_dia >= TETO_MOEDAS_POR_DIA:
            break
        if ja_na_semana >= TETO_MOEDAS_POR_SEMANA:
            break

        estado.segundos_acumulados -= SEGUNDOS_PARA_MOEDA
        estado.saldo_moedas = int(estado.saldo_moedas or 0) + 1
        estado.moedas_ganhas_no_dia = ja_no_dia + 1
        estado.moedas_ganhas_na_semana = ja_na_semana + 1
        moedas_ganhas += 1

    return moedas_ganhas


def membro_pode_informar_id_manualmente(membro: discord.Member) -> bool:
    """
    True se o membro tiver algum cargo da hierarquia (Visitante já está fora dessa
    lista).
    """
    if membro.guild_permissions.administrator:
        return True
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
    """
    Se o membro está atualmente numa call configurada como válida, retorna o canal.
    Senão, None.
    """
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

        # id_fivem já vem resolvido pelo painel (recrutamento ou modal)
        estado.id_fivem = id_fivem
        estado.toggle_ligado = True
        estado.lembrete_1_enviado = False
        estado.lembrete_2_enviado = False
        estado.lembrete_3_enviado = False

        canal_atual = _membro_esta_em_call_valida(membro)
        if canal_atual is not None:
            agora = datetime.now(timezone.utc)
            estado.em_call_valida = True
            estado.call_entrada_em = agora
            estado.canal_atual_id = canal_atual.id
            estado.ocioso_desde = None
            # Surdo (ou mudo+surdo) não inicia cronômetro de moeda
            if _membro_conta_tempo_moeda(membro):
                estado.segmento_iniciado_em = agora
            else:
                estado.segmento_iniciado_em = None
                estado.afk_mudo_surdo_desde = agora
                estado.afk_canal_referencia_id = canal_atual.id
        else:
            estado.ocioso_desde = datetime.now(
                timezone.utc
            )  # ligou mas ainda fora de call

        await session.commit()
        id_fivem_atual = estado.id_fivem
        saldo_atual = estado.saldo_moedas

    await registrar_evento_plantao(
        membro.guild,
        membro.id,
        "TOGGLE_ON",
        id_fivem_atual,
        campos_extra={
            "Saldo Atual": f"{saldo_atual} moedas",
            "Já Conectou em Call": "Sim" if canal_atual is not None else "Não",
        },
    )
    if canal_atual is not None:
        await registrar_evento_plantao(
            membro.guild,
            membro.id,
            "ENTROU_CALL",
            id_fivem_atual,
            canal_id=canal_atual.id,
        )

    return "✅ Você entrou em serviço! Conecte-se a uma das calls disponíveis para começar a contar tempo."


async def desligar_servico(membro: discord.Member) -> str:
    """Encerra o plantão, consolida o tempo em call e registra os eventos gerados.

    Atualiza o estado no banco e pode creditar moedas pelo segmento aberto antes de
    desligar o toggle. Também grava eventos de auditoria para que sair da call não
    elimine o histórico nem deixe tempo pendente sem processamento.
    """
    async with async_session() as session:
        estado = await _obter_ou_criar_estado(session, membro.id)

        if not estado.toggle_ligado:
            await session.commit()
            return "❌ Você não está em serviço."

        estava_ocioso_desde = estado.ocioso_desde

        if estado.em_call_valida:
            await _finalizar_periodo_em_call(
                estado,
                membro.guild,
                evento="CALL_ENCERRADA",
                motivo="Encerramento do plantão (saiu do serviço)",
            )

        estado.toggle_ligado = False
        estado.ocioso_desde = None
        estado.lembrete_1_enviado = False
        estado.lembrete_2_enviado = False

        await session.commit()
        saldo_final = estado.saldo_moedas
        id_fivem_atual = estado.id_fivem

    if estava_ocioso_desde is not None:
        duracao = int(
            (
                datetime.now(timezone.utc) - garantir_aware(estava_ocioso_desde)
            ).total_seconds()
        )
        await registrar_evento_plantao(
            membro.guild,
            membro.id,
            "OCIOSO_ENCERRADO",
            id_fivem_atual,
            duracao_segundos=duracao,
            detalhes="Encerrado por saída manual do serviço",
        )

    cotacao = cotacao_moeda_do_membro(membro)
    await registrar_evento_plantao(
        membro.guild,
        membro.id,
        "TOGGLE_OFF",
        id_fivem_atual,
        campos_extra={
            "Saldo Final": (
                f"{saldo_final} moedas "
                f"({formatar_dinheiro(saldo_final * cotacao)})"
            )
        },
    )

    return "✅ Você saiu de serviço. Cronômetro encerrado."


async def _finalizar_periodo_em_call(
    estado: EstadoPlantao,
    guild: discord.Guild,
    evento: str = "SAIU_CALL",
    motivo: str = "Saiu da call de voz",
):
    """
    Fecha o período em call: soma só o segmento ativo (não conta ocioso/surdo),
    credita moedas e marca ocioso (toggle ainda ligado).
    """
    if estado.call_entrada_em is None and estado.segmento_iniciado_em is None:
        # Já estava pausado / sem sessão aberta
        estado.em_call_valida = False
        estado.call_entrada_em = None
        estado.segmento_iniciado_em = None
        estado.canal_atual_id = None
        if estado.toggle_ligado and estado.ocioso_desde is None:
            estado.ocioso_desde = datetime.now(timezone.utc)
        return

    canal_anterior_id = estado.canal_atual_id
    discord_id = estado.discord_id
    id_fivem_atual = estado.id_fivem

    # Só o trecho em que o cronômetro estava rodando (não surdo / não pausado)
    decorrido_segmento = _acumular_segmento_atual(estado)
    moedas_ganhas = _creditar_moedas_de_acumulado(estado)

    estado.em_call_valida = False
    estado.call_entrada_em = None
    estado.segmento_iniciado_em = None
    estado.canal_atual_id = None
    estado.ocioso_desde = datetime.now(timezone.utc)
    estado.lembrete_1_enviado = False
    estado.lembrete_2_enviado = False
    estado.lembrete_3_enviado = False
    estado.afk_mudo_surdo_desde = None
    estado.afk_canal_referencia_id = None
    estado.afk_aviso_enviado = False

    await registrar_evento_plantao(
        guild,
        discord_id,
        evento,
        id_fivem_atual,
        canal_id=canal_anterior_id,
        duracao_segundos=decorrido_segmento,
        campos_extra={"Motivo": motivo},
    )

    if moedas_ganhas > 0:
        await registrar_evento_plantao(
            guild,
            discord_id,
            "MOEDA_CREDITADA",
            estado.id_fivem,
            campos_extra={
                "Moedas Ganhas": str(moedas_ganhas),
                "Saldo Total": (
                    f"{estado.saldo_moedas} moedas "
                    f"({formatar_dinheiro(estado.saldo_moedas * VALOR_MOEDA_INGAME)})"
                ),
            },
        )
        try:
            from src.plantao.carteira_service import registrar_movimentacao

            await registrar_movimentacao(
                discord_id=discord_id,
                tipo="GANHO_PLANTAO",
                valor=moedas_ganhas,
                saldo_apos=int(estado.saldo_moedas),
                referencia=f"+{moedas_ganhas} plantão",
            )
        except Exception as erro_ao_registrar_ganho:
            # ATENCAO: aqui as moedas JA foram creditadas ao membro. Se o
            # extrato falha, o saldo e o extrato ficam divergentes, e isso
            # precisa gritar no log para alguem conferir na mao.
            await capturar_erro_e_logar(
                erro_ao_registrar_ganho,
                contexto=(
                    "registrar no extrato o ganho de plantao de "
                    f"{moedas_ganhas} moedas do membro {discord_id}"
                ),
            )


async def pausar_cronometro_moeda(
    estado: EstadoPlantao,
    *,
    motivo: str = "Pausa (surdo / AFK)",
) -> int:
    """
    Para de contar tempo para moeda sem sair da call.
    Acumula o segmento aberto e limpa segmento_iniciado_em.
    Retorna segundos fechados neste pause.
    """
    return _acumular_segmento_atual(estado)


def retomar_cronometro_moeda(estado: EstadoPlantao) -> None:
    """Reinicia segmento se está em call válida e ainda não há segmento aberto."""
    if estado.em_call_valida and estado.segmento_iniciado_em is None:
        estado.segmento_iniciado_em = datetime.now(timezone.utc)


async def fechar_segmento_parcial_do_ciclo(
    discord_id: int,
) -> tuple[int, int]:
    """
    Fecha o trecho aberto em log sem tirar o membro de serviço.

    Usado a cada 1 minuto pela task de plantão: o tempo do ciclo (horas
    em log_plantao) sobe enquanto a pessoa continua em call. Sem isso,
    só entrava no total ao sair de serviço / sair da call.

    - Grava LogPlantao com a duração do trecho (sem postar no canal de log)
    - Soma em segundos_acumulados e credita moedas se completou fração
    - Reinicia segmento_iniciado_em para continuar contando

    Devolve (segundos_fechados, moedas_ganhas).
    """
    from src.database.models import LogPlantao

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return 0, 0
        if not estado.toggle_ligado:
            return 0, 0
        if estado.segmento_iniciado_em is None:
            return 0, 0

        agora = datetime.now(timezone.utc)
        inicio = garantir_aware(estado.segmento_iniciado_em)
        decorrido = int((agora - inicio).total_seconds())

        if decorrido < 1:
            return 0, 0

        estado.segundos_acumulados = (
            int(estado.segundos_acumulados or 0) + decorrido
        )
        moedas_ganhas = _creditar_moedas_de_acumulado(estado)
        estado.segmento_iniciado_em = agora
        estado.ultima_atualizacao = agora

        sessao.add(
            LogPlantao(
                id_fivem=estado.id_fivem,
                discord_id=estado.discord_id,
                evento="CICLO_MINUTO",
                canal_id=None,
                duracao_segundos=decorrido,
                detalhes="Fechamento parcial a cada minuto (ainda em serviço)",
            )
        )
        await sessao.commit()

        saldo_apos = int(estado.saldo_moedas or 0)

    if moedas_ganhas > 0:
        try:
            from src.plantao.carteira_service import registrar_movimentacao

            await registrar_movimentacao(
                discord_id=discord_id,
                tipo="GANHO_PLANTAO",
                valor=moedas_ganhas,
                saldo_apos=saldo_apos,
                referencia=f"+{moedas_ganhas} plantão (ciclo minuto)",
            )
        except Exception as erro_extrato:
            await capturar_erro_e_logar(
                erro_extrato,
                contexto=(
                    "registrar no extrato o ganho parcial de plantão de "
                    f"{moedas_ganhas} moedas do membro {discord_id}"
                ),
            )

    return decorrido, moedas_ganhas


# ---------------------------------------------------------------------------
# Administração de estado (comandos /plantao)
# ---------------------------------------------------------------------------


def calcular_segundos_do_segmento_aberto(estado: EstadoPlantao | None) -> int:
    """
    Segundos do trecho ainda aberto (segmento_iniciado_em → agora).

    Só conta se o cronômetro está rodando: em call válida e sem pausa
    (surdo). Quem está pausado tem segmento_iniciado_em = None e devolve 0.
    """
    if estado is None:
        return 0
    if not estado.toggle_ligado:
        return 0
    if estado.segmento_iniciado_em is None:
        return 0

    inicio_do_segmento = garantir_aware(estado.segmento_iniciado_em)
    decorrido = int((datetime.now(timezone.utc) - inicio_do_segmento).total_seconds())
    if decorrido < 0:
        return 0
    return decorrido


async def calcular_segundos_historico_fechado(discord_id: int) -> int:
    """
    Soma só o tempo já gravado em log (segmentos fechados).

    Não inclui o trecho ainda aberto em call. O total sobe a cada minuto
    (evento CICLO_MINUTO) e também ao sair da call ou encerrar o serviço.
    """
    from sqlalchemy import func

    from src.database.models import LogPlantao

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.coalesce(func.sum(LogPlantao.duracao_segundos), 0)).where(
                LogPlantao.discord_id == discord_id,
                LogPlantao.duracao_segundos.is_not(None),
                LogPlantao.duracao_segundos > 0,
            )
        )
        return int(resultado.scalar_one() or 0)


async def calcular_segundos_plantao_atual(
    discord_id: int,
    estado: EstadoPlantao | None,
) -> int:
    """
    Tempo contado no plantão atual (desde o último TOGGLE_ON).

    - Logs fechados depois do último entrar em serviço
    - Mais o segmento ainda aberto (se o cronômetro estiver rodando)

    Pausa (surdo) zera o segmento aberto: o tempo para de crescer até
    retomar. Fora de serviço devolve 0.
    """
    from sqlalchemy import func

    from src.database.models import LogPlantao

    if estado is None or not estado.toggle_ligado:
        return 0

    async with async_session() as sessao:
        resultado_toggle = await sessao.execute(
            select(LogPlantao.criado_em)
            .where(
                LogPlantao.discord_id == discord_id,
                LogPlantao.evento == "TOGGLE_ON",
            )
            .order_by(LogPlantao.criado_em.desc())
            .limit(1)
        )
        inicio_do_plantao = resultado_toggle.scalar_one_or_none()

        if inicio_do_plantao is None:
            segundos_fechados = 0
        else:
            inicio_aware = garantir_aware(inicio_do_plantao)
            resultado_soma = await sessao.execute(
                select(func.coalesce(func.sum(LogPlantao.duracao_segundos), 0)).where(
                    LogPlantao.discord_id == discord_id,
                    LogPlantao.duracao_segundos.is_not(None),
                    LogPlantao.duracao_segundos > 0,
                    LogPlantao.criado_em >= inicio_aware,
                )
            )
            segundos_fechados = int(resultado_soma.scalar_one() or 0)

    segundos_abertos = calcular_segundos_do_segmento_aberto(estado)
    return segundos_fechados + segundos_abertos


async def consultar_estado_plantao(discord_id: int) -> EstadoPlantao | None:
    """Busca o registro de estado sem criar linha nova."""
    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        return resultado.scalar_one_or_none()


async def listar_em_servico(limite: int = 40) -> list[EstadoPlantao]:
    """Lista membros com toggle ligado (mais recentes primeiro)."""
    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao)
            .where(EstadoPlantao.toggle_ligado.is_(True))
            .order_by(EstadoPlantao.ultima_atualizacao.desc())
            .limit(limite)
        )
        return list(resultado.scalars().all())


async def admin_definir_moedas(discord_id: int, novo_saldo: int) -> EstadoPlantao:
    """Define o saldo de moedas (admin). Cria estado se não existir."""
    async with async_session() as session:
        estado = await _obter_ou_criar_estado(session, discord_id)
        saldo_antes = int(estado.saldo_moedas or 0)
        estado.saldo_moedas = max(0, int(novo_saldo))
        await session.commit()
        await session.refresh(estado)
        saldo_depois = int(estado.saldo_moedas)

    try:
        from src.plantao.carteira_service import registrar_movimentacao

        delta = saldo_depois - saldo_antes
        if delta != 0:
            await registrar_movimentacao(
                discord_id=discord_id,
                tipo="AJUSTE_STAFF",
                valor=delta,
                saldo_apos=saldo_depois,
                referencia="admin set_moedas",
            )
    except Exception as erro_ao_registrar_ajuste:
        # ATENCAO: o saldo JA foi alterado pelo administrador. Sem o extrato,
        # ninguem consegue auditar depois quem mexeu e por que.
        await capturar_erro_e_logar(
            erro_ao_registrar_ajuste,
            contexto=(
                "registrar no extrato o ajuste de moedas feito pela staff "
                f"no membro {discord_id}"
            ),
        )
    return estado


async def solicitar_troca_moedas(
    membro: discord.Member,
    quantidade_moedas: int,
) -> tuple[bool, str, int, int]:
    """
    Debita moedas do saldo e prepara dados para solicitação no canal de finanças.

    Retorna (ok, mensagem, saldo_restante, valor_ingame).
    Não envia mensagem no Discord — o painel/logger faz isso.
    """
    if quantidade_moedas <= 0:
        return False, "Informe uma quantidade maior que zero.", 0, 0

    async with async_session() as session:
        estado = await _obter_ou_criar_estado(session, membro.id)
        saldo_atual = int(estado.saldo_moedas or 0)
        if quantidade_moedas > saldo_atual:
            return (
                False,
                f"Saldo insuficiente. Você tem **{saldo_atual}** moeda(s).",
                saldo_atual,
                0,
            )

        estado.saldo_moedas = saldo_atual - quantidade_moedas
        await session.commit()
        saldo_restante = int(estado.saldo_moedas)
        id_fivem = estado.id_fivem

    valor_ingame = valor_ingame_de_moedas(quantidade_moedas, membro=membro)

    try:
        from src.plantao.carteira_service import registrar_movimentacao

        await registrar_movimentacao(
            discord_id=membro.id,
            tipo="TROCA_INGAME",
            valor=-quantidade_moedas,
            saldo_apos=saldo_restante,
            referencia=f"{quantidade_moedas} moedas → {formatar_dinheiro(valor_ingame)}",
        )
    except Exception as erro_ao_registrar_troca:
        # ATENCAO: as moedas JA foram debitadas. Sem o extrato, o membro pode
        # cobrar a troca e ninguem consegue provar que ela aconteceu.
        await capturar_erro_e_logar(
            erro_ao_registrar_troca,
            contexto=(
                f"registrar no extrato a troca de {quantidade_moedas} moedas "
                f"do membro {membro.id}"
            ),
            guilda=membro.guild,
        )

    # Log não pode derrubar a troca se falhar
    try:
        await registrar_evento_plantao(
            membro.guild,
            membro.id,
            "TROCA_MOEDAS_SOLICITADA",
            id_fivem,
            campos_extra={
                "Moedas trocadas": str(quantidade_moedas),
                "Valor in-game": formatar_dinheiro(valor_ingame),
                "Saldo restante": str(saldo_restante),
                "ID FiveM": id_fivem or "—",
            },
        )
    except Exception as erro_log:
        from src.utils.error_handling import enviar_erro_para_log_erros

        await enviar_erro_para_log_erros(
            membro.guild,
            "Troca de moedas — falha ao registrar evento de plantão",
            erro_log,
            contexto="solicitar_troca_moedas.registrar_evento_plantao",
            usuario=membro,
        )

    return (
        True,
        (
            f"**{quantidade_moedas}** moeda(s) → {formatar_dinheiro(valor_ingame)}. "
            f"Saldo restante: **{saldo_restante}**."
        ),
        saldo_restante,
        valor_ingame,
    )


def montar_corpo_solicitacao_troca_moedas(
    *,
    membro: discord.Member,
    id_fivem: str | None,
    quantidade_moedas: int,
    valor_ingame: int,
) -> tuple[str, str]:
    """
    Corpo Components V2 da solicitação de troca de moedas.
    Retorna (titulo, corpo_markdown) — o rodapé fica na view.
    """
    from src.utils.formatacao import formatar_data_solicitacao

    fid = id_fivem or "—"
    data_txt = formatar_data_solicitacao()
    cotacao = cotacao_moeda_do_membro(membro)
    valor_unitario_txt = formatar_reais(cotacao)
    valor_total_txt = formatar_reais(valor_ingame)

    titulo = "🏥 PAGAMENTO — TROCA DE MOEDAS"
    corpo = (
        f"👨‍⚕️ {membro.mention}　·　🆔 **FID:** `{fid}`\n"
        f"> - `🎯` **Origem:** Plantão\n"
        f"> - `💎` **Moedas:** x{quantidade_moedas} moedas\n"
        f"> - `💵` **Conversão:** {valor_unitario_txt} cada\n"
        f"> - `📅` **Data da Solicitação:** {data_txt}\n\n"
        f"• 💰 **Valor Total:** **{valor_total_txt}**\n"
        f"• 🧾 **Observações (se houver):** "
        f"_Troca de **{quantidade_moedas}** moeda(s) de plantão por dinheiro in-game "
        f"({valor_unitario_txt} cada). Discord `{membro.id}`._"
    )
    return titulo, corpo


# Alias legado (se algum import antigo ainda chamar o nome anterior)
def montar_texto_solicitacao_troca_moedas(
    *,
    membro: discord.Member,
    id_fivem: str | None,
    quantidade_moedas: int,
    valor_ingame: int,
) -> str:
    """Mantém o formato antigo de texto usando o construtor atual do card financeiro.

    Reúne título e corpo em uma única string para compatibilidade com importações
    antigas. Assim, integrações legadas continuam exibindo os mesmos dados sem duplicar
    a regra de cálculo e formatação da solicitação.
    """
    titulo, corpo = montar_corpo_solicitacao_troca_moedas(
        membro=membro,
        id_fivem=id_fivem,
        quantidade_moedas=quantidade_moedas,
        valor_ingame=valor_ingame,
    )
    return f"# {titulo}\n{corpo}"


async def admin_forcar_desligar(discord_id: int) -> bool:
    """
    Desliga toggle e zera campos de call/ociosidade no banco.
    Retorna False se o membro já estava desligado ou sem registro.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None or not estado.toggle_ligado:
            return False
        estado.toggle_ligado = False
        estado.em_call_valida = False
        estado.call_entrada_em = None
        estado.segmento_iniciado_em = None
        estado.canal_atual_id = None
        estado.ocioso_desde = None
        estado.lembrete_1_enviado = False
        estado.lembrete_2_enviado = False
        estado.lembrete_3_enviado = False
        estado.afk_mudo_surdo_desde = None
        estado.afk_aviso_enviado = False
        await session.commit()
        return True


async def admin_limpar_estado(discord_id: int) -> bool:
    """Apaga o registro de estado_plantao do membro. Retorna se existia."""
    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return False
        await session.delete(estado)
        await session.commit()
        return True
