"""
Painel fixo: quem está com plantão ligado neste momento.

Só monta o visual. A task busca os estados e edita a mensagem.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from src.config import FUSO_HORARIO_LOCAL
from src.database.models import EstadoPlantao
from src.plantao.plantao_service import calcular_segundos_plantao_atual
from src.utils.formatacao import formatar_hms

FUSO = ZoneInfo(FUSO_HORARIO_LOCAL)

# Discord limita o texto dos componentes; corta a lista se passar disso.
LIMITE_CARACTERES_CORPO = 3500
LIMITE_LINHAS = 40


async def montar_painel_plantao_ativo(
    estados: list[EstadoPlantao],
) -> discord.ui.LayoutView:
    """
    Monta o card com a lista de médicos em serviço.

    Cada linha: menção, FID, se está em call e o tempo do plantão atual.
    """
    agora = datetime.now(FUSO).strftime("%H:%M:%S")
    total = len(estados)

    if total == 0:
        texto_corpo = (
            "_Ninguém com plantão ligado no momento._\n\n"
            "Quando alguém bater ponto, o nome aparece aqui."
        )
        cor = discord.Color.dark_grey()
    else:
        linhas: list[str] = []
        for estado in estados[:LIMITE_LINHAS]:
            segundos = await calcular_segundos_plantao_atual(
                estado.discord_id,
                estado,
            )
            tempo = formatar_hms(segundos)
            fid = estado.id_fivem or "—"
            if estado.em_call_valida:
                marca = "🟢"
                situacao = "em call"
            else:
                marca = "🟡"
                situacao = "ocioso"

            linhas.append(
                f"{marca} <@{estado.discord_id}> · "
                f"FID `{fid}` · {situacao} · `{tempo}`"
            )

        texto_corpo = "\n".join(linhas)
        if total > LIMITE_LINHAS:
            texto_corpo += f"\n\n_… e mais {total - LIMITE_LINHAS} em serviço._"

        if len(texto_corpo) > LIMITE_CARACTERES_CORPO:
            texto_corpo = texto_corpo[:LIMITE_CARACTERES_CORPO] + "\n…"

        cor = discord.Color.green()

    texto_titulo = (
        f"# 🟢 Plantão ativo\n"
        f"**{total}** "
        f"{'médico' if total == 1 else 'médicos'} "
        f"em serviço agora"
    )
    texto_rodape = f"-# Atualizado em tempo real · {agora}"

    container = discord.ui.Container(
        discord.ui.TextDisplay(texto_titulo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(texto_corpo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(texto_rodape),
        accent_color=cor,
    )

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view
