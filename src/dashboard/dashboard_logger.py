"""
Log visual do domínio dashboard em canal do Discord.

Usa o padrão LogContainerView (Components V2) para a administração
acompanhar sinal do bot, sync de metas e falhas da fila.
"""

from __future__ import annotations

import logging

import discord

from src.config import CANAIS
from src.utils.log_container import LogContainerView
from src.utils.mensagens import COR_AVISO, COR_ERRO, COR_SUCESSO

registrador = logging.getLogger(__name__)


def _canal_de_log(guilda: discord.Guild) -> discord.abc.Messageable | None:
    """Resolve o canal de log do dashboard (ou None se não configurado)."""
    canal_id = int(CANAIS.get("CANAL_DASHBOARD") or 0)
    if not canal_id:
        return None
    canal = guilda.get_channel(canal_id)
    return canal


async def registrar_evento_dashboard(
    guilda: discord.Guild,
    *,
    titulo: str,
    linhas: str,
    sucesso: bool = True,
    aviso: bool = False,
) -> None:
    """
    Publica um card de log no canal do dashboard.

    Falha de publicação só gera warning técnico — nunca derruba a task.
    """
    canal = _canal_de_log(guilda)
    if canal is None:
        return

    if aviso:
        cor = COR_AVISO
    elif sucesso:
        cor = COR_SUCESSO
    else:
        cor = COR_ERRO

    view = LogContainerView(
        titulo=titulo,
        linhas=linhas,
        guild=guilda,
        cor=cor,
    )
    try:
        await canal.send(view=view)
    except Exception as falha:  # noqa: BLE001
        registrador.warning(
            "Não consegui publicar log do dashboard: %s",
            falha,
        )


async def log_sinal_do_bot_falhou(
    guilda: discord.Guild,
    detalhe: str,
) -> None:
    """Aviso quando o sinal do bot não chega na API."""
    await registrar_evento_dashboard(
        guilda,
        titulo="Dashboard · sinal do bot falhou",
        linhas=(
            "O bot não conseguiu registrar o sinal do bot na API.\n"
            f"Detalhe: `{detalhe}`\n"
            "Confira DASHBOARD_API_URL e se DASHBOARD_API_TOKEN "
            "é igual ao JWT_SECRET da API."
        ),
        sucesso=False,
    )


async def log_metas_publicadas(
    guilda: discord.Guild,
    quantidade: int,
) -> None:
    """Confirma republicação do painel de KPIs."""
    await registrar_evento_dashboard(
        guilda,
        titulo="Dashboard · metas sincronizadas",
        linhas=f"Painel de KPIs atualizado com **{quantidade}** meta(s).",
        sucesso=True,
    )


async def log_comando_fila(
    guilda: discord.Guild,
    tipo: str,
    resultado: str,
    *,
    erro: bool,
) -> None:
    """Registra execução de um comando vindo do site."""
    await registrar_evento_dashboard(
        guilda,
        titulo=f"Dashboard · comando `{tipo}`",
        linhas=resultado,
        sucesso=not erro,
        aviso=not erro and "desconhecido" in resultado.lower(),
    )
