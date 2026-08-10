"""Cards de alerta e DM do domínio baú."""

from __future__ import annotations

import discord

from src.bau.bau_views import (
    ViewCasoBau,
    ViewDmDevolucao,
)
from src.config import CANAIS
from src.database.models import CasoBau
from src.utils.log_container import LogContainerView


async def publicar_alerta_caso(
    guild: discord.Guild,
    caso: CasoBau,
    *,
    limite_1: int,
    limite_2: int | None,
    atualizar_mensagem_id: int | None = None,
) -> discord.Message | None:
    canal = guild.get_channel(CANAIS.get("CANAL_ALERTA_BAU") or 0)
    if canal is None:
        print("⚠️ [bau] CANAL_ALERTA_BAU não configurado (ID 0 ou inválido)")
        return None

    view = ViewCasoBau.montar_layout_alerta(
        caso,
        guild=guild,
        limite_1=limite_1,
        limite_2=limite_2,
    )
    if atualizar_mensagem_id:
        try:
            mensagem_antiga = await canal.fetch_message(atualizar_mensagem_id)
            await mensagem_antiga.edit(view=view)
            return mensagem_antiga
        except discord.HTTPException:
            pass
    return await canal.send(view=view)


async def enviar_dm_excesso(
    membro: discord.Member,
    caso: CasoBau,
) -> bool:
    view_dm = ViewDmDevolucao(caso_id=caso.id, guild_id=membro.guild.id)
    try:
        await membro.send(view=view_dm)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def log_item_desconhecido(guild: discord.Guild, nome: str, id_fivem: str) -> None:
    canal = guild.get_channel(CANAIS.get("LOG_ERROS") or 0)
    if canal is None:
        return
    view = LogContainerView(
        titulo="Baú — item desconhecido no log",
        linhas=f"- **ID FiveM:** `{id_fivem}`\n- **Item bruto:** `{nome}`",
        guild=guild,
        cor=discord.Color.yellow(),
    )
    await canal.send(view=view)


async def log_parse_falhou(guild: discord.Guild, conteudo: str) -> None:
    canal = guild.get_channel(CANAIS.get("LOG_ERROS") or 0)
    if canal is None:
        return
    trecho = (conteudo or "")[:800]
    view = LogContainerView(
        titulo="Baú — log ilegível (parse falhou)",
        linhas=f"```\n{trecho}\n```",
        guild=guild,
        cor=discord.Color.red(),
    )
    await canal.send(view=view)


async def log_verbal_aplicada(
    guild: discord.Guild,
    *,
    caso: CasoBau,
    tipo: str,
) -> None:
    from src.bau.bau_service import (
        formatar_bloco_itens_yaml,
        ler_itens_do_caso,
    )

    canal = guild.get_channel(CANAIS.get("CANAL_ALERTA_BAU") or 0)
    if canal is None:
        return
    mapa_itens = ler_itens_do_caso(caso)
    view = LogContainerView(
        titulo=f"Baú — {tipo} · prazo estourado",
        linhas=(
            f"- **Caso:** `#{caso.id}`\n"
            f"- **FiveM:** `{caso.id_fivem}`\n"
            f"- **Itens:**\n{formatar_bloco_itens_yaml(mapa_itens)}\n"
            f"- **Membro:** "
            + (f"<@{caso.discord_id}>" if caso.discord_id else "_sem discord_")
            + "\n- Botão **Ocorrência Valley** liberado no card."
        ),
        guild=guild,
        cor=discord.Color.red(),
    )
    await canal.send(view=view)
