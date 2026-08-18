# src/cogs/avaliar_atendimento.py
"""
Comando /avaliar-atendimento

Publica a avaliação no canal CANAL_AVALIAR_ATENDIMENTO.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from src.config import CANAIS
from src.utils.error_handling import enviar_erro_para_log_erros
from src.utils.formatacao import agora_brasilia
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)


def _montar_estrelas(nota: int) -> str:
    """Ex.: 3 → ⭐⭐⭐☆☆"""
    nota_limitada = max(1, min(5, int(nota)))
    return "⭐" * nota_limitada + "☆" * (5 - nota_limitada)


def _formatar_data_avaliacao() -> str:
    """Ex.: 13/08/2026 às 18:40"""
    local = agora_brasilia()
    return (
        f"{local.day:02d}/{local.month:02d}/{local.year} às {local.strftime('%H:%M')}"
    )


class AvaliarAtendimentoCog(commands.Cog):
    """Avaliação de atendimento médico/hospitalar."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="avaliar-atendimento",
        description="Registra uma avaliação de atendimento (1 a 5 estrelas)",
    )
    @app_commands.describe(
        membro="Membro que atendeu (médico, paramédico, etc.)",
        nota="Nota de 1 a 5 estrelas",
        comentario="Comentário opcional sobre o atendimento",
    )
    @app_commands.choices(
        nota=[
            app_commands.Choice(name="⭐ 1", value=1),
            app_commands.Choice(name="⭐⭐ 2", value=2),
            app_commands.Choice(name="⭐⭐⭐ 3", value=3),
            app_commands.Choice(name="⭐⭐⭐⭐ 4", value=4),
            app_commands.Choice(name="⭐⭐⭐⭐⭐ 5", value=5),
        ]
    )
    async def avaliar_atendimento(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
        nota: app_commands.Choice[int],
        comentario: str | None = None,
    ):
        """Publica uma avaliação somente quando o contexto e a nota são válidos.

        Impede avaliações de bots, notas fora da escala e envios sem canal configurado
        antes de publicar o cartão no Discord. A confirmação ao usuário só é enviada
        depois da publicação, evitando informar sucesso para uma avaliação não exibida.
        """
        if interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use este comando dentro do servidor."],
            )
            return

        valor_nota = int(nota.value) if hasattr(nota, "value") else int(nota)
        if valor_nota < 1 or valor_nota > 5:
            await responder_aviso(
                interacao,
                titulo="Nota inválida",
                linhas=["A nota deve ser entre **1** e **5**."],
            )
            return

        if membro.bot:
            await responder_aviso(
                interacao,
                titulo="Membro inválido",
                linhas=["Não é possível avaliar um bot."],
            )
            return

        canal_id = CANAIS.get("CANAL_AVALIAR_ATENDIMENTO") or 0
        canal = interacao.guild.get_channel(int(canal_id)) if canal_id else None
        if canal is None:
            await responder_erro(
                interacao,
                titulo="Canal não configurado",
                linhas=[
                    "O canal `CANAL_AVALIAR_ATENDIMENTO` não foi encontrado.",
                    "Peça à diretoria para conferir o config.",
                ],
            )
            return

        await interacao.response.defer(ephemeral=True)

        texto_comentario = (comentario or "").strip()
        bloco_comentario = (
            f"> ```{texto_comentario}```" if texto_comentario else "> _Sem comentário._"
        )
        data_txt = _formatar_data_avaliacao()
        estrelas = _montar_estrelas(valor_nota)

        corpo = (
            f"`👤` * **Avaliador:** {interacao.user.mention}\n"
            f"`⚕️` * **Para:** {membro.mention}\n"
            f"`⭐` * **Nota:** {estrelas} · **{valor_nota}/5**\n"
            f"{bloco_comentario}"
        )

        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# ⭐ Avaliação de Atendimento"
                    '\n> Digite "**/avaliar-atendimento**" para fazer uma avaliação.'
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.Section(
                    corpo,
                    accessory=discord.ui.Thumbnail(membro.display_avatar.url),
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    f"-# ✅ *Avaliação registrada com sucesso!* · {data_txt}"
                ),
                accent_color=discord.Color.gold(),
            )
        )

        try:
            await canal.send(view=view)
        except discord.HTTPException as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Falha ao postar avaliação de atendimento",
                erro,
                contexto="avaliar_atendimento.canal.send",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Não foi possível registrar",
                linhas=["Falha ao enviar no canal de avaliações. Tente de novo."],
            )
            return

        await responder_sucesso(
            interacao,
            titulo="Avaliação enviada",
            linhas=[
                f"**Para:** {membro.mention}",
                f"**Nota:** {estrelas} ({valor_nota}/5)",
                f"Publicada em <#{canal.id}>.",
            ],
            delay=15,
        )


async def setup(bot: commands.Bot):
    """Registra o comando de avaliação de atendimento durante a inicialização do bot."""
    await bot.add_cog(AvaliarAtendimentoCog(bot))
