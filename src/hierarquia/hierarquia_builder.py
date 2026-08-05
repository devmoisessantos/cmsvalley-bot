import math
from datetime import (
    datetime,
    timezone,
)

import discord


def montar_card_cargo(
    cargo: discord.Role, membros: list[discord.Member]
) -> discord.ui.Container:
    """Monta um único card para o cargo (sem paginação)"""
    if membros:
        linhas = "\n".join(f"- {membro.mention}" for membro in membros)
    else:
        linhas = "*Nenhum membro possui este cargo no momento.*"

    agora = int(datetime.now(timezone.utc).timestamp())
    rodape_texto = f"-# Atualizado automaticamente • <t:{agora}:f>"

    return discord.ui.Container(
        discord.ui.TextDisplay(f"# {cargo.name} - [{len(membros)} membros]"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(f"# {cargo.mention}"),
        discord.ui.TextDisplay(linhas),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(rodape_texto),
        accent_color=cargo.color if cargo.color.value != 0 else discord.Color.blurple(),
    )


def montar_cards_cargo_paginado(
    cargo: discord.Role, membros: list[discord.Member], max_por_pagina: int = 50
) -> list[discord.ui.Container]:
    """
    Retorna uma lista de Containers para um cargo (paginado se necessário)
    Cada Container tem o cabeçalho completo, e a lista de membros continua
    """

    if not membros:
        # Se não tem membros, retorna um único card com "Nenhum membro"
        return [montar_card_cargo(cargo, membros)]

    # Ordena membros por nome
    membros_ordenados = sorted(membros, key=lambda m: m.display_name.lower())
    total_membros = len(membros_ordenados)

    # Calcula quantas páginas são necessárias
    total_paginas = math.ceil(total_membros / max_por_pagina)
    cards = []

    for pagina in range(total_paginas):
        inicio = pagina * max_por_pagina
        fim = min(inicio + max_por_pagina, total_membros)
        membros_pagina = membros_ordenados[inicio:fim]

        # Monta a lista de membros desta página
        linhas = "\n".join(f"- {m.mention}" for m in membros_pagina)

        # Se tem mais de uma página, adiciona indicação
        if total_paginas > 1:
            cabecalho = f"# {cargo.name} - [{total_membros} membros] (Página {pagina + 1}/{total_paginas})"
        else:
            cabecalho = f"# {cargo.name} - [{total_membros} membros]"

        agora = int(datetime.now(timezone.utc).timestamp())
        rodape_texto = f"-# Atualizado automaticamente • <t:{agora}:f>"

        card = discord.ui.Container(
            discord.ui.TextDisplay(cabecalho),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(f"# {cargo.mention}"),
            discord.ui.TextDisplay(linhas),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(rodape_texto),
            accent_color=cargo.color
            if cargo.color.value != 0
            else discord.Color.blurple(),
        )
        cards.append(card)

    return cards
