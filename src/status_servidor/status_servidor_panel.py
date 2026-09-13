"""
Painel visual do status do servidor FiveM (Components V2).

Mostra se o servidor está online ou offline, quantos jogadores estão
conectados, quanto falta para o próximo restart e um botão de conectar.
"""

from __future__ import annotations

import discord

from src.config import STATUS_SERVIDOR
from src.status_servidor.status_servidor_service import (
    calcular_proximo_restart,
)


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

    if online:
        texto_status = "ONLINE"
        cor = discord.Color.green()
    else:
        texto_status = "OFFLINE"
        cor = discord.Color.red()

    linhas = [
        f"**Servidor:** {nome}",
        f"**Status:** {texto_status}",
        f"**Jogadores:** {jogadores} / {max_jogadores}",
        f"**Próximo restart:** {texto_restart}",
        f"**Connect:** `{STATUS_SERVIDOR['CONNECT']}`",
    ]

    if dados.get("erro") and not online:
        linhas.append(f"\n_{dados['erro']}_")

    texto_corpo = "\n".join(linhas)

    botao_conectar = discord.ui.Button(
        label="Conectar",
        style=discord.ButtonStyle.link,
        url=STATUS_SERVIDOR["LINK_CFX"],
    )
    linha_botao = discord.ui.ActionRow(botao_conectar)

    container = discord.ui.Container(
        discord.ui.TextDisplay("# Status do servidor"),
        discord.ui.TextDisplay(texto_corpo),
        linha_botao,
        accent_color=cor,
    )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view
