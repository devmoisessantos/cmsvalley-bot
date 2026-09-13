"""
Painel visual do status do servidor FiveM (Components V2).

Layout do card:

- Título com nome do servidor (e thumbnail, se configurado)
- Status ONLINE / OFFLINE com indicador
- Jogadores no formato [ atual/máximo ]
- IP / connect
- Próximo restart
- Rodapé "Atualizado em tempo real"
- Botão Conectar (link CFX)
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from src.config import (
    FUSO_HORARIO_LOCAL,
    STATUS_SERVIDOR,
)
from src.status_servidor.status_servidor_service import (
    calcular_proximo_restart,
)

FUSO = ZoneInfo(FUSO_HORARIO_LOCAL)


def montar_painel_status(dados: dict) -> discord.ui.LayoutView:
    """
    Monta o card de status a partir dos dados já buscados pelo service.

    Não faz consulta de rede: só monta o visual. Assim a task e o comando
    controlam quando buscar e quando editar a mensagem.
    """
    online = bool(dados.get("online"))
    jogadores = int(dados.get("jogadores") or 0)
    max_jogadores = int(
        dados.get("max_jogadores") or STATUS_SERVIDOR["MAX_JOGADORES"]
    )
    nome = dados.get("nome") or STATUS_SERVIDOR["NOME_SERVIDOR"]
    texto_restart = calcular_proximo_restart()
    agora = datetime.now(FUSO).strftime("%H:%M:%S")

    if online:
        texto_status = "🟢 ONLINE"
        cor = discord.Color.green()
    else:
        texto_status = "🔴 OFFLINE"
        cor = discord.Color.red()

    texto_jogadores = f"[ {jogadores}/{max_jogadores} ]"
    connect = STATUS_SERVIDOR["CONNECT"]

    linhas_corpo = [
        f"**Status**\n{texto_status}",
        f"**Jogadores**\n`{texto_jogadores}`",
        f"**IP FiveM**\n`{connect}`",
        f"**Próximo Restart**\n{texto_restart}",
    ]

    if dados.get("erro") and not online:
        linhas_corpo.append(f"_{dados['erro']}_")

    texto_corpo = "\n\n".join(linhas_corpo)
    texto_rodape = f"-# Atualizado em tempo real · {agora}"

    componentes: list = []

    # Título + thumbnail opcional (ícone do servidor / hospital)
    url_thumbnail = STATUS_SERVIDOR.get("URL_THUMBNAIL")
    texto_titulo = f"# {nome}"

    if url_thumbnail:
        componentes.append(
            discord.ui.Section(
                texto_titulo,
                accessory=discord.ui.Thumbnail(url=url_thumbnail),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(texto_titulo))

    componentes.append(
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
    )
    componentes.append(discord.ui.TextDisplay(texto_corpo))
    componentes.append(
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
    )
    componentes.append(discord.ui.TextDisplay(texto_rodape))

    botao_conectar = discord.ui.Button(
        label="Conectar",
        style=discord.ButtonStyle.link,
        url=STATUS_SERVIDOR["LINK_CFX"],
    )
    componentes.append(discord.ui.ActionRow(botao_conectar))

    container = discord.ui.Container(
        *componentes,
        accent_color=cor,
    )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view
