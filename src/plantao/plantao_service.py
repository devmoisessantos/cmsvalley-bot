from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select

from src.config import (
    CARGOS,
    CARGOS_DOUTOR_OU_ACIMA,
    CARGOS_HIERARQUIA,
    SEGUNDOS_PARA_MOEDA,
    VALOR_MOEDA_INGAME,
    obter_todos_ids_canais_plantao,
)
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from src.plantao.plantao_logger import registrar_evento_plantao
from src.utils.formatacao import (
    formatar_dinheiro,
    formatar_reais,
)


def garantir_aware(dt: datetime) -> datetime:
    """Se o datetime veio sem timezone (naive), assume que já era UTC e anexa isso."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def membro_e_doutor_ou_acima(membro: discord.Member) -> bool:
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


def membro_pode_informar_id_manualmente(membro: discord.Member) -> bool:
    """True se o membro tiver algum cargo da hierarquia (Visitante já está fora dessa lista)."""
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
            estado.segmento_iniciado_em = agora
            estado.canal_atual_id = canal_atual.id
            estado.ocioso_desde = None  # já entrou contando, não está ocioso
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

    await registrar_evento_plantao(
        membro.guild,
        membro.id,
        "TOGGLE_OFF",
        id_fivem_atual,
        campos_extra={
            "Saldo Final": f"{saldo_final} moedas ({formatar_dinheiro(saldo_final * VALOR_MOEDA_INGAME)})"
        },
    )

    return "✅ Você saiu de serviço. Cronômetro encerrado."


async def _finalizar_periodo_em_call(
    estado: EstadoPlantao,
    guild: discord.Guild,
    evento: str = "SAIU_CALL",
    motivo: str = "Saiu da call de voz",
):
    """Fecha o segmento de call atual: loga a duração, credita moedas se aplicável, e reinicia o estado ocioso."""
    if estado.call_entrada_em is None:
        return

    entrada_total = garantir_aware(estado.call_entrada_em)
    decorrido_total = (datetime.now(timezone.utc) - entrada_total).total_seconds()

    inicio_segmento = (
        garantir_aware(estado.segmento_iniciado_em)
        if estado.segmento_iniciado_em
        else entrada_total
    )
    decorrido_segmento = int(
        (datetime.now(timezone.utc) - inicio_segmento).total_seconds()
    )

    canal_anterior_id = estado.canal_atual_id
    discord_id = estado.discord_id
    id_fivem_atual = estado.id_fivem

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
                "Saldo Total": f"{estado.saldo_moedas} moedas ({formatar_dinheiro(estado.saldo_moedas * VALOR_MOEDA_INGAME)})",
            },
        )


# ---------------------------------------------------------------------------
# Administração de estado (comandos /plantao)
# ---------------------------------------------------------------------------


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
        estado.saldo_moedas = max(0, int(novo_saldo))
        await session.commit()
        await session.refresh(estado)
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

    valor_ingame = quantidade_moedas * VALOR_MOEDA_INGAME

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
    from datetime import (
        datetime,
        timezone,
    )
    from zoneinfo import ZoneInfo

    from src.config import (
        MESES_ABREV,
        TIMEZONE_LOCAL,
    )

    fid = id_fivem or "—"
    agora_local = datetime.now(timezone.utc).astimezone(ZoneInfo(TIMEZONE_LOCAL))
    data_txt = (
        f"{agora_local.day} de {MESES_ABREV[agora_local.month]} "
        f"{agora_local.year} {agora_local.strftime('%H:%M')}"
    )
    valor_unitario_txt = formatar_reais(VALOR_MOEDA_INGAME)
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
