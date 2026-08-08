"""
Central de notificações por DM ao jogador.

Toda mensagem privada ao membro deve passar por aqui.
Usa Components V2 (LayoutView + Container + TextDisplay).

Padrão:
  - título claro
  - linhas descritivas
  - botões de link opcionais (ticket, registro, etc.)
  - nunca propaga Forbidden/HTTPException (DM bloqueada = silêncio)
"""

from __future__ import annotations

import logging
from datetime import datetime

import discord

from src.config import (
    CANAIS,
    GUILD_ID,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cores
# ---------------------------------------------------------------------------

COR_SUCESSO = discord.Color.green()
COR_ERRO = discord.Color.red()
COR_AVISO = discord.Color.orange()
COR_INFO = discord.Color.blurple()
COR_PUNICAO = discord.Color.dark_red()


# ---------------------------------------------------------------------------
# Núcleo
# ---------------------------------------------------------------------------


def _montar_url_canal(canal_id: int, guild_id: int | None = None) -> str:
    """Link discord:// para um canal do servidor."""
    id_da_guilda = guild_id or int(GUILD_ID)
    return f"https://discord.com/channels/{id_da_guilda}/{canal_id}"


def _url_canal_tickets() -> str | None:
    canal_id = CANAIS.get("GUIA_DUVIDAS_TICKET") or 0
    if canal_id <= 0:
        return None
    return _montar_url_canal(canal_id)


def _linha_de_botoes(
    botoes: list[tuple[str, str]],
) -> discord.ui.ActionRow | None:
    """
    Monta ActionRow com botões de link.
    Cada item é (rótulo, url).
    """
    if not botoes:
        return None
    linha = discord.ui.ActionRow()
    for rotulo, url in botoes[:5]:
        if not url:
            continue
        linha.add_item(
            discord.ui.Button(
                label=rotulo,
                style=discord.ButtonStyle.link,
                url=url,
            )
        )
    return linha if len(linha.children) > 0 else None


async def _registrar_log_notificacao_dm(
    *,
    destino: discord.abc.User | discord.Member | None,
    titulo: str,
    linhas_resumo: list[str],
    enviou: bool,
    motivo_falha: str | None = None,
    guilda: discord.Guild | None = None,
) -> None:
    """
    Posta no canal LOG_NOTIFICACOES_DM o resultado do envio da DM.
    Não mistura com LOG_PLANTAO.
    """
    canal_id = CANAIS.get("LOG_NOTIFICACOES_DM") or 0
    if canal_id <= 0:
        return

    guilda_resolvida = guilda
    if guilda_resolvida is None and isinstance(destino, discord.Member):
        guilda_resolvida = destino.guild

    if guilda_resolvida is None:
        # tenta achar a guilda pelo ID configurado (cache do bot não disponível aqui)
        return

    canal = guilda_resolvida.get_channel(canal_id)
    if canal is None:
        logger.warning(
            "Canal LOG_NOTIFICACOES_DM (%s) não encontrado na guilda",
            canal_id,
        )
        return

    id_destino = getattr(destino, "id", None) if destino is not None else None
    mencao = (
        destino.mention
        if destino is not None and hasattr(destino, "mention")
        else f"`{id_destino}`"
    )
    status = "✅ Enviada" if enviou else f"❌ Falhou ({motivo_falha or 'desconhecido'})"
    cor = COR_SUCESSO if enviou else COR_ERRO

    corpo = (
        f"- **Destino:** {mencao} (`{id_destino}`)\n"
        f"- **Status:** {status}\n"
        f"- **Título da DM:** {titulo}\n"
    )
    if linhas_resumo:
        preview = " | ".join(linhas_resumo[:4])
        if len(preview) > 280:
            preview = preview[:277] + "..."
        corpo += f"- **Resumo:** {preview}"

    try:
        from src.utils.log_container import LogContainerView

        avatar = None
        if destino is not None and hasattr(destino, "display_avatar"):
            avatar = destino.display_avatar.url

        view_do_log = LogContainerView(
            titulo="📨 Log de Notificação DM",
            linhas=corpo,
            guild=guilda_resolvida,
            cor=cor,
            avatar_url=avatar,
        )
        await canal.send(view=view_do_log)
    except Exception as erro:
        logger.warning("Falha ao postar LOG_NOTIFICACOES_DM: %s", erro)


async def enviar_dm_card(
    destino: discord.abc.User | discord.Member | None,
    *,
    titulo: str,
    linhas: list[str],
    cor: discord.Color = COR_INFO,
    botoes_link: list[tuple[str, str]] | None = None,
    guilda: discord.Guild | None = None,
    registrar_log: bool = True,
) -> bool:
    """
    Envia um card Components V2 na DM do usuário.

    Retorna True se enviou, False se falhou (DM fechada, membro None, etc.).
    Por padrão registra o resultado em LOG_NOTIFICACOES_DM.
    """
    if destino is None:
        logger.warning("enviar_dm_card chamado com destino=None")
        if registrar_log:
            await _registrar_log_notificacao_dm(
                destino=None,
                titulo=titulo,
                linhas_resumo=linhas,
                enviou=False,
                motivo_falha="destino None",
                guilda=guilda,
            )
        return False

    componentes: list = [
        discord.ui.TextDisplay(f"# {titulo}"),
    ]
    if linhas:
        texto = "\n".join(linhas)
        componentes.append(discord.ui.TextDisplay(texto))

    linha_botoes = _linha_de_botoes(botoes_link or [])
    if linha_botoes is not None:
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(linha_botoes)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*componentes, accent_color=cor))

    enviou = False
    motivo_falha: str | None = None
    try:
        await destino.send(view=view)
        logger.info("DM enviada para %s (%s)", destino, getattr(destino, "id", "?"))
        enviou = True
    except discord.Forbidden:
        motivo_falha = "DM bloqueada"
        logger.warning(
            "DM bloqueada para %s (%s)",
            destino,
            getattr(destino, "id", "?"),
        )
    except discord.HTTPException as erro:
        motivo_falha = f"HTTP {erro}"
        logger.warning(
            "Falha HTTP ao enviar DM para %s: %s",
            getattr(destino, "id", "?"),
            erro,
        )

    if registrar_log:
        await _registrar_log_notificacao_dm(
            destino=destino,
            titulo=titulo,
            linhas_resumo=linhas,
            enviou=enviou,
            motivo_falha=motivo_falha,
            guilda=guilda,
        )
    return enviou


async def enviar_dm_texto(
    destino: discord.abc.User | discord.Member | None,
    texto: str,
    *,
    guilda: discord.Guild | None = None,
    registrar_log: bool = True,
) -> bool:
    """
    Envia texto simples na DM (legado / casos sem card).
    Preferir enviar_dm_card sempre que possível.
    """
    titulo = "Mensagem de texto"
    if destino is None:
        logger.warning("enviar_dm_texto chamado com destino=None")
        if registrar_log:
            await _registrar_log_notificacao_dm(
                destino=None,
                titulo=titulo,
                linhas_resumo=[texto[:120]],
                enviou=False,
                motivo_falha="destino None",
                guilda=guilda,
            )
        return False

    enviou = False
    motivo_falha: str | None = None
    try:
        await destino.send(texto)
        logger.info(
            "DM texto enviada para %s (%s)", destino, getattr(destino, "id", "?")
        )
        enviou = True
    except discord.Forbidden:
        motivo_falha = "DM bloqueada"
        logger.warning(
            "DM bloqueada para %s (%s)",
            destino,
            getattr(destino, "id", "?"),
        )
    except discord.HTTPException as erro:
        motivo_falha = f"HTTP {erro}"
        logger.warning(
            "Falha HTTP ao enviar DM texto para %s: %s",
            getattr(destino, "id", "?"),
            erro,
        )

    if registrar_log:
        await _registrar_log_notificacao_dm(
            destino=destino,
            titulo=titulo,
            linhas_resumo=[texto[:120]],
            enviou=enviou,
            motivo_falha=motivo_falha,
            guilda=guilda,
        )
    return enviou


# ---------------------------------------------------------------------------
# Punições
# ---------------------------------------------------------------------------


async def notificar_dm_advertencia(
    *,
    alvo: discord.Member,
    executor: discord.Member,
    id_fivem: str,
    cargo_nome: str,
    motivo: str,
    msg_log: discord.Message | None = None,
) -> bool:
    """DM ao advertido com resumo + botão para o registro público."""
    data_str = datetime.now().strftime("%d/%m/%Y, %H:%M")
    linhas = [
        f"> **ID FiveM:** `{id_fivem}`",
        "",
        f"### > **🧾 Punição:**\n`{cargo_nome.strip()}`",
        "",
        "### > **⏳ Duração:**\n`Até o Pagamento via Ticket`",
        "",
        f"### > **📝 Motivo:**\n`{motivo[:500]}`",
        "",
        f"### > **👮 Aplicado por:**\n{executor.mention} (`{executor.id}`)",
        "",
        f"### > **📅 Data da Punição:**\n`{data_str}`",
    ]

    botoes: list[tuple[str, str]] = []
    if msg_log is not None:
        botoes.append(("Acessar a Advertência", msg_log.jump_url))
    url_tickets = _url_canal_tickets()
    if url_tickets:
        botoes.append(("Abrir Ticket / Revogar", url_tickets))

    return await enviar_dm_card(
        alvo,
        titulo="Você recebeu uma advertência!",
        linhas=linhas,
        cor=COR_PUNICAO,
        botoes_link=botoes,
    )


async def notificar_dm_exoneracao(
    *,
    alvo: discord.Member,
    executor: discord.Member,
    id_fivem: str,
    motivo: str,
    automatica: bool = False,
    msg_log: discord.Message | None = None,
) -> bool:
    """DM de exoneração (manual ou automática pela 3ª advertência)."""
    data_str = datetime.now().strftime("%d/%m/%Y, %H:%M")
    origem = "Automática — 3ª advertência formal" if automatica else "Manual"

    linhas = [
        f"> **ID FiveM:** `{id_fivem}`",
        "",
        "### > **⛔ Situação:**\n`Exonerado`",
        "",
        f"### > **📋 Origem:**\n`{origem}`",
        "",
        f"### > **📝 Motivo:**\n`{motivo[:500]}`",
        "",
        f"### > **👮 Responsável:**\n{executor.mention} (`{executor.id}`)",
        "",
        f"### > **📅 Data:**\n`{data_str}`",
        "",
        ("Seus cargos foram removidos. Restam apenas **Exonerado** e **Visitantes**."),
        ("Para **solicitar a revogação** da exoneração, abra um ticket com a equipe."),
    ]

    botoes: list[tuple[str, str]] = []
    url_tickets = _url_canal_tickets()
    if url_tickets:
        botoes.append(("Revogar Exoneração (Ticket)", url_tickets))
    if msg_log is not None:
        botoes.append(("Ver Registro", msg_log.jump_url))

    return await enviar_dm_card(
        alvo,
        titulo="Você foi exonerado",
        linhas=linhas,
        cor=COR_PUNICAO,
        botoes_link=botoes,
    )


async def notificar_dm_remocao_punicao(
    *,
    alvo: discord.Member,
    executor: discord.Member,
    cargos_removidos: list[str],
    motivo_remocao: str | None = None,
) -> bool:
    """DM quando uma punição é removida."""
    lista = ", ".join(f"`{c.strip()}`" for c in cargos_removidos) or "—"
    motivo_txt = (motivo_remocao or "Sem motivo informado")[:500]
    linhas = [
        f"### > **🧹 Punições removidas:**\n{lista}",
        "",
        f"### > **📝 Motivo:**\n`{motivo_txt}`",
        "",
        f"### > **👮 Removido por:**\n{executor.mention} (`{executor.id}`)",
    ]
    return await enviar_dm_card(
        alvo,
        titulo="Punição removida",
        linhas=linhas,
        cor=COR_SUCESSO,
    )


# ---------------------------------------------------------------------------
# Plantão
# ---------------------------------------------------------------------------


async def notificar_dm_plantao_lembrete_ocioso(
    membro: discord.Member | None,
    *,
    minutos: int,
    nivel: int,
    guilda: discord.Guild | None = None,
) -> bool:
    """
    Lembrete de ociosidade (fora de call com plantão ligado).

    nivel 1 = aviso leve, 2 = atenção, 3 = último aviso antes do desligamento.
    """
    if nivel <= 1:
        titulo = "Plantão — lembrete de inatividade"
        cor = COR_INFO
        extra = "Não esqueça de se conectar a uma call válida."
    elif nivel == 2:
        titulo = "Plantão — atenção"
        cor = COR_AVISO
        extra = "Conecte-se logo ou o plantão será encerrado automaticamente."
    else:
        titulo = "Plantão — último aviso"
        cor = COR_ERRO
        extra = (
            "Este é o último aviso. Sem call válida em breve, "
            "o plantão será desligado automaticamente."
        )

    linhas = [
        f"Já se passaram **`{minutos} minutos`** sem você estar em uma call de plantão.",
        "",
        extra,
    ]
    return await enviar_dm_card(
        membro,
        titulo=titulo,
        linhas=linhas,
        cor=cor,
        guilda=guilda,
    )


async def notificar_dm_plantao_desligado_automatico(
    membro: discord.Member | None,
    *,
    minutos: int,
    guilda: discord.Guild | None = None,
) -> bool:
    """Plantão encerrado por ociosidade prolongada."""
    return await enviar_dm_card(
        membro,
        titulo="Plantão encerrado automaticamente",
        linhas=[
            (
                f"Você ficou mais de **`{minutos} minutos`** "
                "sem estar em uma call válida."
            ),
            "",
            "O plantão foi desligado. Ligue novamente pelo painel quando for atuar.",
        ],
        cor=COR_ERRO,
        guilda=guilda,
    )


async def notificar_dm_plantao_afk_aviso(
    membro: discord.Member | None,
    *,
    limite_minutos: int,
    penalidade_moedas: int,
    guilda: discord.Guild | None = None,
) -> bool:
    """Aviso de AFK (mudo + surdo prolongado)."""
    horas = max(1, limite_minutos // 60)
    return await enviar_dm_card(
        membro,
        titulo="Aviso de AFK no plantão",
        linhas=[
            (
                f"Você está **mudo e surdo** há quase **{horas}h** "
                "no mesmo canal de voz."
            ),
            "",
            (
                "Se não estiver mais ativo, será desconectado automaticamente "
                f"e perderá **{penalidade_moedas} moedas** de penalidade."
            ),
        ],
        cor=COR_AVISO,
        guilda=guilda,
    )


async def notificar_dm_plantao_afk_desconectado(
    membro: discord.Member | None,
    *,
    limite_minutos: int,
    penalidade_moedas: int,
    guilda: discord.Guild | None = None,
) -> bool:
    """Desconectado por AFK + penalidade aplicada."""
    return await enviar_dm_card(
        membro,
        titulo="Desconectado por inatividade (AFK)",
        linhas=[
            (
                f"Você ficou **mudo e surdo** por **{limite_minutos} minutos** "
                "no mesmo canal."
            ),
            "",
            f"Penalidade aplicada: **-{penalidade_moedas} moedas**.",
        ],
        cor=COR_ERRO,
        guilda=guilda,
    )


async def notificar_dm_plantao_housekeeping(
    membro: discord.Member | None,
    *,
    horas_limite: int,
    guilda: discord.Guild | None = None,
) -> bool:
    """Plantão fechado pelo housekeeping (sessão abandonada)."""
    return await enviar_dm_card(
        membro,
        titulo="Plantão encerrado pelo sistema",
        linhas=[
            (f"Sua sessão ficou aberta por mais de **{horas_limite}h** sem atividade."),
            "",
            "O plantão foi desligado automaticamente. "
            "Ligue novamente pelo painel se for atuar.",
        ],
        cor=COR_AVISO,
        guilda=guilda,
    )


async def notificar_dm_moeda_creditada(
    membro: discord.Member | None,
    *,
    saldo_total: int,
    valor_em_reais: str | None = None,
) -> bool:
    """Opcional: avisa quando uma moeda é creditada no plantão."""
    linhas = [
        "Você recebeu **+1 moeda** pelo tempo em call de plantão.",
        "",
        f"### > **Saldo atual:**\n`{saldo_total} moedas`",
    ]
    if valor_em_reais:
        linhas.append(f"({valor_em_reais})")
    return await enviar_dm_card(
        membro,
        titulo="Moeda creditada",
        linhas=linhas,
        cor=COR_SUCESSO,
    )
