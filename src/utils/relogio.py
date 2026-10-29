"""
Horário para carimbos ``<t:unix:R>`` — só a API do Discord.

O container pode ter relógio atrasado (sem NTP). O Discord compara o
timestamp do card com o relógio do celular do membro. Por isso o bot
não confia no ``time.time()`` do sistema para o rodapé.

Fonte única: cabeçalho ``Date`` de ``https://discord.com``.
Guarda o desvio (Discord − local) e devolve ``time.time() + desvio``.

Se a rede falhar uma vez, mantém o último desvio conhecido.
"""

from __future__ import annotations

import logging
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

import aiohttp

registrador = logging.getLogger(__name__)

# Discord − local. Positivo = sistema do bot atrasado.
_desvio_segundos: float = 0.0
_ultima_sincronizacao: float = 0.0
# Sync a cada 5 min (desvio pode crescer em containers sem NTP).
_INTERVALO_SINCRONIZACAO = 300.0


def timestamp_unix_agora() -> int:
    """
    Unix "agora" alinhado ao horário da API do Discord.

    Uso síncrono na montagem do rodapé ``<t:...:R>``.
    """
    return int(time.time() + _desvio_segundos)


async def sincronizar_relogio_com_rede() -> None:
    """
    Atualiza o desvio consultando só o Discord (cabeçalho Date).
    """
    global _desvio_segundos, _ultima_sincronizacao

    agora_local = time.time()
    if (
        _ultima_sincronizacao > 0
        and agora_local - _ultima_sincronizacao < _INTERVALO_SINCRONIZACAO
    ):
        return

    hora_discord = await _hora_unix_do_discord()
    if hora_discord is None:
        registrador.warning(
            "Não consegui ler a hora do Discord; mantenho desvio atual de %.1f s.",
            _desvio_segundos,
        )
        return

    local_no_fim = time.time()
    _desvio_segundos = hora_discord - local_no_fim
    _ultima_sincronizacao = local_no_fim

    if abs(_desvio_segundos) >= 30:
        registrador.warning(
            "Relógio local %.0f s fora do Discord "
            "(desvio aplicado nos carimbos <t:R>).",
            _desvio_segundos,
        )
    else:
        registrador.info(
            "Relógio alinhado ao Discord; desvio = %.1f s.",
            _desvio_segundos,
        )


async def _hora_unix_do_discord() -> float | None:
    """Unix UTC a partir do cabeçalho Date de discord.com."""
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sessao:
            async with sessao.head("https://discord.com") as resposta:
                cabecalho = resposta.headers.get("Date")
                if not cabecalho:
                    return None
                momento = parsedate_to_datetime(cabecalho)
                if momento.tzinfo is None:
                    momento = momento.replace(tzinfo=timezone.utc)
                return momento.timestamp()
    except Exception as erro_discord:
        registrador.debug(
            "Hora via discord.com falhou: %s",
            erro_discord,
        )
        return None
