"""Publicação do laudo no canal oficial e no log interno."""

from __future__ import annotations

import discord

from src.config import CANAIS
from src.database.connection import async_session
from src.database.models import Laudo
from src.utils.log_container import LogContainerView


async def publicar_laudo_nos_canais(
    *,
    guild: discord.Guild,
    texto_laudo: str,
    laudo: Laudo,
    psicologo: discord.Member,
    paciente: discord.Member | None,
) -> discord.Message | None:
    """
    Envia o laudo em CANAL_LAUDOS (público operacional)
    e um resumo em LOG_LAUDO.
    """
    canal_laudos = guild.get_channel(CANAIS.get("CANAL_LAUDOS", 0))
    mensagem_publica: discord.Message | None = None

    if canal_laudos is not None:
        layout = discord.ui.LayoutView(timeout=None)
        cor = (
            discord.Color.green()
            if laudo.parecer == "APROVADO"
            else discord.Color.red()
        )
        layout.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(texto_laudo),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    f"-# Registro `#{laudo.id}` · Consulta `#{laudo.consulta_id}` · {guild.name}"
                ),
                accent_color=cor,
            )
        )
        mensagem_publica = await canal_laudos.send(view=layout)

        if mensagem_publica is not None:
            try:
                async with async_session() as sessao:
                    registro = await sessao.get(Laudo, laudo.id)
                    if registro is not None:
                        registro.canal_laudo_message_id = mensagem_publica.id
                        await sessao.commit()
            except Exception as erro:
                print(f"⚠️ [laudos] não gravou message_id: {erro}")

    canal_log = guild.get_channel(CANAIS.get("LOG_LAUDO", 0))
    if canal_log is not None:
        mencao_paciente = (
            paciente.mention if paciente else f"<@{laudo.discord_id_paciente}>"
        )
        linhas = (
            f"- **Laudo:** `#{laudo.id}`\n"
            f"- **Consulta:** `#{laudo.consulta_id}`\n"
            f"- **Paciente:** {mencao_paciente} · passaporte `{laudo.id_fivem_paciente or '—'}`\n"
            f"- **Psicólogo:** {psicologo.mention} · passaporte `{laudo.id_fivem_psicologo or '—'}`\n"
            f"- **Parecer:** `{laudo.parecer}`\n"
            f"- **CRP:** `{laudo.registro_profissional}`"
        )
        view_log = LogContainerView(
            titulo="🧠 Laudo psicológico registrado",
            linhas=linhas,
            guild=guild,
            cor=(
                discord.Color.green()
                if laudo.parecer == "APROVADO"
                else discord.Color.red()
            ),
            avatar_url=psicologo.display_avatar.url,
        )
        await canal_log.send(view=view_log)

    return mensagem_publica
