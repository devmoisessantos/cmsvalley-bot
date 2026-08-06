"""Log de punição em Components V2 + tópico restrito de provas."""

from __future__ import annotations

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

    thread = None
    try:
        thread = await msg.create_thread(
            name="📁 Provas anexadas",
            auto_archive_duration=10080,
        )
        # Aviso no tópico
        await thread.send(
            "📁 **Provas anexadas**\n"
            "_Tópico restrito — apenas staff com permissão de moderação deve escrever aqui._"
        )

        # Links FORA de container para preview automático do Discord
        if links:
            bloco_links = "🔗 **Links**\n" + "\n".join(links)
            await thread.send(bloco_links)

        # Tenta travar envio de membros comuns (moderadores ainda escrevem)
        try:
            await thread.edit(locked=True)
        except discord.HTTPException:
            pass

    except discord.HTTPException:
        thread = None

    return msg, thread
