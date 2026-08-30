"""Log visual de compartilhamento de tela em canal do Discord."""

from __future__ import annotations

import logging

import discord

from src.config import CANAIS
from src.utils.error_handling import ignorar_falha_cosmetica
from src.utils.log_container import LogContainerView
from src.utils.mensagens import COR_ERRO, COR_INFO, COR_SUCESSO

registrador = logging.getLogger(__name__)


async def publicar_log_sala_criada(
    guilda: discord.Guild,
    membro: discord.abc.User,
    codigo: str,
    link: str,
) -> None:
    """Publica no canal de log quando um membro gera um link."""
    canal_id = CANAIS.get("LOG_SCREENSHARE") or 0
    canal = guilda.get_channel(canal_id) if canal_id else None
    if canal is None:
        registrador.debug(
            "LOG_SCREENSHARE nao configurado (0); log visual ignorado."
        )
        return

    linhas = (
        f"Membro: {membro.mention} (`{membro.id}`)\n"
        f"Codigo: `{codigo}`\n"
        f"Link: {link}"
    )
    view = LogContainerView(
        titulo="Compartilhamento — sala criada",
        linhas=linhas,
        guild=guilda,
        cor=COR_SUCESSO,
    )
    try:
        await canal.send(view=view)
    except discord.HTTPException as erro_ao_publicar:
        ignorar_falha_cosmetica(
            erro_ao_publicar,
            o_que_falhou="publicar log de sala criada",
        )


async def publicar_log_erro_screenshare(
    guilda: discord.Guild | None,
    membro: discord.abc.User | None,
    contexto: str,
    detalhe: str,
) -> None:
    """Publica falha de compartilhamento no LOG_SCREENSHARE e no LOG_ERROS."""
    if guilda is None:
        return

    texto = (
        f"Contexto: `{contexto}`\n"
        f"Detalhe: {detalhe[:500]}"
    )
    if membro is not None:
        texto = f"Membro: {membro.mention} (`{membro.id}`)\n" + texto

    for chave, cor in (
        ("LOG_SCREENSHARE", COR_ERRO),
        ("LOG_ERROS", COR_ERRO),
    ):
        canal_id = CANAIS.get(chave) or 0
        canal = guilda.get_channel(canal_id) if canal_id else None
        if canal is None:
            continue
        view = LogContainerView(
            titulo="Compartilhamento — erro",
            linhas=texto,
            guild=guilda,
            cor=cor,
        )
        try:
            await canal.send(view=view)
        except discord.HTTPException as erro_ao_publicar:
            ignorar_falha_cosmetica(
                erro_ao_publicar,
                o_que_falhou=f"publicar erro screenshare em {chave}",
            )
