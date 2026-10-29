"""
Relógio confiável para carimbos do Discord (<t:unix:R>).

Em container sem NTP (sem systemd/sudo) o relógio do sistema pode ficar
atrasado. O Discord compara o timestamp do card com o relógio do celular
do membro: se o bot gravar um Unix 2 minutos no passado, o rodapé já
nasce como "há 2 minutos".

Esta rotina:
1. Consulta a hora real via HTTP (cabeçalho Date ou API pública)
2. Guarda o desvio (segundos) entre o sistema e a hora real
3. Devolve time.time() + desvio para montar <t:...:R>

Se a rede falhar, usa o relógio local (melhor que travar o bot).
"""

from __future__ import annotations

import logging
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

import aiohttp

registrador = logging.getLogger(__name__)

# Desvio em segundos: real - local. Positivo = sistema atrasado.
_desvio_segundos: float = 0.0
_ultima_sincronizacao: float = 0.0
# Não martela a rede: no máximo uma sync a cada 10 minutos.
_INTERVALO_SINCRONIZACAO = 600.0


def timestamp_unix_agora() -> int:
    """
    Unix agora, corrigido pelo desvio medido na última sincronização.

    Pode ser chamado de código síncrono (montagem de rodapé).
    """
    return int(time.time() + _desvio_segundos)


async def sincronizar_relogio_com_rede() -> None:
    """
    Atualiza o desvio medindo a hora real na internet.

    Preferência:
    1. Cabeçalho Date de https://discord.com (mesma infra do cliente)
    2. Fallback: worldtimeapi.org em UTC
    """
    global _desvio_segundos, _ultima_sincronizacao

    agora_local = time.time()
    if (
        _ultima_sincronizacao > 0
        and agora_local - _ultima_sincronizacao < _INTERVALO_SINCRONIZACAO
    ):
        return

    hora_real = await _buscar_hora_real_http()
    if hora_real is None:
        registrador.warning(
            "Não consegui sincronizar o relógio pela rede; "
            "mantenho desvio atual de %.1f s.",
            _desvio_segundos,
        )
        return

    # Mede de novo o local perto da resposta para reduzir latência no desvio.
    local_no_fim = time.time()
    _desvio_segundos = hora_real - local_no_fim
    _ultima_sincronizacao = local_no_fim

    if abs(_desvio_segundos) >= 30:
        registrador.warning(
            "Relógio do sistema está %.0f s fora da hora real "
            "(desvio aplicado nos carimbos <t:R>).",
            _desvio_segundos,
        )
    else:
        registrador.info(
            "Relógio sincronizado; desvio = %.1f s.",
            _desvio_segundos,
        )


async def _buscar_hora_real_http() -> float | None:
    """Devolve Unix UTC da rede, ou None se tudo falhar."""
    timeout = aiohttp.ClientTimeout(total=8)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as sessao:
            async with sessao.head("https://discord.com") as resposta:
                cabecalho = resposta.headers.get("Date")
                if cabecalho:
                    momento = parsedate_to_datetime(cabecalho)
                    if momento.tzinfo is None:
                        momento = momento.replace(tzinfo=timezone.utc)
                    return momento.timestamp()
    except Exception as erro_discord:
        registrador.debug(
            "Hora via discord.com falhou: %s",
            erro_discord,
        )

    try:
        async with aiohttp.ClientSession(timeout=timeout) as sessao:
            async with sessao.get(
                "https://worldtimeapi.org/api/timezone/Etc/UTC"
            ) as resposta:
                if resposta.status != 200:
                    return None
                dados = await resposta.json(content_type=None)
                unixtime = dados.get("unixtime")
                if unixtime is not None:
                    return float(unixtime)
    except Exception as erro_api:
        registrador.debug(
            "Hora via worldtimeapi falhou: %s",
            erro_api,
        )

    return None
