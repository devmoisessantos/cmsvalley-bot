"""Log de punição em Components V2 + tópico de provas + DM ao advertido."""

from __future__ import annotations

import asyncio
from datetime import datetime

import discord

from src.config import CANAIS


async def registrar_log_punicao(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    id_fivem: str,
    cargo_role: discord.Role,
    motivo: str,
    links: list[str],
    punicao_id: int,
) -> tuple[discord.Message | None, discord.Thread | None]:
    canal_id = CANAIS.get("LOG_PUNICOES") or CANAIS.get("CANAL_ADVERTENCIAS") or 0
    canal = guild.get_channel(canal_id) if canal_id else None
    if canal is None:
        print("⚠️ [punicoes] Canal de log de punições não encontrado.")
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

    # ── Tópico com provas (ligado ao registro) ──────────────────────────────
    thread = await _criar_topico_provas(msg, canal, links)

    # Notifica o advertido em DM com botão link para o registro
    await notificar_dm_advertencia(
        alvo=alvo,
        executor=executor,
        id_fivem=id_fivem,
        cargo_nome=cargo_role.name,
        motivo=motivo,
        msg_log=msg,
    )

    return msg, thread


async def _criar_topico_provas(
    msg: discord.Message,
    canal: discord.abc.Messageable,
    links: list[str],
) -> discord.Thread | None:
    """Cria o tópico 'Provas anexadas' no registro, posta os links e fecha.

    Fechar = archived+locked: some da lista de tópicos ativos do canal,
    mas continua acessível clicando no registro da advertência.
    Não deleta e não exclui o tópico.
    """
    thread: discord.Thread | None = None

    # 1) Tenta criar a partir da mensagem do registro
    try:
        thread = await msg.create_thread(
            name="📁 Provas anexadas",
            auto_archive_duration=60,  # 1h (mínimo válido)
            reason="Provas da advertência",
        )
    except discord.HTTPException as e:
        print(f"⚠️ [punicoes] create_thread via mensagem falhou: {e}")
        # 2) Fallback: criar pelo canal apontando a mensagem
        try:
            if isinstance(canal, (discord.TextChannel, discord.ForumChannel)):
                thread = await canal.create_thread(
                    name="📁 Provas anexadas",
                    message=msg,
                    auto_archive_duration=60,
                    reason="Provas da advertência",
                )
        except discord.HTTPException as e2:
            print(f"⚠️ [punicoes] create_thread via canal falhou: {e2}")
            thread = None

    if thread is None:
        print("⚠️ [punicoes] Não foi possível criar o tópico de provas.")
        return None

    # Posta as provas (links soltos → Discord gera preview)
    try:
        if links:
            # Divide em blocos se muitos links (limite de 2000 chars)
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
                    "Links abaixo são enviados fora de container para permitir preview automático do Discord."
                )
                await thread.send("### 🔗 Links**\n\n")
                await thread.send("\n".join(bloco))
        else:
            await thread.send(
                "\n📁 **Provas anexadas**\n_Nenhum link de prova foi informado._"
            )
    except discord.HTTPException as e:
        print(f"⚠️ [punicoes] Falha ao postar provas no tópico: {e}")

    # Fecha o tópico (some da lista de canais / tópicos ativos).
    # Continua acessível pelo registro da advertência.
    # locked impede que membros comuns reabram; archived remove da lista ativa.
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


async def notificar_dm_advertencia(
    *,
    alvo: discord.Member,
    executor: discord.Member,
    id_fivem: str,
    cargo_nome: str,
    motivo: str,
    msg_log: discord.Message | None,
) -> None:
    """Envia DM ao usuário advertido com resumo + botão link para o registro."""
    data_str = datetime.now().strftime("%d/%m/%Y, %H:%M")

    items: list = [
        discord.ui.TextDisplay("# Você recebeu uma advertência!"),
        discord.ui.TextDisplay(f"> **ID FiveM:** `{id_fivem}`"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(f"### > **🧾 Punição:**\n`{cargo_nome.strip()}`"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay("### > **⏳ Duração:**\n`Até o Pagamento via Ticket`"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(f"### > **📝 Motivo:**\n`{motivo[:500]}`"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(
            f"### > **👮 Aplicado por:**\n{executor.mention} (`{executor.id}`)"
        ),
        discord.ui.TextDisplay(f"### > **📅 Data da Punição:**\n`{data_str}`"),
    ]

    if msg_log is not None:
        row = discord.ui.ActionRow()
        row.add_item(
            discord.ui.Button(
                label="Acessar a Advertência",
                style=discord.ButtonStyle.link,
                url=msg_log.jump_url,
            )
        )
        items.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        items.append(row)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*items, accent_color=discord.Color.dark_red()))

    try:
        await alvo.send(view=view)
    except (discord.Forbidden, discord.HTTPException):
        pass
