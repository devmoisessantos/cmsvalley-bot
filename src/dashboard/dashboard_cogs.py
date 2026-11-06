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
    sincronizar_metas_por_cargo_do_dashboard,
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

    @grupo.command(
        name="sync",
        description="Sincroniza metas por cargo e republica o painel de KPIs.",
    )
    @esta_autorizado()
    async def comando_sync(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Puxa metas da API e atualiza o card de KPIs no Discord."""
        await interacao.response.defer(ephemeral=True)
        if not self.cliente.ativo:
            await responder_erro(
                interacao,
                "Integração desligada",
                [
                    "Configure `DASHBOARD_API_URL` e "
                    "`DASHBOARD_API_TOKEN` no ambiente do bot."
                ],
            )
            return

        cargos = await sincronizar_metas_por_cargo_do_dashboard(self.cliente)
        quantidade = await publicar_painel_de_metas(self.bot, self.cliente)
        canal = id_canal_padrao()
        await responder_sucesso(
            interacao,
            "Sincronização concluída",
            [
                f"Metas por cargo aplicadas: **{cargos}**.",
                f"Painel de KPIs com **{quantidade}** meta(s).",
                f"Canal padrão: `{canal or 'não configurado'}`.",
            ],
        )

    @grupo.command(
        name="catalogo",
        description="Lista metas e fontes de dados do dashboard.",
    )
    @esta_autorizado()
    async def comando_catalogo(
        self,
        interacao: discord.Interaction,
    ) -> None:
        """Ensina a equipe de onde saem os números do dashboard."""
        linhas: list[str] = ["## Metas de referência"]
        for chave, meta in METAS_CATALOGO.items():
            origem = meta["origem"]
            tabelas = ", ".join(origem["tabelas"])
            linhas.append(
                f"**{meta['rotulo']}** (`{chave}`) — tabelas: `{tabelas}`"
            )
            linhas.append(origem["regra"])

        linhas.append("## Fontes no banco do bot")
        for nome, fonte in FONTES_DE_DADOS.items():
            linhas.append(
                f"**{nome}** → `{fonte['tabela']}` — {fonte['descricao']}"
            )

        # Cards não gostam de texto infinito; corta com segurança.
        texto_cheio = "\n".join(linhas)
        if len(texto_cheio) > 3500:
            linhas = linhas[:40]
            linhas.append("… (catálogo truncado)")

        await responder_info(
            interacao,
            "Catálogo do dashboard",
            linhas,
            delay=30,
        )

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
                "Integração desligada",
                [
                    "Configure URL e token do dashboard no ambiente do bot."
                ],
            )
            return

        try:
            cargos, canais, info = montar_snapshot_da_guild(self.bot)
        except Exception as falha:  # noqa: BLE001
            registrador.exception("Falha ao montar snapshot: %s", falha)
            await responder_erro(
                interacao,
                "Snapshot falhou",
                [f"Não consegui montar cargos/canais: `{falha}`."],
            )
            return

        corpo = montar_corpo_do_sinal(self.bot)
        resposta = await self.cliente.enviar_sinal_do_bot(corpo)
        if resposta is None:
            await responder_erro(
                interacao,
                "API não respondeu",
                [
                    "O sinal do bot não foi aceito.",
                    "Confira URL, token e se a API está no ar "
                    "(rotas `/internal/*`).",
                ],
            )
            return

        await responder_sucesso(
            interacao,
            "Snapshot enviado",
            [
                f"**{len(cargos)}** cargos, **{len(canais)}** canais.",
                f"Guild: `{info.get('name', '—')}`.",
                f"Catálogo v{montar_catalogo_para_api()['versao_catalogo']}.",
            ],
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DashboardCogs(bot))
