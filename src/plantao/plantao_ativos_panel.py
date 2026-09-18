"""
Painel fixo: quem está com plantão ligado neste momento.

Só monta o visual. A task busca os estados e edita a mensagem.

Status:
- 🟢 em call válida, contando tempo
- 🟡 em call, mas mudo/surdo (tempo pausado)
- 🔴 plantão ligado, fora de call
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

# Discord limita texto dos componentes; parte a lista se passar disso.
LIMITE_CARACTERES_CORPO = 3500
ENTRADAS_POR_CARD = 25

STATUS_EM_CALL = "em_call"
STATUS_MUDO_SURDO = "mudo_surdo"
STATUS_FORA_CALL = "fora_call"

ORDEM_STATUS = (STATUS_EM_CALL, STATUS_MUDO_SURDO, STATUS_FORA_CALL)

META_STATUS = {
    STATUS_EM_CALL: {
        "marca": "🟢",
        "titulo": "Em call",
        "detalhe": "Canal de voz · tempo contando",
    },
    STATUS_MUDO_SURDO: {
        "marca": "🟡",
        "titulo": "Mudo / surdo",
        "detalhe": "Na call, tempo pausado",
    },
    STATUS_FORA_CALL: {
        "marca": "🔴",
        "titulo": "Fora de call",
        "detalhe": "Plantão ligado, sem canal de voz",
    },
}


def _classificar_status(estado: EstadoPlantao) -> str:
    """
    Define a cor/status a partir do estado gravado.

    Em call + segmento aberto → verde (contando).
    Em call sem segmento (surdo/mudo) → amarelo (pausado).
    Fora de call → vermelho.
    """
    if not estado.em_call_valida:
        return STATUS_FORA_CALL
    if estado.segmento_iniciado_em is not None and estado.afk_mudo_surdo_desde is None:
        return STATUS_EM_CALL
    return STATUS_MUDO_SURDO


def _linha_membro(
    estado: EstadoPlantao,
    *,
    segundos: int,
    status: str,
) -> str:
    """Uma entrada legível do médico em serviço."""
    meta = META_STATUS[status]
    marca = meta["marca"]
    fid = estado.id_fivem or "—"
    tempo = formatar_hms(segundos)

    if status == STATUS_EM_CALL:
        if estado.canal_atual_id:
            onde = f"Em call · <#{estado.canal_atual_id}>"
        else:
            onde = "Em call · canal de voz"
        return f"{marca} <@{estado.discord_id}> · FID `{fid}`\n↳ {onde} · **`{tempo}`**"

    if status == STATUS_MUDO_SURDO:
        if estado.canal_atual_id:
            onde = f"Na call · <#{estado.canal_atual_id}>"
        else:
            onde = "Na call"
        return (
            f"{marca} <@{estado.discord_id}> · FID `{fid}`\n"
            f"↳ {onde} · mudo/surdo · tempo pausado · **`{tempo}`**"
        )

    return (
        f"{marca} <@{estado.discord_id}> · FID `{fid}`\n"
        f"↳ Fora de call · plantão ligado · **`{tempo}`**"
    )


def _montar_resumo(
    total: int,
    qtd_em_call: int,
    qtd_mudo: int,
    qtd_fora: int,
) -> str:
    """Bloco de resumo no topo do painel."""
    if total == 0:
        return (
            "# 🩺 Plantão ativo\n"
            "**Ninguém** em serviço no momento\n"
            "-# Quando alguém bater ponto, aparece aqui."
        )

    palavra = "médico" if total == 1 else "médicos"
    return (
        f"# 🩺 Plantão ativo\n"
        f"**{total}** {palavra} em serviço agora\n"
        f"🟢 **{qtd_em_call}** em call · "
        f"🟡 **{qtd_mudo}** mudo/surdo · "
        f"🔴 **{qtd_fora}** fora de call"
    )


def _montar_legenda() -> str:
    return (
        "-# 🟢 Em call (canal de voz) — tempo contando\n"
        "-# 🟡 Mudo ou surdo na call — tempo pausado\n"
        "-# 🔴 Fora de call — plantão ligado, sem voz"
    )


def _montar_rodape() -> str:
    """
    Rodapé com timestamp relativo do Discord (<t:unix:R>).

    O cliente do Discord atualiza sozinho (há 5 segundos, há 23 segundos,
    há 1 minuto…). Mesmo padrão do ranking de plantão em tempo real.
    """
    agora_ts = int(datetime.now(FUSO).timestamp())
    return f"-# CENTRO MÉDICO SUL · atualizado em tempo real · <t:{agora_ts}:R>"


async def montar_painel_plantao_ativo(
    estados: list[EstadoPlantao],
    *,
    guild: discord.Guild | None = None,
) -> discord.ui.LayoutView:
    """
    Monta o card com a lista de médicos em serviço.

    Ordena: em call → mudo/surdo → fora de call.
    Thumbnail: ícone da guilda no cabeçalho do primeiro card.
    Se a lista for longa, parte em vários containers no mesmo LayoutView
    (até 25 entradas por bloco de texto).
    """
    icon_url = guild.icon.url if guild and guild.icon else None
    total = len(estados)

    # Prepara linhas e contagens
    preparados: list[tuple[str, str, int]] = []
    # (status, linha, segundos) — segundos só para ordenar tempo desc

    for estado in estados:
        status = _classificar_status(estado)
        segundos = await calcular_segundos_plantao_atual(
            estado.discord_id,
            estado,
        )
        linha = _linha_membro(estado, segundos=segundos, status=status)
        preparados.append((status, linha, segundos))

    qtd_em_call = sum(1 for s, _, _ in preparados if s == STATUS_EM_CALL)
    qtd_mudo = sum(1 for s, _, _ in preparados if s == STATUS_MUDO_SURDO)
    qtd_fora = sum(1 for s, _, _ in preparados if s == STATUS_FORA_CALL)

    # Ordena por status (verde → amarelo → vermelho), depois tempo maior primeiro
    prioridade = {STATUS_EM_CALL: 0, STATUS_MUDO_SURDO: 1, STATUS_FORA_CALL: 2}
    preparados.sort(key=lambda item: (prioridade[item[0]], -item[2]))

    resumo = _montar_resumo(total, qtd_em_call, qtd_mudo, qtd_fora)
    legenda = _montar_legenda()
    rodape = _montar_rodape()

    def _cabecalho() -> discord.ui.Item:
        if icon_url:
            return discord.ui.Section(
                resumo,
                accessory=discord.ui.Thumbnail(icon_url),
            )
        return discord.ui.TextDisplay(resumo)

    if total == 0:
        cor = discord.Color.dark_grey()
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                _cabecalho(),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    "_Nenhum plantão ligado agora._\n"
                    "Bata o ponto e entre numa call de plantão para aparecer aqui."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(legenda),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(rodape),
                accent_color=cor,
            )
        )
        return view

    # Cor do card: verde se tem alguém contando; amarelo se só pausados; senão vermelho
    if qtd_em_call > 0:
        cor = discord.Color.green()
    elif qtd_mudo > 0:
        cor = discord.Color.gold()
    else:
        cor = discord.Color.red()

    linhas = [linha for _, linha, _ in preparados]

    # Parte em blocos de até 25 entradas / limite de caracteres
    blocos: list[str] = []
    atual: list[str] = []
    tamanho_atual = 0
    for linha in linhas:
        extra = len(linha) + 2
        estourou_qtd = len(atual) >= ENTRADAS_POR_CARD
        estourou_texto = tamanho_atual + extra > LIMITE_CARACTERES_CORPO
        if atual and (estourou_qtd or estourou_texto):
            blocos.append("\n\n".join(atual))
            atual = []
            tamanho_atual = 0
        atual.append(linha)
        tamanho_atual += extra
    if atual:
        blocos.append("\n\n".join(atual))

    view = discord.ui.LayoutView(timeout=None)
    total_blocos = len(blocos)

    for indice, corpo in enumerate(blocos):
        itens: list = []
        eh_primeiro = indice == 0
        eh_ultimo = indice == total_blocos - 1

        if eh_primeiro:
            itens.append(_cabecalho())
            itens.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        else:
            itens.append(
                discord.ui.TextDisplay(
                    f"-# Continuação · plantão ativo ({indice + 1}/{total_blocos})"
                )
            )
            itens.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        itens.append(discord.ui.TextDisplay(corpo))

        if eh_ultimo:
            itens.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
            itens.append(discord.ui.TextDisplay(legenda))
            itens.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
            itens.append(discord.ui.TextDisplay(rodape))

        view.add_item(discord.ui.Container(*itens, accent_color=cor))

    return view
