"""Log de punição em Components V2 + tópico de provas + DM ao advertido.

Canais:
  - CANAL_ADVERTENCIAS → registro público da advertência + tópico de provas + DM
  - LOG_PUNICOES       → log interno (aplicação e remoção)
"""

from __future__ import annotations

import asyncio
import io

import discord

from src.config import CANAIS
from src.utils.notificacao import notificar_dm_advertencia


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
    """Posta a advertência em CANAL_ADVERTENCIAS, cria tópico de provas e notifica em DM."""
    canal = _canal(guild, "CANAL_ADVERTENCIAS")
    if canal is None:
        print("⚠️ [punicoes] Canal de advertências (CANAL_ADVERTENCIAS) não encontrado.")
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

    msg = await canal.send(view=view)
    thread = await _criar_topico_provas(
        msg,
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
        msg_log=msg,
    )

    return msg, thread


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
        print("⚠️ [punicoes] Canal de exonerações (CANAL_EXONERACOES) não encontrado.")
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
        msg = await canal.send(view=view)
    except discord.HTTPException as erro:
        print(f"⚠️ [punicoes] Falha ao postar exoneração: {erro}")
        return None, None

    thread = await _criar_topico_provas(
        msg,
        canal,
        links,
        texto_livre=texto_provas,
        arquivos=arquivos_provas,
    )
    return msg, thread


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
        print("⚠️ [punicoes] Canal de log (LOG_PUNICOES) não encontrado.")
        return None

    link_reg = (
        f"\n- **Registro público:** [*Clique Aqui]({msg_advertencia.jump_url}) para abrir o registro."
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
    except discord.HTTPException as e:
        print(f"⚠️ [punicoes] Falha ao postar log de advertência: {e}")
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
        print("⚠️ [punicoes] Canal de log (LOG_PUNICOES) não encontrado.")
        return None

    lista_cargos = ", ".join(f"**{c.strip()}**" for c in cargos_removidos) or "—"
    ids_txt = ", ".join(f"`#{i}`" for i in punicao_ids) if punicao_ids else "—"
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
    except discord.HTTPException as e:
        print(f"⚠️ [punicoes] Falha ao postar log de remoção: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# TÓPICO DE PROVAS + DM
# ═══════════════════════════════════════════════════════════════════════════


async def _criar_topico_provas(
    msg: discord.Message,
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
        thread = await msg.create_thread(
            name="📁 Provas anexadas",
            auto_archive_duration=60,
            reason="Provas da punição",
        )
    except discord.HTTPException as e:
        print(f"⚠️ [punicoes] create_thread via mensagem falhou: {e}")
        try:
            if isinstance(canal, (discord.TextChannel, discord.ForumChannel)):
                thread = await canal.create_thread(
                    name="📁 Provas anexadas",
                    message=msg,
                    auto_archive_duration=60,
                    reason="Provas da punição",
                )
        except discord.HTTPException as e2:
            print(f"⚠️ [punicoes] create_thread via canal falhou: {e2}")
            thread = None

    if thread is None:
        print("⚠️ [punicoes] Não foi possível criar o tópico de provas.")
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
                        c if c.isalnum() or c in "._-" else "_"
                        for c in (nome or "prova.bin")
                    )[:80]
                    if "." not in nome_seguro:
                        nome_seguro = f"{nome_seguro}.bin"
                    await thread.send(
                        file=discord.File(fp=buffer, filename=nome_seguro)
                    )
                except Exception as erro_arq:
                    print(f"⚠️ [punicoes] falha ao postar arquivo de prova: {erro_arq}")

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
                    "-# Links abaixo são enviados fora de container para permitir preview automático do Discord.\n\n"
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
                "-# Não foi registrado links, abaixo são textos como prova anexados pelo Responsável da Exoneração.\n\n"
            )
            await thread.send("\n### 📝 Observações\n\n")
            await thread.send(texto_limpo[:1900])
        elif not lista_arquivos:
            await thread.send(
                "\n## 📁 **Provas anexadas**\n_Nenhum link, arquivo ou texto de prova foi informado._"
            )
    except discord.HTTPException as e:
        print(f"⚠️ [punicoes] Falha ao postar provas no tópico: {e}")

    await asyncio.sleep(2)
    try:
        await thread.edit(archived=True, locked=True, reason="Fechar tópico de provas")
    except discord.HTTPException as e:
        print(f"⚠️ [punicoes] Falha ao fechar tópico: {e}")
        try:
            await thread.edit(archived=True, reason="Fechar tópico de provas")
        except discord.HTTPException as e2:
            print(f"⚠️ [punicoes] Fallback archived também falhou: {e2}")

    return thread
