# src/cogs/manutencao.py
"""
Grupo /manutencao — ferramentas pós-deploy e recuperação sem reiniciar o bot.

  /manutencao paineis-listar
  /manutencao paineis-recriar
  /manutencao paineis-recriar-todos
  /manutencao sincronizar-comandos
  /manutencao keepalive-status
  /manutencao keepalive-ping
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import (
    commands,
    tasks,
)

from src.config import GUILD_ID
from src.manutencao.manutencao_paineis import (
    NOMES_DOS_PAINEIS,
    listar_paineis_no_banco,
    recriar_painel,
    recriar_todos_os_paineis,
)
from src.utils.keepalive_api import (
    KEEPALIVE_INTERVALO_MINUTOS,
    montar_url_keepalive,
    ping_api_keepalive,
)
from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    enviar_card,
)
from src.utils.permissions import apenas_administrador


class ManutencaoCog(commands.Cog):
    """Comandos de manutenção e keep-alive da API externa."""

    grupo_manutencao = app_commands.Group(
        name="manutencao",
        description="Ferramentas de manutenção pós-deploy (somente Administradores)",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ultimo_keepalive_ok: bool | None = None
        self.ultimo_keepalive_detalhe: str = "Ainda não executou."
        self.tarefa_keepalive.change_interval(minutes=KEEPALIVE_INTERVALO_MINUTOS)
        self.tarefa_keepalive.start()

    def cog_unload(self):
        """Cancela o ping recorrente para não manter tarefas órfãs após descarregar."""
        self.tarefa_keepalive.cancel()

    # ------------------------------------------------------------------
    # Keep-alive automático da API (Render)
    # ------------------------------------------------------------------

    @tasks.loop(minutes=10)
    async def tarefa_keepalive(self):
        """Atualiza periodicamente o estado da API externa sem intervenção humana."""
        sucesso, detalhe = await ping_api_keepalive()
        self.ultimo_keepalive_ok = sucesso
        self.ultimo_keepalive_detalhe = detalhe

    @tarefa_keepalive.before_loop
    async def _antes_do_keepalive(self):
        await self.bot.wait_until_ready()
        # Ping imediato ao subir, para acordar a API no deploy
        sucesso, detalhe = await ping_api_keepalive()
        self.ultimo_keepalive_ok = sucesso
        self.ultimo_keepalive_detalhe = detalhe

    # ------------------------------------------------------------------
    # /manutencao paineis-listar
    # ------------------------------------------------------------------

    @grupo_manutencao.command(
        name="paineis-listar",
        description="Lista os painéis registrados no banco de dados",
    )
    @apenas_administrador()
    async def paineis_listar(self, interacao: discord.Interaction):
        """Compara painéis conhecidos com seus registros persistidos no banco.

        Destaca ausências e sobras para que administradores saibam se um painel
        pode ser recuperado automaticamente ou se há um registro obsoleto após
        mudanças de configuração.
        """
        registros = await listar_paineis_no_banco()
        nomes_conhecidos = set(NOMES_DOS_PAINEIS)
        nomes_no_banco = {registro.nome_painel for registro in registros}

        linhas = []
        for nome in NOMES_DOS_PAINEIS:
            if nome in nomes_no_banco:
                registro = next(
                    registro_do_painel
                    for registro_do_painel in registros
                    if registro_do_painel.nome_painel == nome
                )
                linhas.append(
                    f"`✅` **{nome}** · canal `{registro.canal_id}` · msg "
                    f"`{registro.message_id}"
                    f"`"
                )
            else:
                linhas.append(
                    f"`❌` **{nome}** · sem registro (será criado no próximo garantir)"
                )

        extras = nomes_no_banco - nomes_conhecidos
        for nome_extra in sorted(extras):
            linhas.append(f"`⚠️` **{nome_extra}** · no banco, mas sem função de recriar")

        if not linhas:
            linhas = ["Nenhum painel conhecido."]

        await enviar_card(
            interacao,
            titulo="📋 Painéis no banco",
            linhas=linhas,
            cor=COR_INFO,
            delay=40,
        )

    # ------------------------------------------------------------------
    # /manutencao paineis-recriar
    # ------------------------------------------------------------------

    @grupo_manutencao.command(
        name="paineis-recriar",
        description="Apaga registro/mensagem antiga e publica o painel atualizado",
    )
    @app_commands.describe(nome="Qual painel recriar")
    @app_commands.choices(
        nome=[
            app_commands.Choice(name=nome_painel, value=nome_painel)
            for nome_painel in NOMES_DOS_PAINEIS
        ]
    )
    @apenas_administrador()
    async def paineis_recriar(
        self,
        interacao: discord.Interaction,
        nome: app_commands.Choice[str],
    ):
        """Substitui um painel específico e informa o resultado de forma privada.

        Reconhece interações expiradas antes de executar a recuperação. A rotina
        de serviço apaga a referência e a mensagem anteriores antes de publicar
        a versão atual, evitando controles duplicados no Discord.
        """
        try:
            await interacao.response.defer(ephemeral=True, thinking=True)
        except discord.NotFound:
            # Interação expirou (>3s) — bot lento ou pool de DB travado
            return
        except discord.HTTPException:
            return

        resultado = await recriar_painel(self.bot, nome.value)

        await enviar_card(
            interacao,
            titulo=(
                f"✅ Painel `{nome.value}` recriado"
                if resultado.ok
                else f"❌ Falha em `{nome.value}`"
            ),
            linhas=[resultado.mensagem],
            cor=COR_SUCESSO if resultado.ok else COR_ERRO,
            delay=25,
        )

    # ------------------------------------------------------------------
    # /manutencao paineis-recriar-todos
    # ------------------------------------------------------------------

    @grupo_manutencao.command(
        name="paineis-recriar-todos",
        description="Recria TODOS os painéis (apaga antigos e publica de novo)",
    )
    @apenas_administrador()
    async def paineis_recriar_todos(self, interacao: discord.Interaction):
        """Recupera todos os painéis e resume falhas sem interromper os demais.

        Executa a recriação em lote depois de confirmar a interação e mostra a
        situação individual de cada painel. Isso reduz o risco de deixar o bot
        parcialmente recuperado sem que a administração perceba.
        """
        try:
            await interacao.response.defer(ephemeral=True, thinking=True)
        except discord.NotFound:
            # Interação expirou (>3s) — bot lento ou pool de DB travado
            return
        except discord.HTTPException:
            return

        resultados = await recriar_todos_os_paineis(self.bot)

        linhas = []
        for resultado in resultados:
            emoji = "✅" if resultado.ok else "❌"
            linhas.append(f"{emoji} **{resultado.nome}** — {resultado.mensagem}")

        quantidade_ok = sum(
            1 for resultado_da_recriacao in resultados if resultado_da_recriacao.ok
        )
        await enviar_card(
            interacao,
            titulo=f"🔄 Painéis recriados ({quantidade_ok}/{len(resultados)})",
            linhas=linhas,
            cor=COR_SUCESSO if quantidade_ok == len(resultados) else COR_AVISO,
            delay=60,
        )

    # ------------------------------------------------------------------
    # /manutencao sincronizar-comandos
    # ------------------------------------------------------------------

    @grupo_manutencao.command(
        name="sincronizar-comandos",
        description="Reenvia os slash commands para o Discord (sem reiniciar o bot)",
    )
    @apenas_administrador()
    async def sincronizar_comandos(self, interacao: discord.Interaction):
        """Reenvia comandos de barra à guilda sem exigir reinício do bot.

        Copia os comandos globais para a guilda configurada antes da sincronização,
        agilizando a disponibilidade após um deploy. As falhas são devolvidas
        em um card privado para não ocultar problemas de permissão ou conexão.
        """
        try:
            await interacao.response.defer(ephemeral=True, thinking=True)
        except discord.NotFound:
            # Interação expirou (>3s) — bot lento ou pool de DB travado
            return
        except discord.HTTPException:
            return

        try:
            guild_object = discord.Object(id=GUILD_ID)
            self.bot.tree.copy_global_to(guild=guild_object)
            sincronizados = await self.bot.tree.sync(guild=guild_object)
            await enviar_card(
                interacao,
                titulo="✅ Comandos sincronizados",
                linhas=[
                    f"Quantidade enviada ao Discord: **{len(sincronizados)}**",
                    f"Servidor: `{GUILD_ID}`",
                ],
                cor=COR_SUCESSO,
                delay=20,
            )
        except Exception as erro:
            await enviar_card(
                interacao,
                titulo="❌ Falha na sincronização",
                linhas=[str(erro)],
                cor=COR_ERRO,
                delay=20,
            )

    # ------------------------------------------------------------------
    # /manutencao keepalive-status
    # ------------------------------------------------------------------

    @grupo_manutencao.command(
        name="keepalive-status",
        description="Mostra o estado do keep-alive da API (Render)",
    )
    @apenas_administrador()
    async def keepalive_status(self, interacao: discord.Interaction):
        """Mostra o último resultado e a tarefa que mantém a API ativa."""
        if self.ultimo_keepalive_ok is True:
            emoji = "✅"
        elif self.ultimo_keepalive_ok is False:
            emoji = "❌"
        else:
            emoji = "⏳"

        await enviar_card(
            interacao,
            titulo="🌐 Keep-alive da API",
            linhas=[
                f"URL: `{montar_url_keepalive()}`",
                f"Intervalo: a cada **{KEEPALIVE_INTERVALO_MINUTOS}** minutos",
                f"Tarefa rodando: **{self.tarefa_keepalive.is_running()}**",
                f"Último ping: {emoji} {self.ultimo_keepalive_detalhe}",
            ],
            cor=COR_INFO,
            delay=25,
        )

    # ------------------------------------------------------------------
    # /manutencao keepalive-ping
    # ------------------------------------------------------------------

    @grupo_manutencao.command(
        name="keepalive-ping",
        description="Força um ping imediato na API (acorda o Render agora)",
    )
    @apenas_administrador()
    async def keepalive_ping(self, interacao: discord.Interaction):
        """Força teste imediato da API e atualiza o estado do monitoramento."""
        try:
            await interacao.response.defer(ephemeral=True, thinking=True)
        except discord.NotFound:
            # Interação expirou (>3s) — bot lento ou pool de DB travado
            return
        except discord.HTTPException:
            return

        sucesso, detalhe = await ping_api_keepalive()
        self.ultimo_keepalive_ok = sucesso
        self.ultimo_keepalive_detalhe = detalhe

        await enviar_card(
            interacao,
            titulo="🌐 Ping na API" + (" — ok" if sucesso else " — falhou"),
            linhas=[detalhe],
            cor=COR_SUCESSO if sucesso else COR_ERRO,
            delay=15,
        )

    # ------------------------------------------------------------------
    # /manutencao ajuda
    # ------------------------------------------------------------------

    @grupo_manutencao.command(
        name="ajuda",
        description="Explica os comandos de manutenção",
    )
    @apenas_administrador()
    async def ajuda(self, interacao: discord.Interaction):
        """Envia guia privado para evitar uso indevido de ações de recuperação."""
        await enviar_card(
            interacao,
            titulo="🛠️ Ajuda · Manutenção",
            linhas=[
                "`/manutencao paineis-listar` — o que está salvo no banco.",
                "`/manutencao paineis-recriar` — apaga um painel e publica de novo.",
                "`/manutencao paineis-recriar-todos` — recria todos os painéis.",
                "`/manutencao sincronizar-comandos` — atualiza slash commands no "
                "Discord.",
                "`/manutencao keepalive-status` — estado do ping na API Render.",
                "`/manutencao keepalive-ping` — acorda a API agora.",
                "O keep-alive roda sozinho enquanto o bot estiver online.",
            ],
            cor=COR_INFO,
            delay=40,
        )


async def setup(bot: commands.Bot):
    """Adiciona ao bot as ferramentas administrativas e o keep-alive da API."""
    await bot.add_cog(ManutencaoCog(bot))
