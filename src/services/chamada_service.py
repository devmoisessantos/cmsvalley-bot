import re
import discord
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, func

from src.config import (
    TIMEZONE_LOCAL, RR_HORARIOS, INTERVALO_CHAMADA_MINUTOS,
    CARGOS_PUNICOES, CANAIS
)
from src.utils.log_container import LogContainerView
from src.utils.logger import log_mudanca_cargo
from src.database.connection import async_session
from src.database.models import ControleChamada, Recrutamento, FaltaChamada
from src.services.plantao_service import garantir_aware


ORDEM_PUNICOES = [
    "⛔┇ADV VERBAL ",   # falta 1 → aplicado direto
    "🚫┇Adv 01",        # falta 3 → aplicado (falta 2 só avisa que vem essa)
    "🚫┇Adv 02",        # falta 5 → aplicado (falta 4 só avisa)
    "🚫┇Adv 03",        # falta 7 → aplicado (falta 6 só avisa)
    "🚫┇Exonerado",     # falta 9 → aplicado (falta 8 só avisa)
]

_PADRAO_LINHA_EMS = re.compile(r"(\d{3,7})\s*[:.\-]\s*(.+)")
CONFIANCA_MINIMA_AUTOMATICA = 0.5  # abaixo disso, marca a entrada como "revisar manualmente"
# ─────────────────────────────────────────────
# 1) Cooldown / janela liberada
# ─────────────────────────────────────────────

def _limite_apos_rr_mais_recente(agora_utc: datetime) -> datetime:
    """Encontra o horário mais recente de 'RR + 1h' que já passou (olha hoje e ontem,
    pra cobrir o caso de agora ser antes do primeiro RR do dia)."""
    tz = ZoneInfo(TIMEZONE_LOCAL)
    agora_local = agora_utc.astimezone(tz)
    candidatos = []

    for offset_dias in (0, -1):
        dia_ref = (agora_local + timedelta(days=offset_dias)).date()
        for horario_str in RR_HORARIOS:
            hora, minuto = map(int, horario_str.split(":"))
            rr_dt = datetime(dia_ref.year, dia_ref.month, dia_ref.day, hora, minuto, tzinfo=tz)
            limite = rr_dt + timedelta(hours=1)
            if limite <= agora_local:
                candidatos.append(limite)

    if not candidatos:
        return agora_local - timedelta(days=1)  # fallback improvável

    return max(candidatos).astimezone(timezone.utc)


async def obter_controle_chamada(session) -> ControleChamada:
    resultado = await session.execute(select(ControleChamada).where(ControleChamada.id == 1))
    controle = resultado.scalar_one_or_none()
    if controle is None:
        controle = ControleChamada(id=1)
        session.add(controle)
        await session.flush()
    return controle


async def calcular_proximo_horario_permitido() -> tuple[datetime, bool]:
    """Retorna (próximo horário permitido em UTC, já liberado agora?)."""
    agora = datetime.now(timezone.utc)
    async with async_session() as session:
        controle = await obter_controle_chamada(session)
        limite_rr = _limite_apos_rr_mais_recente(agora)

        if controle.ultima_chamada_em is None:
            proximo = limite_rr
        else:
            proximo_por_intervalo = garantir_aware(controle.ultima_chamada_em) + timedelta(
                minutes=INTERVALO_CHAMADA_MINUTOS
            )
            proximo = max(limite_rr, proximo_por_intervalo)

        await session.commit()

    return proximo, agora >= proximo


# ─────────────────────────────────────────────
# 2) Lock de concorrência (só um Doutor por vez)
# ─────────────────────────────────────────────

async def tentar_iniciar_chamada(doutor_id: int) -> tuple[bool, int | None]:
    """Tenta pegar o lock. Retorna (conseguiu, id_do_doutor_que_ja_esta_chamando_se_falhou)."""
    async with async_session() as session:
        controle = await obter_controle_chamada(session)

        if controle.chamada_em_andamento:
            outro_doutor_id = controle.doutor_em_chamada_id
            await session.commit()
            return False, outro_doutor_id

        controle.chamada_em_andamento = True
        controle.doutor_em_chamada_id = doutor_id
        controle.chamada_iniciada_em = datetime.now(timezone.utc)
        await session.commit()
        return True, None


async def finalizar_chamada(marcar_ultima_chamada: bool = True):
    """Libera o lock. Se marcar_ultima_chamada=True, também atualiza o cooldown global
    (chamado só quando a chamada é concluída de verdade, não em caso de cancelamento)."""
    async with async_session() as session:
        controle = await obter_controle_chamada(session)
        controle.chamada_em_andamento = False
        controle.doutor_em_chamada_id = None
        controle.chamada_iniciada_em = None
        if marcar_ultima_chamada:
            controle.ultima_chamada_em = datetime.now(timezone.utc)
        await session.commit()


# ─────────────────────────────────────────────
# 3) Parser do texto extraído do print do /ems
# ─────────────────────────────────────────────
def extrair_entradas_do_ems(linhas_com_confianca: list[tuple[str, float]]) -> dict:
    """Recebe a saída do EasyOCR (texto, confiança) e retorna as entradas separadas
    entre confiáveis (aceitas automaticamente) e de baixa confiança (o Doutor confere/corrige)."""
    confiaveis: list[dict] = []
    revisar: list[dict] = []
    ids_ja_vistos: set[str] = set()

    for texto, confianca in linhas_com_confianca:
        texto = texto.strip()
        if not texto:
            continue

        match = _PADRAO_LINHA_EMS.search(texto)
        if not match:
            continue

        id_fivem = match.group(1).strip()
        if id_fivem in ids_ja_vistos:
            continue
        ids_ja_vistos.add(id_fivem)

        entrada = {"id_fivem": id_fivem, "nome_ems": match.group(2).strip(), "confianca": confianca}

        if confianca >= CONFIANCA_MINIMA_AUTOMATICA:
            confiaveis.append(entrada)
        else:
            revisar.append(entrada)

    return {"confiaveis": confiaveis, "revisar": revisar}
# ─────────────────────────────────────────────
# 4) Cruzamento com o banco — separa SUL (reconhecido) de NORTE/desconhecido
# ─────────────────────────────────────────────

async def cruzar_entradas_com_banco(entradas: list[tuple[str, str]]) -> dict:
    """Pra cada (id_fivem, nome) do print, verifica se existe Recrutamento aprovado
    com esse id_fivem — se sim, é considerado membro do SUL. Caso contrário, vai
    pra 'nao_reconhecidos' (pode ser Hospital Norte, ou alguém não recrutado)."""
    reconhecidos: list[dict] = []
    nao_reconhecidos: list[dict] = []

    async with async_session() as session:
        for id_fivem, nome_ems in entradas:
            resultado = await session.execute(
                select(Recrutamento.discord_id_candidato)
                .where(Recrutamento.id_fivem == id_fivem, Recrutamento.status == "APROVADO")
                .order_by(Recrutamento.id.desc())
                .limit(1)
            )
            discord_id = resultado.scalar_one_or_none()

            if discord_id is not None:
                reconhecidos.append({"id_fivem": id_fivem, "nome_ems": nome_ems, "discord_id": discord_id})
            else:
                nao_reconhecidos.append({"id_fivem": id_fivem, "nome_ems": nome_ems})

    return {"reconhecidos": reconhecidos, "nao_reconhecidos": nao_reconhecidos}


# ─────────────────────────────────────────────
# 5) Contagem de faltas / advertência automática
# ─────────────────────────────────────────────
def _calcular_estado_punicao(total_faltas: int) -> dict:
    """Ímpar = aplica o próximo tier. Par = só avisa qual tier vem se faltar de novo."""
    indice_aplicar = min((total_faltas - 1) // 2, len(ORDEM_PUNICOES) - 1)

    if total_faltas % 2 == 1:
        return {"aplicar": True, "cargo_nome": ORDEM_PUNICOES[indice_aplicar]}

    indice_aviso = min(total_faltas // 2, len(ORDEM_PUNICOES) - 1)
    return {"aplicar": False, "cargo_aviso_nome": ORDEM_PUNICOES[indice_aviso]}


async def registrar_falta(discord_id: int, chamada_id: int, motivo: str, guild) -> int:
    async with async_session() as session:
        session.add(FaltaChamada(discord_id=discord_id, chamada_id=chamada_id, motivo=motivo))
        await session.commit()

        resultado = await session.execute(
            select(func.count()).select_from(FaltaChamada).where(FaltaChamada.discord_id == discord_id)
        )
        total_faltas = resultado.scalar_one()

    estado_punicao = _calcular_estado_punicao(total_faltas)

    if estado_punicao["aplicar"]:
        await _aplicar_punicao(guild, discord_id, total_faltas, estado_punicao["cargo_nome"])
    else:
        await _avisar_proxima_punicao(guild, discord_id, total_faltas, estado_punicao["cargo_aviso_nome"])

    return total_faltas


async def _aplicar_punicao(guild, discord_id: int, total_faltas: int, cargo_nome: str):
    membro = guild.get_member(discord_id)
    cargo_id = CARGOS_PUNICOES.get(cargo_nome)
    if membro is None or cargo_id is None:
        return

    cargo = guild.get_role(cargo_id)
    if cargo is None:
        return

    # Remove tiers anteriores de punição, pra não acumular vários cargos ao mesmo tempo
    cargos_punicao_ids = set(CARGOS_PUNICOES.values())
    cargos_antigos = [r for r in membro.roles if r.id in cargos_punicao_ids and r.id != cargo.id]
    if cargos_antigos:
        try:
            await membro.remove_roles(*cargos_antigos, reason="Escalonamento de punição por faltas")
        except discord.Forbidden:
            pass

    try:
        await membro.add_roles(cargo, reason=f"{total_faltas}ª falta em chamada")
    except discord.Forbidden:
        pass

    await log_mudanca_cargo(
        guild, candidato=membro, executor=guild.me,
        cargos_adicionados=[cargo.mention],
        cargos_removidos=[r.mention for r in cargos_antigos] if cargos_antigos else None,
    )

    canal = guild.get_channel(CANAIS.get("CANAL_ADVERTENCIAS"))
    if canal:
        linhas = (
            f"- **Membro:** {membro.mention} (`{membro.id}`)\n"
            f"- **Total de Faltas:** {total_faltas}\n"
            f"- **Punição Aplicada:** {cargo_nome.strip()}"
        )
        view = LogContainerView(
            titulo="🚫 Punição Aplicada por Faltas em Chamada",
            linhas=linhas,
            guild=guild,
            cor=discord.Color.red(),
            avatar_url=membro.display_avatar.url,
        )
        await canal.send(view=view)


async def _avisar_proxima_punicao(guild, discord_id: int, total_faltas: int, cargo_aviso_nome: str):
    membro = guild.get_member(discord_id)
    if membro is None:
        return

    try:
        await membro.send(
            f"⚠️ Você já soma **{total_faltas} faltas** em chamadas de plantão. "
            f"Se faltar novamente, receberá **{cargo_aviso_nome.strip()}**."
        )
    except discord.Forbidden:
        pass