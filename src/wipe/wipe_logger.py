"""
Log visual do wipe no canal LOGS_WIPE.
"""

from __future__ import annotations

import logging

import discord

from src.config import CANAIS
from src.utils.log_container import LogContainerView

registrador = logging.getLogger(__name__)


async def publicar_relatorio_do_wipe(
    guilda: discord.Guild,
    titulo: str,
    linhas: list[str],
) -> None:
    """
    Publica o relatório do wipe no canal LOGS_WIPE.

    Quebra em várias mensagens se o texto for longo demais para o Discord.
    """
    id_canal = CANAIS.get("LOGS_WIPE")
    if not id_canal:
        registrador.info("[wipe] sem canal LOGS_WIPE — só log técnico")
        return

    canal = guilda.get_channel(id_canal)
    if canal is None or not isinstance(canal, discord.TextChannel):
        registrador.warning("[wipe] canal LOGS_WIPE não encontrado: %s", id_canal)
        return

    texto_base = "\n".join(linhas) if linhas else "_(sem detalhes)_"
    fatias: list[str] = []
    atual = ""
    for linha in texto_base.splitlines():
        if len(atual) + len(linha) + 1 > 1800 and atual:
            fatias.append(atual)
            atual = linha
        else:
            atual = f"{atual}\n{linha}" if atual else linha
    if atual:
        fatias.append(atual)

    try:
        for indice, fatia in enumerate(fatias):
            cabecalho = titulo if indice == 0 else f"{titulo} (cont.)"
            view = LogContainerView(
                titulo=cabecalho,
                linhas=fatia,
                guild=guilda,
                cor=discord.Color.dark_gold(),
            )
            await canal.send(view=view)
    except Exception as erro:
        registrador.exception("[wipe] falha ao publicar relatório: %s", erro)
