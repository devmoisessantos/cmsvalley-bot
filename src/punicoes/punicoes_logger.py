"""Log de punição em Components V2 + tópico de provas + DM ao advertido.

Canais:
  - CANAL_ADVERTENCIAS → registro público da advertência + tópico de provas + DM
  - LOG_PUNICOES       → log interno (aplicação e remoção)
  - CANAL_PAGAMENTO_ADV → comprovante de pagamento ao remover advertência
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import (
    datetime,
    timezone,
)

import discord

from src.config import (
    CANAIS,
    CARGOS_PUNICOES,
    MESES_ABREV,
)
from src.utils.formatacao import (
    agora_brasilia,
    para_horario_brasilia,
)
from src.utils.notificacao import notificar_dm_advertencia

registrador = logging.getLogger(__name__)

_DIAS_SEMANA = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


def _formatar_data_pagamento(data_hora: datetime | None = None) -> str:
    """Ex.: sexta-feira, 16 de outubro de 2026 às 17:16"""
    local = (
        para_horario_brasilia(data_hora) if data_hora is not None else agora_brasilia()
    )
    assert local is not None
    dia_semana = _DIAS_SEMANA[local.weekday()]
    nome_mes = MESES_ABREV[local.month]
    # MESES_ABREV costuma ser abreviado (Out); para o card usamos o nome completo
    meses_completos = {
        1: "janeiro",
        2: "fevereiro",
        3: "março",
        4: "abril",
        5: "maio",
        6: "junho",
        7: "julho",
        8: "agosto",
        9: "setembro",
        10: "outubro",
        11: "novembro",
        12: "dezembro",
    }
    nome_mes = meses_completos.get(local.month, nome_mes)
    return (
        f"{dia_semana}, {local.day} de {nome_mes} de {local.year} "
        f"às {local.strftime('%H:%M')}"
    )


def _cargo_destaque_do_membro(membro: discord.Member) -> discord.Role | None:
    """Cargo mais alto do membro, ignorando @everyone e cargos de punição."""
    ids_punicao = set(CARGOS_PUNICOES.values())
    candidatos = [
        cargo
        for cargo in membro.roles
        if cargo.is_default() is False and cargo.id not in ids_punicao
    ]
    if not candidatos:
        return None
    return max(candidatos, key=lambda cargo: cargo.position)


def _canal(guild: discord.Guild, chave: str) -> discord.abc.GuildChannel | None:
    canal_id = CANAIS.get(chave) or 0
    return guild.get_channel(canal_id) if canal_id else None


# ═══════════════════════════════════════════════════════════════════════════
# CANAL PÚBLICO — CANAL_ADVERTENCIAS
# ═══════════════════════════════════════════════════════════════════════════


async def registrar_advertencia(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    id_fivem: str,
    cargo_role: discord.Role,
    motivo: str,
    links: list[str],
    punicao_id: int,
    texto_provas: str | None = None,
    arquivos_provas: list[tuple[bytes, str]] | None = None,
) -> tuple[discord.Message | None, discord.Thread | None]:
    """
    Posta a advertência em CANAL_ADVERTENCIAS, cria tópico de provas e notifica em DM.
    """
    canal = _canal(guild, "CANAL_ADVERTENCIAS")
    if canal is None:
        registrador.warning(
            "⚠️ [punicoes] Canal de advertências (CANAL_ADVERTENCIAS) não encontrado."
        )
        return None, None

    linhas = (
        f"- **Membro advertido:** {alvo.mention} (`{alvo.id}`)\n"
        f"- **Advertido por:** {executor.mention} (`{executor.id}`)\n"
        f"- **ID FiveM:** `{id_fivem}`\n"
        f"- **Punição:** {cargo_role.mention}\n"
        f"- **Duração:** Até realizar o Pagamento ou ser Removida\n"
        f"- **Motivo da advertência:**\n{motivo}\n"
        f"- **Registro:** `#{punicao_id}`"
    )

    container = discord.ui.Container(
        discord.ui.TextDisplay("# 🔴 Nova Punição Aplicada!"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.Section(
            linhas,
            accessory=discord.ui.Thumbnail(alvo.display_avatar.url),
        ),
        accent_color=discord.Color.red(),
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)

    mensagem = await canal.send(view=view)
    thread = await _criar_topico_provas(
        mensagem,
        canal,
        links,
        texto_livre=texto_provas,
        arquivos=arquivos_provas,
    )

    await notificar_dm_advertencia(
        alvo=alvo,
        executor=executor,
        id_fivem=id_fivem,
        cargo_nome=cargo_role.name,
        motivo=motivo,
        msg_log=mensagem,
    )

    return mensagem, thread


async def registrar_exoneracao(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    id_fivem: str,
    motivo: str,
    links: list[str],
    punicao_id: int | None = None,
    texto_provas: str | None = None,
    automatica: bool = False,
    arquivos_provas: list[tuple[bytes, str]] | None = None,
) -> tuple[discord.Message | None, discord.Thread | None]:
    """Posta a exoneração em CANAL_EXONERACOES (mesmo modelo das advertências)."""
    canal = _canal(guild, "CANAL_EXONERACOES")
    if canal is None:
        registrador.warning(
            "⚠️ [punicoes] Canal de exonerações (CANAL_EXONERACOES) não encontrado."
        )
        return None, None

    origem = "Automática (3ª advertência)" if automatica else "Manual"
    registro_txt = f"`#{punicao_id}`" if punicao_id else "—"

    linhas = (
        f"- **Membro exonerado:** {alvo.mention} (`{alvo.id}`)\n"
        f"- **Exonerado por:** {executor.mention} (`{executor.id}`)\n"
        f"- **ID FiveM:** `{id_fivem}`\n"
        f"- **Origem:** {origem}\n"
        f"- **Cargos finais:** Exonerado + Visitantes\n"
        f"- **Motivo da exoneração:**\n{motivo}\n"
        f"- **Registro:** {registro_txt}"
    )

    container = discord.ui.Container(
        discord.ui.TextDisplay("# ⛔ Membro Exonerado"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.Section(
            linhas,
            accessory=discord.ui.Thumbnail(alvo.display_avatar.url),
        ),
        accent_color=discord.Color.dark_red(),
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)

    try:
        mensagem = await canal.send(view=view)
    except discord.HTTPException as erro:
        registrador.warning(f"⚠️ [punicoes] Falha ao postar exoneração: {erro}")
        return None, None

    thread = await _criar_topico_provas(
        mensagem,
        canal,
        links,
        texto_livre=texto_provas,
        arquivos=arquivos_provas,
    )
    return mensagem, thread


# ═══════════════════════════════════════════════════════════════════════════
# LOG INTERNO — LOG_PUNICOES
# ═══════════════════════════════════════════════════════════════════════════


async def registrar_log_advertencia(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    id_fivem: str,
    cargo_role: discord.Role,
    motivo: str,
    punicao_id: int,
    msg_advertencia: discord.Message | None = None,
) -> discord.Message | None:
    """Registra aplicação de advertência no LOG_PUNICOES (sem tópico de provas)."""
    canal = _canal(guild, "LOG_PUNICOES")
    if canal is None:
        registrador.warning("⚠️ [punicoes] Canal de log (LOG_PUNICOES) não encontrado.")
        return None

    link_reg = (
        f"\n- **Registro público:** [*Clique Aqui]({msg_advertencia.jump_url}) para "
        f"abrir o registro."
        if msg_advertencia
        else ""
    )

    linhas = (
        f"- **Membro:** {alvo.mention} (`{alvo.id}`)\n"
        f"- **Responsável:** {executor.mention} (`{executor.id}`)\n"
        f"- **ID FiveM:** `{id_fivem}`\n"
        f"- **Punição:** {cargo_role.mention}\n"
        f"- **Motivo:** {motivo[:500]}\n"
        f"- **ID do registro:** `#{punicao_id}`"
        f"{link_reg}"
    )

    container = discord.ui.Container(
        discord.ui.TextDisplay("# 🔴 Novo Registro de Punição"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.Section(
            linhas,
            accessory=discord.ui.Thumbnail(alvo.display_avatar.url),
        ),
        accent_color=discord.Color.red(),
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)

    try:
        return await canal.send(view=view)
    except discord.HTTPException as erro:
        registrador.warning(f"⚠️ [punicoes] Falha ao postar log de advertência: {erro}")
        return None


async def registrar_log_remocao(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    cargos_removidos: list[str],
    motivo_remocao: str | None,
    punicao_ids: list[int] | None = None,
    id_fivem: str | None = None,
) -> discord.Message | None:
    """Registra remoção de punição no LOG_PUNICOES."""
    canal = _canal(guild, "LOG_PUNICOES")
    if canal is None:
        registrador.warning("⚠️ [punicoes] Canal de log (LOG_PUNICOES) não encontrado.")
        return None

    lista_cargos = (
        ", ".join(f"**{nome_do_cargo.strip()}**" for nome_do_cargo in cargos_removidos)
        or "—"
    )
    ids_txt = (
        ", ".join(f"`#{indice}`" for indice in punicao_ids) if punicao_ids else "—"
    )
    motivo_txt = (motivo_remocao or "Sem motivo informado")[:500]
    fivem_txt = f"`{id_fivem}`" if id_fivem else "—"

    linhas = (
        f"- **Membro:** {alvo.mention} (`{alvo.id}`)\n"
        f"- **Removido por:** {executor.mention} (`{executor.id}`)\n"
        f"- **ID FiveM:** {fivem_txt}\n"
        f"- **Punições removidas:** {lista_cargos}\n"
        f"- **IDs dos registros:** {ids_txt}\n"
        f"- **Motivo da remoção:** {motivo_txt}"
    )

    container = discord.ui.Container(
        discord.ui.TextDisplay("# 🟢 Punição Removida"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.Section(
            linhas,
            accessory=discord.ui.Thumbnail(alvo.display_avatar.url),
        ),
        accent_color=discord.Color.green(),
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)

    try:
        return await canal.send(view=view)
    except discord.HTTPException as erro:
        registrador.warning(f"⚠️ [punicoes] Falha ao postar log de remoção: {erro}")
        return None


async def registrar_pagamento_advertencia(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    cargo_id_advertencia: int,
    cargo_nome_advertencia: str,
    punicao_id: int | None,
    id_fivem: str | None,
    motivo_remocao: str | None,
    bytes_comprovante: bytes,
    nome_arquivo: str,
) -> discord.Message | None:
    """
    Publica o comprovante de pagamento da advertência em CANAL_PAGAMENTO_ADV.

    Espera os bytes já baixados (mesmo padrão do laudo: ler arquivo antes de
    qualquer publicação ou apagar mensagem).
    """
    canal = _canal(guild, "CANAL_PAGAMENTO_ADV")
    if canal is None:
        registrador.warning(
            "⚠️ [punicoes] Canal CANAL_PAGAMENTO_ADV não encontrado."
        )
        return None

    if not bytes_comprovante:
        registrador.warning(
            "⚠️ [punicoes] Comprovante vazio para punição #%s.",
            punicao_id,
        )
        return None

    agora = datetime.now(timezone.utc)
    momento_unix = int(agora.timestamp())
    data_pagamento = _formatar_data_pagamento(agora)

    fivem_txt = id_fivem or "—"
    registro_txt = f"`#{punicao_id}`" if punicao_id is not None else "—"
    motivo_txt = (motivo_remocao or "Pagamento de advertência").strip()[:400]

    cargo_membro = _cargo_destaque_do_membro(alvo)
    cargo_membro_txt = (
        cargo_membro.mention if cargo_membro is not None else "—"
    )
    adv_role = guild.get_role(cargo_id_advertencia)
    adv_txt = (
        adv_role.mention
        if adv_role is not None
        else f"**{cargo_nome_advertencia.strip()}**"
    )

    nome_arquivo_limpo = nome_arquivo or "comprovante_pagamento.png"
    e_imagem = nome_arquivo_limpo.lower().endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".gif")
    )

    buffer = io.BytesIO(bytes_comprovante)
    buffer.seek(0)
    arquivo = discord.File(fp=buffer, filename=nome_arquivo_limpo)

    nl = "\n"
    titulo = "# 💳 Pagamento de Advertência"
    corpo = (
        f"> • `👤` **Nome:** {alvo.mention} • **ID:** `{fivem_txt}`"
        + nl
        + f"> • `🎖️` **Cargo:** {cargo_membro_txt}"
        + nl
        + f"> • `⚠️` **Advertência:** {adv_txt}"
        + nl
        + f"> • `📅` **Data do Pagamento:** `{data_pagamento}`"
        + nl
        + nl
        + f"> • `🛡️` **Responsável pela remoção:** {executor.mention}"
        + nl
        + f"> • `🔢` **Nº do registro:** {registro_txt}"
        + nl
        + f"> • `📝` **Motivo:** {motivo_txt}"
    )

    componentes: list = [
        discord.ui.Section(
            titulo,
            accessory=discord.ui.Thumbnail(alvo.display_avatar.url),
        ),
        discord.ui.TextDisplay(corpo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
    ]

    if e_imagem:
        componentes.append(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(f"attachment://{nome_arquivo_limpo}")
            )
        )
    else:
        try:
            componentes.append(
                discord.ui.File(f"attachment://{nome_arquivo_limpo}")
            )
        except (TypeError, AttributeError):
            pass

    componentes.append(
        discord.ui.TextDisplay(
            f"-# CENTRO MÉDICO SUL VALLEY • Pagamento de ADV • <t:{momento_unix}:f>"
        )
    )

    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(
            *componentes,
            accent_color=discord.Color.green(),
        )
    )

    try:
        return await canal.send(view=layout, file=arquivo)
    except discord.HTTPException as erro:
        registrador.warning(
            "⚠️ [punicoes] Falha ao postar pagamento de ADV (#%s): %s",
            punicao_id,
            erro,
        )
        return None


# ═══════════════════════════════════════════════════════════════════════════
# TÓPICO DE PROVAS + DM
# ═══════════════════════════════════════════════════════════════════════════


async def _criar_topico_provas(
    mensagem: discord.Message,
    canal: discord.abc.Messageable,
    links: list[str],
    texto_livre: str | None = None,
    arquivos: list[tuple[bytes, str]] | None = None,
) -> discord.Thread | None:
    """Cria o tópico 'Provas anexadas', posta links, arquivos e/ou texto, depois fecha.

    - Arquivos → reenviados pelo bot (discord.File) — cópia permanente.
    - URLs → links (preview do Discord).
    - Texto livre → observações.
    - Se não houver nada → avisa que não houve prova anexada.
    """
    thread: discord.Thread | None = None

    try:
        thread = await mensagem.create_thread(
            name="📁 Provas anexadas",
            auto_archive_duration=60,
            reason="Provas da punição",
        )
    except discord.HTTPException as erro:
        registrador.warning(f"⚠️ [punicoes] create_thread via mensagem falhou: {erro}")
        try:
            if isinstance(canal, (discord.TextChannel, discord.ForumChannel)):
                thread = await canal.create_thread(
                    name="📁 Provas anexadas",
                    message=mensagem,
                    auto_archive_duration=60,
                    reason="Provas da punição",
                )
        except discord.HTTPException as e2:
            registrador.warning(f"⚠️ [punicoes] create_thread via canal falhou: {e2}")
            thread = None

    if thread is None:
        registrador.warning("⚠️ [punicoes] Não foi possível criar o tópico de provas.")
        return None

    texto_limpo = (texto_livre or "").strip()
    lista_arquivos = arquivos or []

    try:
        # 1) Arquivos do FileUpload — reenvio permanente pelo bot
        if lista_arquivos:
            await thread.send("## 📁 Provas em arquivo")
            await thread.send(
                "-# Arquivos reenviados pelo bot (cópia permanente neste tópico)."
            )
            for dados, nome in lista_arquivos:
                try:
                    buffer = io.BytesIO(dados)
                    buffer.seek(0)
                    nome_seguro = "".join(
                        caractere if caractere.isalnum() or caractere in "._-" else "_"
                        for caractere in (nome or "prova.bin")
                    )[:80]
                    if "." not in nome_seguro:
                        nome_seguro = f"{nome_seguro}.bin"
                    await thread.send(
                        file=discord.File(fp=buffer, filename=nome_seguro)
                    )
                except Exception as erro_arq:
                    registrador.warning(
                        f"⚠️ [punicoes] falha ao postar arquivo de prova: {erro_arq}"
                    )

        if links:
            bloco: list[str] = []
            tamanho = 0
            for link in links:
                linha = link.strip()
                if not linha:
                    continue
                if tamanho + len(linha) + 1 > 1900 and bloco:
                    await thread.send("\n".join(bloco))
                    bloco = []
                    tamanho = 0
                bloco.append(linha)
                tamanho += len(linha) + 1
            if bloco:
                await thread.send("\n## 📁 Provas anexadas")
                await thread.send(
                    "-# Links abaixo são enviados fora de container para permitir "
                    "preview automático do Discord.\n\n"
                )
                await thread.send("### 🔗 Links\n\n")
                await thread.send("\n".join(bloco))
            # Se além dos links o staff escreveu texto extra, posta também
            if texto_limpo:
                urls_no_texto = set(links)
                texto_sem_urls = texto_limpo
                for url in urls_no_texto:
                    texto_sem_urls = texto_sem_urls.replace(url, "")
                texto_sem_urls = texto_sem_urls.strip()
                if texto_sem_urls:
                    await thread.send("\n### 📝 Observações\n\n")
                    await thread.send(texto_sem_urls[:1900])
        elif texto_limpo:
            await thread.send("\n## 📁 Provas anexadas")
            await thread.send(
                "-# Não foi registrado links, abaixo são textos como prova anexados "
                "pelo Responsável da Exoneração.\n\n"
            )
            await thread.send("\n### 📝 Observações\n\n")
            await thread.send(texto_limpo[:1900])
        elif not lista_arquivos:
            await thread.send(
                "\n## 📁 **Provas anexadas**\n_Nenhum link, arquivo ou texto de prova "
                "foi informado._"
            )
    except discord.HTTPException as erro:
        registrador.warning(f"⚠️ [punicoes] Falha ao postar provas no tópico: {erro}")

    await asyncio.sleep(2)
    try:
        await thread.edit(archived=True, locked=True, reason="Fechar tópico de provas")
    except discord.HTTPException as erro:
        registrador.warning(f"⚠️ [punicoes] Falha ao fechar tópico: {erro}")
        try:
            await thread.edit(archived=True, reason="Fechar tópico de provas")
        except discord.HTTPException as e2:
            registrador.warning(f"⚠️ [punicoes] Fallback archived também falhou: {e2}")

    return thread
