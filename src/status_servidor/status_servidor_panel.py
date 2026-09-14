"""
Painel visual do status do servidor FiveM (Components V2).

Layout do card:

# Status do servidor
{nome} (+ thumbnail, se configurado)
> _Status_:
```
🟢 ONLINE / 🔴 OFFLINE
```
> _Jogadores_:
```yaml
 [ X/2048 ]
```
> _IP FiveM_:
```
connect valleyfivem.com
```
_Próximo restart_:
em Xhrs Ym
Atualizado em tempo real · horário
[ Conectar ]
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
    max_jogadores = int(dados.get("max_jogadores") or STATUS_SERVIDOR["MAX_JOGADORES"])
    nome = dados.get("nome") or STATUS_SERVIDOR["NOME_SERVIDOR"]
    texto_restart = calcular_proximo_restart()
    agora = datetime.now(FUSO).strftime("%H:%M:%S")

    if online:
        texto_status = "🟢 ONLINE"
        cor = discord.Color.green()
    else:
        texto_status = "🔴 OFFLINE"
        cor = discord.Color.red()

    connect = STATUS_SERVIDOR["CONNECT"]

    texto_corpo = (
        f"> __Status__:\n"
        f"```yaml\n{texto_status}\n```\n"
        f"> __Jogadores__:\n"
        f"```yaml\n [ {jogadores}/{max_jogadores} ]\n```\n"
        f"> __IP FiveM__:\n"
        f"```yaml\n{connect}\n```\n"
        f"__Próximo restart__:\n"
        f"```yaml\n{texto_restart}```"
    )

    if dados.get("erro") and not online:
        texto_corpo = f"{texto_corpo}\n\n_{dados['erro']}_"

    texto_rodape = f"-# Atualizado em tempo real · {agora}"

    componentes: list = []

    url_thumbnail = STATUS_SERVIDOR.get("URL_THUMBNAIL") or ""
    texto_titulo = f"# Status do servidor\n> ### **{nome}**\nConfira abaixo o status geral do servidor."

    # Mesmo padrão do plantao: Thumbnail recebe a URL por posição,
    # e Section recebe o texto do título (str), não TextDisplay.
    if url_thumbnail:
        componentes.append(
            discord.ui.Section(
                texto_titulo,
                accessory=discord.ui.Thumbnail(url_thumbnail),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(texto_titulo))

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
    componentes.append(discord.ui.TextDisplay(texto_corpo))
    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
    componentes.append(discord.ui.TextDisplay(texto_rodape))

    botao_conectar = discord.ui.Button(
        label="Conectar",
        style=discord.ButtonStyle.link,
        url=STATUS_SERVIDOR["LINK_CFX"],
    )
    linha_botao = discord.ui.ActionRow()
    linha_botao.add_item(botao_conectar)
    componentes.append(linha_botao)

    container = discord.ui.Container(
        *componentes,
        accent_color=cor,
    )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view
