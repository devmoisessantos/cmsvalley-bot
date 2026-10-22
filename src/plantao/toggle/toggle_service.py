"""
Regras de negócio do toggle médico do jogo.

Trata a log suja da cidade e decide o que fazer no plantão:

- Duplicata no mesmo segundo → ignora
- SAIU menos de 1 minuto depois de ENTROU → ruído, não desliga
- SAIU com plantão ligado e sem ser ruído → desliga o plantão
- ENTROU → agenda verificação de call em 5 minutos (aviso por DM)

O toggle do Discord continua sendo a fonte oficial para *ligar* o plantão.
A log só desliga e alerta.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import discord
from sqlalchemy import select

from src.config import (
    GUILD_ID,
    obter_todos_ids_canais_plantao,
)
from src.database.conexao import async_session
from src.database.models import (
    EstadoPlantao,
    Recrutamento,
    Usuario,
)
from src.plantao.plantao_service import (
    consultar_estado_plantao,
    desligar_servico,
)
from src.plantao.toggle.toggle_parse import EventoToggleParseado
from src.utils.notificacao import (
    notificar_dm_toggle_desligado_pelo_jogo,
    notificar_dm_toggle_sem_call,
)

registrador = logging.getLogger(__name__)

# Menos de 1 minuto entre ENTROU e SAIU = ruído da cidade
JANELA_RUIDO_EM_SEGUNDOS = 60
# Mesma ação no mesmo passaporte em poucos segundos = duplicata
JANELA_DUPLICATA_EM_SEGUNDOS = 3
# Tempo para entrar na call depois de ENTROU no jogo
SEGUNDOS_PARA_AVISO_DE_CALL = 5 * 60


@dataclass
class UltimoEventoMemoria:
    """Último evento visto por passaporte, só em memória (reinicia com o bot)."""

    acao: str
    momento: datetime


# id_fivem → último evento processado (deduplicata e ruído)
_ultimos_eventos: dict[str, UltimoEventoMemoria] = {}

# discord_id → tarefa asyncio da verificação de call em 5 min
_tarefas_aviso_call: dict[int, asyncio.Task] = {}

# id_fivem → momento do último ENTROU aceito (para janela de ruído do SAIU)
_ultimo_entrou_em: dict[str, datetime] = {}


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _para_utc(momento: datetime | None) -> datetime:
    """Usa o momento da log quando existe; senão, a hora atual em UTC."""
    if momento is None:
        return _agora_utc()
    if momento.tzinfo is None:
        return momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc)


def _eh_duplicata(
    id_fivem: str,
    acao: str,
    momento_utc: datetime,
) -> bool:
    """True se a mesma ação do mesmo passaporte veio de novo em poucos segundos."""
    anterior = _ultimos_eventos.get(id_fivem)
    if anterior is None:
        return False
    if anterior.acao != acao:
        return False
    diferenca = abs((momento_utc - anterior.momento).total_seconds())
    return diferenca < JANELA_DUPLICATA_EM_SEGUNDOS


def _eh_ruido_saida(id_fivem: str, momento_saida_utc: datetime) -> bool:
    """
    True se o SAIU veio menos de 1 minuto depois do último ENTROU do mesmo ID.

    A cidade costuma postar entra/sai em sequência rápida sem intenção real.
    """
    momento_entrou = _ultimo_entrou_em.get(id_fivem)
    if momento_entrou is None:
        return False
    diferenca = (momento_saida_utc - momento_entrou).total_seconds()
    if diferenca < 0:
        # Log fora de ordem: trata como ruído para não desligar à toa
        return True
    return diferenca < JANELA_RUIDO_EM_SEGUNDOS


def _registrar_evento_na_memoria(
    id_fivem: str,
    acao: str,
    momento_utc: datetime,
) -> None:
    _ultimos_eventos[id_fivem] = UltimoEventoMemoria(
        acao=acao,
        momento=momento_utc,
    )
    if acao == "ENTROU":
        _ultimo_entrou_em[id_fivem] = momento_utc


async def resolver_discord_id_por_fivem(id_fivem: str) -> int | None:
    """
    Acha o Discord a partir do passaporte FiveM.

    Ordem: Usuario → EstadoPlantao → último Recrutamento com esse ID.
    Não importa o miolo de outro domínio; consulta as tabelas direto.
    """
    id_texto = str(id_fivem).strip()
    async with async_session() as sessao:
        resultado_usuario = await sessao.execute(
            select(Usuario.discord_id)
            .where(Usuario.id_fivem == id_texto)
            .limit(1)
        )
        discord_id = resultado_usuario.scalar_one_or_none()
        if discord_id is not None:
            return int(discord_id)

        resultado_plantao = await sessao.execute(
            select(EstadoPlantao.discord_id)
            .where(EstadoPlantao.id_fivem == id_texto)
            .limit(1)
        )
        discord_id = resultado_plantao.scalar_one_or_none()
        if discord_id is not None:
            return int(discord_id)

        resultado_recrutamento = await sessao.execute(
            select(Recrutamento.discord_id_candidato)
            .where(
                Recrutamento.id_fivem == id_texto,
                Recrutamento.discord_id_candidato.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        discord_id = resultado_recrutamento.scalar_one_or_none()
        if discord_id is not None:
            return int(discord_id)

    return None


def _membro_esta_em_call_de_plantao(membro: discord.Member) -> bool:
    """True se o membro está em algum canal de voz configurado como plantão."""
    voz = membro.voice
    if voz is None or voz.channel is None:
        return False
    return voz.channel.id in obter_todos_ids_canais_plantao()


def _cancelar_aviso_de_call(discord_id: int) -> None:
    """Cancela a verificação de 5 minutos se ainda estiver pendente."""
    tarefa = _tarefas_aviso_call.pop(discord_id, None)
    if tarefa is not None and not tarefa.done():
        tarefa.cancel()


def _agendar_aviso_de_call(
    bot: discord.Client,
    guilda: discord.Guild,
    discord_id: int,
    id_fivem: str,
    nome_cidade: str,
) -> None:
    """
    Agenda a checagem de call 5 minutos depois do ENTROU.

    Se já houver tarefa para esse membro, cancela a antiga e cria outra.
    """
    _cancelar_aviso_de_call(discord_id)

    async def _verificar_depois() -> None:
        try:
            await asyncio.sleep(SEGUNDOS_PARA_AVISO_DE_CALL)
        except asyncio.CancelledError:
            return

        _tarefas_aviso_call.pop(discord_id, None)

        servidor = bot.get_guild(guilda.id) or bot.get_guild(int(GUILD_ID))
        if servidor is None:
            registrador.warning(
                "Toggle: guilda indisponível ao checar call de %s",
                id_fivem,
            )
            return

        membro = servidor.get_member(discord_id)
        if membro is None:
            registrador.info(
                "Toggle: membro %s (FiveM %s) não está no servidor na checagem",
                discord_id,
                id_fivem,
            )
            return

        if _membro_esta_em_call_de_plantao(membro):
            registrador.debug(
                "Toggle: %s já está em call de plantão — sem aviso",
                id_fivem,
            )
            return

        estado = await consultar_estado_plantao(discord_id)
        plantao_ligado = bool(estado is not None and estado.toggle_ligado)

        await notificar_dm_toggle_sem_call(
            membro,
            id_fivem=id_fivem,
            nome_cidade=nome_cidade,
            plantao_ja_ligado=plantao_ligado,
            guilda=servidor,
        )

    tarefa = asyncio.create_task(
        _verificar_depois(),
        name=f"toggle_aviso_call_{discord_id}",
    )
    _tarefas_aviso_call[discord_id] = tarefa


async def processar_evento_toggle(
    bot: discord.Client,
    guilda: discord.Guild,
    evento: EventoToggleParseado,
) -> str:
    """
    Aplica um evento parseado. Retorna um código curto do que foi feito
    (para log técnico), por exemplo: ignorado_duplicata, desligado, aviso_agendado.
    """
    momento_utc = _para_utc(evento.momento)
    id_fivem = evento.id_fivem

    if _eh_duplicata(id_fivem, evento.acao, momento_utc):
        registrador.debug(
            "Toggle: duplicata ignorada %s %s",
            id_fivem,
            evento.acao,
        )
        return "ignorado_duplicata"

    if evento.acao == "SAIU" and _eh_ruido_saida(id_fivem, momento_utc):
        registrador.info(
            "Toggle: SAIU de %s tratado como ruído (menos de 1 min após ENTROU)",
            id_fivem,
        )
        _registrar_evento_na_memoria(id_fivem, evento.acao, momento_utc)
        return "ignorado_ruido"

    _registrar_evento_na_memoria(id_fivem, evento.acao, momento_utc)

    discord_id = await resolver_discord_id_por_fivem(id_fivem)
    if discord_id is None:
        registrador.info(
            "Toggle: passaporte %s sem Discord vinculado (%s)",
            id_fivem,
            evento.acao,
        )
        return "sem_discord"

    membro = guilda.get_member(discord_id)
    if membro is None:
        # Tenta cache global do bot
        usuario = bot.get_user(discord_id)
        if usuario is not None and isinstance(usuario, discord.Member):
            membro = usuario
        else:
            try:
                membro = await guilda.fetch_member(discord_id)
            except (discord.NotFound, discord.HTTPException):
                membro = None

    if evento.acao == "ENTROU":
        if membro is None:
            registrador.info(
                "Toggle: ENTROU de %s (discord %s) sem membro no servidor",
                id_fivem,
                discord_id,
            )
            return "entrou_sem_membro"

        _agendar_aviso_de_call(
            bot,
            guilda,
            discord_id,
            id_fivem,
            evento.nome_cidade,
        )
        registrador.info(
            "Toggle: ENTROU %s → aviso de call em 5 min agendado para %s",
            id_fivem,
            discord_id,
        )
        return "aviso_agendado"

    # SAIU
    _cancelar_aviso_de_call(discord_id)

    estado = await consultar_estado_plantao(discord_id)
    if estado is None or not estado.toggle_ligado:
        registrador.debug(
            "Toggle: SAIU de %s mas plantão já estava desligado",
            id_fivem,
        )
        return "saida_sem_plantao"

    if membro is None:
        registrador.warning(
            "Toggle: SAIU de %s com plantão ligado, mas membro ausente — "
            "não desliga sem Member (auditoria incompleta)",
            id_fivem,
        )
        return "saida_sem_membro"

    try:
        mensagem_resultado = await desligar_servico(membro)
        registrador.info(
            "Toggle: plantão de %s (FiveM %s) desligado pela log do jogo: %s",
            discord_id,
            id_fivem,
            mensagem_resultado,
        )
        await notificar_dm_toggle_desligado_pelo_jogo(
            membro,
            id_fivem=id_fivem,
            nome_cidade=evento.nome_cidade,
            guilda=guilda,
        )
        return "desligado"
    except Exception as erro_ao_desligar:
        registrador.exception(
            "Toggle: falha ao desligar plantão de %s (FiveM %s): %s",
            discord_id,
            id_fivem,
            erro_ao_desligar,
        )
        return "erro_ao_desligar"


async def processar_eventos_toggle(
    bot: discord.Client,
    guilda: discord.Guild,
    eventos: list[EventoToggleParseado],
) -> list[str]:
    """Processa a lista de eventos na ordem em que aparecem na mensagem."""
    resultados: list[str] = []
    for evento in eventos:
        resultado = await processar_evento_toggle(bot, guilda, evento)
        resultados.append(resultado)
    return resultados
