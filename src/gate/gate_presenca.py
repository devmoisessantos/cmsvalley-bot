# src/gate/gate_presenca.py
"""
Painel de lista de presença de um evento GATE.

Publica e atualiza a mensagem no canal de marcar presença.
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord

from src.config import (
    CANAIS,
    HIERARQUIA_GATE,
)
from src.database.connection import async_session
from src.database.models import EventosGate
from src.gate.gate_service import (
    buscar_evento_por_id,
    listar_presencas,
)


async def montar_container_presenca(
    bot: discord.Client,
    evento,
) -> discord.ui.Container:
    """Monta o Container visual da lista de presença (público para o bot.py)."""
    if hasattr(evento, "guild_id"):
        guilda = bot.get_guild(evento.guild_id)
    else:
        guilda = bot.guilds[0] if bot.guilds else None

    if guilda is None:
        return discord.ui.Container(
            discord.ui.TextDisplay("# 📋 Lista de Presença\nServidor não encontrado."),
            accent_colour=discord.Colour.green(),
        )

    membros_da_gate = [
        membro
        for membro in guilda.members
        if any(cargo.name in HIERARQUIA_GATE for cargo in membro.roles)
    ]

    presencas = await listar_presencas(evento.id)
    confirmados_por_id = {presenca.discord_id: presenca for presenca in presencas}

    membros_nao_confirmados = [
        membro for membro in membros_da_gate if membro.id not in confirmados_por_id
    ]

    url_do_icone = guilda.icon.url if guilda.icon else None

    texto_limite = ""
    if evento.limite_participantes and evento.limite_participantes > 0:
        texto_limite = (
            f"\n`👥` **Limite:** {len(confirmados_por_id)}/"
            f"{evento.limite_participantes}"
        )

    texto_cabecalho = (
        "# 📋 Lista de Presença\n"
        f"`✅` **Total de confirmados:** {len(confirmados_por_id)}"
        f"{texto_limite}\n"
        f"`👨‍⚕️` **Responsável pela lista:** <@{evento.responsavel_id}>"
    )

    container = discord.ui.Container(accent_colour=discord.Colour.green())

    if url_do_icone:
        container.add_item(
            discord.ui.Section(
                texto_cabecalho,
                accessory=discord.ui.Thumbnail(url_do_icone),
            )
        )
    else:
        container.add_item(discord.ui.TextDisplay(texto_cabecalho))

    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    if confirmados_por_id:
        linhas_confirmados = "\n".join(
            f"`✅` `{presenca.id_fivem}` — <@{discord_id}>"
            for discord_id, presenca in confirmados_por_id.items()
        )
    else:
        linhas_confirmados = "_ninguém confirmou ainda_"

    container.add_item(
        discord.ui.TextDisplay(f"**Marcou presença:**\n{linhas_confirmados}")
    )

    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    if membros_nao_confirmados:
        linhas_nao_confirmados = "\n".join(
            f"`❓` — <@{membro.id}>" for membro in membros_nao_confirmados
        )
    else:
        linhas_nao_confirmados = "_todos confirmaram_"

    container.add_item(
        discord.ui.TextDisplay(f"**Não confirmado:**\n{linhas_nao_confirmados}")
    )

    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    # Botão só faz sentido se o evento ainda está aberto
    if evento.status == "aberto":
        linha_do_botao = discord.ui.ActionRow()
        linha_do_botao.add_item(
            discord.ui.Button(
                label="✅ Confirmar Presença",
                style=discord.ButtonStyle.success,
                custom_id=f"presenca:{evento.id}",
            )
        )
        container.add_item(linha_do_botao)
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    else:
        container.add_item(
            discord.ui.TextDisplay("_Evento encerrado — presença fechada._")
        )
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    momento_atual = int(datetime.now(timezone.utc).timestamp())
    texto_rodape = f"-# {guilda.name} • <t:{momento_atual}:f>"
    container.add_item(discord.ui.TextDisplay(texto_rodape))

    return container


async def enviar_painel_presenca(bot: discord.Client, evento) -> bool:
    """
    Publica a lista de presença e grava message_id / channel_id no banco.

    Retorna True se publicou, False se o canal não existir.
    """
    canal = bot.get_channel(CANAIS["CANAL_MARCAR_PRESENCA_GATE"])
    if canal is None:
        print(
            "[GATE] Canal CANAL_MARCAR_PRESENCA_GATE não encontrado — "
            "painel de presença não enviado."
        )
        return False

    view_da_lista = discord.ui.LayoutView(timeout=None)
    container = await montar_container_presenca(bot, evento)
    view_da_lista.add_item(container)

    mensagem = await canal.send(view=view_da_lista)

    async with async_session() as sessao:
        evento_no_banco = await sessao.get(EventosGate, evento.id)
        if evento_no_banco is None:
            return True
        evento_no_banco.message_id = mensagem.id
        evento_no_banco.channel_id = canal.id
        await sessao.commit()

    return True


async def atualizar_painel_presenca(bot: discord.Client, evento_id: int) -> bool:
    """Reconstrói e edita a mensagem da lista de presença."""
    evento = await buscar_evento_por_id(evento_id)
    if evento is None:
        return False
    if not evento.channel_id or not evento.message_id:
        return False

    canal = bot.get_channel(evento.channel_id)
    if canal is None:
        return False

    try:
        mensagem = await canal.fetch_message(evento.message_id)
    except (discord.NotFound, discord.HTTPException):
        return False

    view_da_lista = discord.ui.LayoutView(timeout=None)
    container = await montar_container_presenca(bot, evento)
    view_da_lista.add_item(container)

    await mensagem.edit(view=view_da_lista)
    return True
