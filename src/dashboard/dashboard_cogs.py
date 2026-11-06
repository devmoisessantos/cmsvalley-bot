"""
Comandos administrativos do domínio dashboard.

/dashboard status   — mostra se a integração está ativa e o último estado
/dashboard sync     — força republicação do painel de KPIs
/dashboard catalogo — resume o que cada métrica significa e de onde vem
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.config import DASHBOARD_API_TOKEN, DASHBOARD_API_URL
from src.dashboard.dashboard_catalogo import (
    FONTES_DE_DADOS,
    METAS_CATALOGO,
    montar_catalogo_para_api,
)
from src.dashboard.dashboard_cliente import ClienteDashboard
from src.dashboard.dashboard_panel import montar_painel_de_status
from src.dashboard.dashboard_service import (
    VERSAO_BOT,
    id_canal_padrao,
    montar_corpo_do_sinal,
    montar_snapshot_da_guild,
    publicar_painel_de_metas,
)
from src.utils.mensagens import (
    responder_erro,
    responder_info,
    responder_sucesso,
    responder_view,
)
from src.utils.permissions import esta_autorizado

registrador = logging.getLogger(__name__)


class DashboardCogs(commands.Cog):
    """Comandos de barra para operar o dashboard a partir do Discord."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cliente = ClienteDashboard()

    async def cog_load(self) -> None:
        await self.cliente.abrir()

    async def cog_unload(self) -> None:
        await self.cliente.fechar()

    grupo = app_commands.Group(
        name="dashboard",
        description="Integração com o dashboard web (cmsvalley-api).",
    )

    @grupo.command(
        name="status",
        description="Mostra se a ponte com a API está ativa e o estado do bot.",
    )
    @esta_autorizado()
    async def comando_status(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Card técnico: URL, token, latência e snapshot."""
        ativo = bool(DASHBOARD_API_URL and DASHBOARD_API_TOKEN)
        corpo = montar_corpo_do_sinal(self.bot)
        bot_info = {
            "online": ativo,
            "latencia_ms": corpo.get("latencia_ms"),
            "guilds": corpo.get("guilds"),
            "membros": corpo.get("membros"),
            "cogs": corpo.get("cogs"),
            "versao": VERSAO_BOT,
            "motivo": (
                ""
                if ativo
                else "DASHBOARD_API_URL ou DASHBOARD_API_TOKEN ausentes"
            ),
        }
        view = montar_painel_de_status(bot_info)
        await responder_view(interacao, view, ephemeral=True)

        extras = (
            f"API: `{DASHBOARD_API_URL or '—'}`\n"
            f"Canal padrão: `{id_canal_padrao() or 'não configurado'}`\n"
            f"Token configurado: `{'sim' if DASHBOARD_API_TOKEN else 'não'}`"
        )
        try:
            await interacao.followup.send(extras, ephemeral=True)
        except Exception:  # noqa: BLE001
            pass

    @grupo.command(
        name="sync",
        description="Força a republicação do painel de KPIs no canal do dashboard.",
    )
    @esta_autorizado()
    async def comando_sync(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Busca overview na API e atualiza o card de metas no Discord."""
        await interacao.response.defer(ephemeral=True)
        if not self.cliente.ativo:
            await responder_erro(
                interacao,
                "Integração desligada: configure DASHBOARD_API_URL e "
                "DASHBOARD_API_TOKEN no .env do bot.",
            )
            return
        try:
            quantidade = await publicar_painel_de_metas(
                self.bot,
                self.cliente,
            )
        except Exception as falha:  # noqa: BLE001
            registrador.exception("Falha no /dashboard sync")
            await responder_erro(
                interacao,
                f"Não consegui sincronizar as metas: {falha}",
            )
            return

        if quantidade == 0:
            await responder_info(
                interacao,
                "Sincronização ok, mas nenhuma meta foi renderizada "
                "(canal ausente ou overview vazio).",
            )
            return
        await responder_sucesso(
            interacao,
            f"Painel de KPIs atualizado com {quantidade} meta(s).",
        )

    @grupo.command(
        name="catalogo",
        description="Lista o que cada métrica significa e de qual tabela vem.",
    )
    @esta_autorizado()
    async def comando_catalogo(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Ensina a equipe (e a API) de onde saem os números do dashboard."""
        linhas_metas = []
        for chave, meta in METAS_CATALOGO.items():
            origem = meta["origem"]
            tabelas = ", ".join(origem["tabelas"])
            linhas_metas.append(
                f"**{meta['rotulo']}** (`{chave}`)\n"
                f"Tabelas: `{tabelas}`\n"
                f"{origem['regra']}"
            )

        linhas_fontes = []
        for nome, fonte in FONTES_DE_DADOS.items():
            linhas_fontes.append(
                f"**{nome}** → `{fonte['tabela']}`\n"
                f"{fonte['descricao']}"
            )

        texto = (
            "# Catálogo do dashboard\n"
            "## Metas\n"
            + "\n\n".join(linhas_metas)
            + "\n\n## Fontes no banco do bot\n"
            + "\n\n".join(linhas_fontes)
        )
        # Resposta longa: cortamos se passar do limite do Discord.
        if len(texto) > 1800:
            texto = texto[:1800] + "\n…"

        await responder_info(interacao, texto)

    @grupo.command(
        name="snapshot",
        description="Envia agora o snapshot de cargos/canais para a API.",
    )
    @esta_autorizado()
    async def comando_snapshot(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Força um sinal do bot completo com cargos e canais reais."""
        await interacao.response.defer(ephemeral=True)
        if not self.cliente.ativo:
            await responder_erro(
                interacao,
                "Integração desligada: configure URL e token do dashboard.",
            )
            return

        corpo = montar_corpo_do_sinal(self.bot)
        cargos, canais, info = montar_snapshot_da_guild(self.bot)
        resposta = await self.cliente.enviar_sinal_do_bot(corpo)
        if resposta is None:
            await responder_erro(
                interacao,
                "A API não aceitou o sinal do bot. "
                "Confira URL, token e se o banco da API está no ar.",
            )
            return

        await responder_sucesso(
            interacao,
            (
                f"Snapshot enviado: {len(cargos)} cargos, "
                f"{len(canais)} canais, guild `{info.get('name', '—')}`. "
                f"Catálogo v{montar_catalogo_para_api()['versao_catalogo']}."
            ),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DashboardCogs(bot))
