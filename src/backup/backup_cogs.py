"""Comandos e listeners do sistema de backup.

Grupo único: /backup

  criar · listar · exportar · deletar · comparar · status
  restaurar-cargos · restaurar-canais · restaurar-membros · restaurar-tudo
  rejoin · sincronizar-membros

Todas as respostas usam src.utils.mensagens
(CardView, responder_card, responder_ephemera, excluir_mensagem).

Apenas Administradores.
"""

from __future__ import annotations

import asyncio
import os

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.backup.backup_logger import BackupLogger
from src.backup.backup_manager import BackupManager
from src.backup.diff_engine import DiffEngine
from src.backup.member_snapshot import (
    definir_rejoin,
    rejoin_esta_ativo,
    restaurar_cargos_no_rejoin,
    salvar_snapshot_membro,
    sincronizar_todos_os_membros,
)
from src.backup.restore_manager import RestoreManager
from src.config import (
    AUTO_BACKUP_INTERVAL_HOURS,
    BACKUP_DIR,
    CONFIRMATION_TIMEOUT,
    LOG_CHANNEL_NAME,
    MAX_BACKUPS_PER_GUILD,
)
from src.utils.mensagens import (
    CardView,
    excluir_mensagem,
    responder_card,
    responder_ephemera,
)
from src.utils.permissions import apenas_administrador
from src.utils.views import ConfirmView


class BackupCog(commands.Cog):
    """Todos os comandos de backup ficam neste único grupo."""

    grupo_backup = app_commands.Group(
        name="backup",
        description="Backup e restauração do servidor (somente Administradores)",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.gerenciador = BackupManager()
        self.restaurador = RestoreManager()
        self.comparador = DiffEngine()
        self.logger = BackupLogger(LOG_CHANNEL_NAME)
        self.tarefa_backup_automatico.change_interval(hours=AUTO_BACKUP_INTERVAL_HOURS)
        self.tarefa_backup_automatico.start()

    def cog_unload(self):
        self.tarefa_backup_automatico.cancel()

    # ══════════════════════════════════════════════════════════════════════
    # Helpers de resposta (mensagens.py + followup quando já deferiu)
    # ══════════════════════════════════════════════════════════════════════

    async def _enviar_card(
        self,
        interaction: discord.Interaction,
        titulo: str,
        linhas: list[str],
        cor: discord.Color = discord.Color.blurple(),
        delay: int | None = 15,
        extra_row: discord.ui.ActionRow | None = None,
    ) -> discord.Message:
        """Envia CardView. Se a interaction já foi respondida, usa followup."""
        view = CardView(
            titulo=titulo,
            linhas=linhas,
            cor=cor,
            timeout=None,
            extra_row=extra_row,
        )
        if interaction.response.is_done():
            mensagem = await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.send_message(view=view, ephemeral=True)
            mensagem = await interaction.original_response()

        if delay is not None:
            asyncio.create_task(excluir_mensagem(mensagem, delay=delay))
        return mensagem

    async def _carregar_backup_alvo(
        self,
        interaction: discord.Interaction,
        nome_arquivo: str | None,
    ) -> tuple[dict | None, str | None]:
        arquivo = nome_arquivo or self.gerenciador.nome_backup_mais_recente(
            interaction.guild.id
        )
        if not arquivo:
            return None, None
        backup = self.gerenciador.carregar_backup(interaction.guild.id, arquivo)
        return backup, arquivo

    async def _pedir_confirmacao(
        self,
        interaction: discord.Interaction,
        titulo: str,
        linhas: list[str],
    ) -> bool:
        """Mensagem com botões Confirmar/Cancelar. Não auto-apaga enquanto espera."""
        view_botoes = ConfirmView(
            autor_id=interaction.user.id,
            timeout=CONFIRMATION_TIMEOUT,
        )
        corpo = "\n".join(f"`•` {linha}" for linha in linhas if linha is not None)
        mensagem = await interaction.followup.send(
            content=f"**{titulo}**\n{corpo}",
            view=view_botoes,
            ephemeral=True,
        )
        await view_botoes.wait()
        asyncio.create_task(excluir_mensagem(mensagem, delay=3))
        return bool(view_botoes.value)

    def _backup_de_seguranca(self, guild: discord.Guild, autor: str) -> None:
        backup = self.gerenciador.criar_backup(
            guild, criado_por=f"Auto (antes de restore por {autor})"
        )
        self.gerenciador.salvar_backup(backup)

    # ══════════════════════════════════════════════════════════════════════
    # Backup automático
    # ══════════════════════════════════════════════════════════════════════

    @tasks.loop(hours=24)
    async def tarefa_backup_automatico(self):
        for guild in self.bot.guilds:
            try:
                backup = self.gerenciador.criar_backup(
                    guild, criado_por="Sistema (automático)"
                )
                self.gerenciador.salvar_backup(backup)
                await self.logger.log(
                    guild,
                    "🔄 Backup automático concluído",
                    (
                        f"Cargos: {len(backup['roles'])} | "
                        f"Canais: {len(backup['channels'])} | "
                        f"Categorias: {len(backup['categories'])} | "
                        f"Membros: {len(backup['members'])}"
                    ),
                    discord.Color.green(),
                )
            except Exception as erro:
                print(f"Erro no backup automático de {guild.name}: {erro}")

    @tarefa_backup_automatico.before_loop
    async def _antes_do_backup_automatico(self):
        await self.bot.wait_until_ready()

    # ══════════════════════════════════════════════════════════════════════
    # Listeners — snapshot vivo
    # ══════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_member_update(self, antes: discord.Member, depois: discord.Member):
        cargos_antes = {cargo.id for cargo in antes.roles}
        cargos_depois = {cargo.id for cargo in depois.roles}
        if cargos_antes != cargos_depois or antes.nick != depois.nick:
            await salvar_snapshot_membro(depois)

    @commands.Cog.listener()
    async def on_member_remove(self, membro: discord.Member):
        await salvar_snapshot_membro(membro)

    @commands.Cog.listener()
    async def on_member_join(self, membro: discord.Member):
        if membro.bot:
            return
        relatorio = await restaurar_cargos_no_rejoin(membro)
        if relatorio:
            await self.logger.log(
                membro.guild,
                "🔁 Rejoin — restore de cargos",
                "\n".join(relatorio),
                discord.Color.teal(),
                autor="Sistema",
            )

    # ══════════════════════════════════════════════════════════════════════
    # /backup criar
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="criar", description="Cria um backup manual do servidor agora"
    )
    @apenas_administrador()
    async def criar(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        backup = self.gerenciador.criar_backup(
            interaction.guild, criado_por=str(interaction.user)
        )
        caminho = self.gerenciador.salvar_backup(backup)
        nome_arquivo = caminho.split("/")[-1]

        try:
            await sincronizar_todos_os_membros(interaction.guild)
        except Exception as erro:
            print(f"Aviso ao sincronizar snapshots: {erro}")

        await self._enviar_card(
            interaction,
            titulo="✅ Backup criado",
            linhas=[
                f"Arquivo: `{nome_arquivo}`",
                f"Cargos: **{len(backup['roles'])}**",
                f"Canais: **{len(backup['channels'])}**",
                f"Categorias: **{len(backup['categories'])}**",
                f"Membros: **{len(backup['members'])}**",
            ],
            cor=discord.Color.green(),
            delay=20,
        )
        await self.logger.log(
            interaction.guild,
            "💾 Backup manual criado",
            f"Arquivo `{nome_arquivo}` via /backup criar.",
            discord.Color.blue(),
            autor=str(interaction.user),
        )

    # ══════════════════════════════════════════════════════════════════════
    # /backup listar
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="listar", description="Lista os backups disponíveis neste servidor"
    )
    @apenas_administrador()
    async def listar(self, interaction: discord.Interaction):
        arquivos = self.gerenciador.listar_backups(interaction.guild.id)
        if not arquivos:
            await responder_card(
                interaction,
                titulo="📂 Backups",
                linhas=["Nenhum backup encontrado para este servidor."],
                cor=discord.Color.orange(),
                delay=12,
            )
            return

        linhas = [
            f"`{indice + 1}.` {nome}" for indice, nome in enumerate(arquivos[:20])
        ]
        await responder_card(
            interaction,
            titulo="📂 Backups disponíveis",
            linhas=linhas,
            cor=discord.Color.blurple(),
            delay=25,
        )

    # ══════════════════════════════════════════════════════════════════════
    # /backup exportar
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="exportar",
        description="Baixa o backup mais recente (ou um específico) como arquivo",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def exportar(
        self, interaction: discord.Interaction, arquivo: str | None = None
    ):
        nome = arquivo or self.gerenciador.nome_backup_mais_recente(
            interaction.guild.id
        )
        if not nome:
            await responder_card(
                interaction,
                titulo="📥 Exportar",
                linhas=["Nenhum backup encontrado."],
                cor=discord.Color.orange(),
                delay=10,
            )
            return

        caminho = self.gerenciador.caminho_completo(interaction.guild.id, nome)
        try:
            await interaction.response.send_message(
                content=f"📥 Backup: `{nome}`",
                file=discord.File(caminho),
                ephemeral=True,
            )
            mensagem = await interaction.original_response()
            asyncio.create_task(excluir_mensagem(mensagem, delay=60))
        except FileNotFoundError:
            await responder_card(
                interaction,
                titulo="📥 Exportar",
                linhas=["Arquivo não encontrado no disco."],
                cor=discord.Color.red(),
                delay=10,
            )

    # ══════════════════════════════════════════════════════════════════════
    # /backup deletar
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="deletar", description="Apaga um backup específico pelo nome do arquivo"
    )
    @app_commands.describe(arquivo="Nome exato do arquivo JSON")
    @apenas_administrador()
    async def deletar(self, interaction: discord.Interaction, arquivo: str):
        sucesso = self.gerenciador.deletar_backup(interaction.guild.id, arquivo)
        if sucesso:
            await responder_card(
                interaction,
                titulo="🗑️ Backup deletado",
                linhas=[f"Arquivo `{arquivo}` removido com sucesso."],
                cor=discord.Color.green(),
                delay=12,
            )
        else:
            await responder_card(
                interaction,
                titulo="🗑️ Deletar backup",
                linhas=["Arquivo não encontrado."],
                cor=discord.Color.red(),
                delay=10,
            )

    # ══════════════════════════════════════════════════════════════════════
    # /backup comparar
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="comparar",
        description="Compara um backup com o estado atual (não altera nada)",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def comparar(
        self, interaction: discord.Interaction, arquivo: str | None = None
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interaction, arquivo)
        if not backup:
            await self._enviar_card(
                interaction,
                titulo="🔍 Comparar",
                linhas=["Nenhum backup encontrado para comparar."],
                cor=discord.Color.orange(),
            )
            return

        diff = self.comparador.comparar(interaction.guild, backup)
        resumo = self.comparador.resumir(diff)
        linhas = [linha for linha in resumo.split("\n") if linha.strip()]
        await self._enviar_card(
            interaction,
            titulo=f"🔍 Comparação · {nome}",
            linhas=linhas or ["Sem diferenças."],
            cor=discord.Color.orange(),
            delay=30,
        )

    # ══════════════════════════════════════════════════════════════════════
    # /backup status  (antigo status.py)
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="status", description="Mostra o status do sistema de backup"
    )
    @apenas_administrador()
    async def status(self, interaction: discord.Interaction):
        arquivos = self.gerenciador.listar_backups(interaction.guild.id)
        ultimo = arquivos[0] if arquivos else "Nenhum"

        pasta_servidor = os.path.join(BACKUP_DIR, str(interaction.guild.id))
        tamanho_total = 0
        if os.path.isdir(pasta_servidor):
            tamanho_total = sum(
                os.path.getsize(os.path.join(pasta_servidor, nome))
                for nome in os.listdir(pasta_servidor)
            )

        estado_rejoin = (
            "ligado ✅" if rejoin_esta_ativo(interaction.guild.id) else "desligado ⏸️"
        )

        await responder_card(
            interaction,
            titulo="📊 Status do sistema de backup",
            linhas=[
                f"Backups salvos: **{len(arquivos)}**",
                f"Último backup: `{ultimo}`",
                f"Espaço usado: **{tamanho_total / 1024:.1f} KB**",
                f"Intervalo automático: a cada **{AUTO_BACKUP_INTERVAL_HOURS}h**",
                f"Máx. backups guardados: **{MAX_BACKUPS_PER_GUILD}**",
                f"Canal de logs: `#{LOG_CHANNEL_NAME}`",
                f"Rejoin automático: **{estado_rejoin}**",
            ],
            cor=discord.Color.blurple(),
            delay=25,
        )

    # ══════════════════════════════════════════════════════════════════════
    # /backup restaurar-cargos
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="restaurar-cargos",
        description="Restaura apenas os cargos a partir de um backup",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_cargos(
        self, interaction: discord.Interaction, arquivo: str | None = None
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interaction, arquivo)
        if not backup:
            await self._enviar_card(
                interaction,
                titulo="🛡️ Restaurar cargos",
                linhas=["Nenhum backup encontrado."],
                cor=discord.Color.orange(),
            )
            return

        previa = await self.restaurador.restaurar_cargos(
            interaction.guild, backup, dry_run=True
        )
        confirmou = await self._pedir_confirmacao(
            interaction,
            titulo=f"🛡️ Prévia · cargos (`{nome}`)",
            linhas=previa[:15] + ["", "Confirma aplicar estas alterações?"],
        )
        if not confirmou:
            await self._enviar_card(
                interaction,
                titulo="❌ Cancelado",
                linhas=["Restauração de cargos cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interaction.guild, str(interaction.user))
        relatorio = await self.restaurador.restaurar_cargos(
            interaction.guild, backup, dry_run=False
        )
        await self._enviar_card(
            interaction,
            titulo="✅ Cargos restaurados",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=discord.Color.green(),
            delay=25,
        )
        await self.logger.log(
            interaction.guild,
            "🛡️ Restauração de cargos",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interaction.user),
        )

    # ══════════════════════════════════════════════════════════════════════
    # /backup restaurar-canais
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="restaurar-canais",
        description="Restaura categorias e canais a partir de um backup",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_canais(
        self, interaction: discord.Interaction, arquivo: str | None = None
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interaction, arquivo)
        if not backup:
            await self._enviar_card(
                interaction,
                titulo="🛡️ Restaurar canais",
                linhas=["Nenhum backup encontrado."],
                cor=discord.Color.orange(),
            )
            return

        previa_cat = await self.restaurador.restaurar_categorias(
            interaction.guild, backup, dry_run=True
        )
        previa_ch = await self.restaurador.restaurar_canais(
            interaction.guild, backup, dry_run=True
        )
        previa = (previa_cat + previa_ch)[:15]
        confirmou = await self._pedir_confirmacao(
            interaction,
            titulo=f"🛡️ Prévia · canais (`{nome}`)",
            linhas=previa + ["", "Confirma aplicar estas alterações?"],
        )
        if not confirmou:
            await self._enviar_card(
                interaction,
                titulo="❌ Cancelado",
                linhas=["Restauração de canais cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interaction.guild, str(interaction.user))
        relatorio_cat = await self.restaurador.restaurar_categorias(
            interaction.guild, backup, dry_run=False
        )
        relatorio_ch = await self.restaurador.restaurar_canais(
            interaction.guild, backup, dry_run=False
        )
        relatorio = relatorio_cat + relatorio_ch
        await self._enviar_card(
            interaction,
            titulo="✅ Canais/categorias restaurados",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=discord.Color.green(),
            delay=25,
        )
        await self.logger.log(
            interaction.guild,
            "🛡️ Restauração de canais",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interaction.user),
        )

    # ══════════════════════════════════════════════════════════════════════
    # /backup restaurar-membros
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="restaurar-membros",
        description="Restaura cargos/apelidos de membros que ainda estão no servidor",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_membros(
        self, interaction: discord.Interaction, arquivo: str | None = None
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interaction, arquivo)
        if not backup:
            await self._enviar_card(
                interaction,
                titulo="🛡️ Restaurar membros",
                linhas=["Nenhum backup encontrado."],
                cor=discord.Color.orange(),
            )
            return

        previa = await self.restaurador.restaurar_membros(
            interaction.guild, backup, dry_run=True
        )
        confirmou = await self._pedir_confirmacao(
            interaction,
            titulo=f"🛡️ Prévia · membros (`{nome}`)",
            linhas=[
                "Quem saiu do servidor não pode ser re-adicionado pelo bot.",
                *previa[:14],
                "",
                "Confirma aplicar?",
            ],
        )
        if not confirmou:
            await self._enviar_card(
                interaction,
                titulo="❌ Cancelado",
                linhas=["Restauração de membros cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interaction.guild, str(interaction.user))
        relatorio = await self.restaurador.restaurar_membros(
            interaction.guild, backup, dry_run=False
        )
        await self._enviar_card(
            interaction,
            titulo="✅ Membros restaurados",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=discord.Color.green(),
            delay=25,
        )
        await self.logger.log(
            interaction.guild,
            "🛡️ Restauração de membros",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interaction.user),
        )

    # ══════════════════════════════════════════════════════════════════════
    # /backup restaurar-tudo
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="restaurar-tudo",
        description="Restaura cargos, categorias e canais (com backup de segurança)",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_tudo(
        self, interaction: discord.Interaction, arquivo: str | None = None
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interaction, arquivo)
        if not backup:
            await self._enviar_card(
                interaction,
                titulo="🛡️ Restaurar tudo",
                linhas=["Nenhum backup encontrado."],
                cor=discord.Color.orange(),
            )
            return

        confirmou = await self._pedir_confirmacao(
            interaction,
            titulo="⚠️ Restaurar estrutura completa",
            linhas=[
                f"Arquivo: `{nome}`",
                "Inclui cargos, categorias e canais.",
                "Um backup de segurança será criado antes.",
                "Membros NÃO entram aqui (use restaurar-membros).",
                "",
                "Tem certeza?",
            ],
        )
        if not confirmou:
            await self._enviar_card(
                interaction,
                titulo="❌ Cancelado",
                linhas=["Restauração completa cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interaction.guild, str(interaction.user))
        resultado = await self.restaurador.restaurar_tudo(
            interaction.guild, backup, dry_run=False
        )
        relatorio = (
            resultado["roles"] + resultado["categories"] + resultado["channels"]
        )
        await self._enviar_card(
            interaction,
            titulo="✅ Restauração completa",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=discord.Color.green(),
            delay=30,
        )
        await self.logger.log(
            interaction.guild,
            "🛡️ Restauração completa",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interaction.user),
        )

    # ══════════════════════════════════════════════════════════════════════
    # /backup rejoin
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="rejoin",
        description="Liga ou desliga o restore automático de cargos ao voltar",
    )
    @app_commands.describe(ativo="True = ligado, False = desligado")
    @apenas_administrador()
    async def rejoin(self, interaction: discord.Interaction, ativo: bool):
        definir_rejoin(interaction.guild.id, ativo)
        estado = "ligado ✅" if ativo else "desligado ⏸️"
        await responder_card(
            interaction,
            titulo="⚙️ Rejoin automático",
            linhas=[f"Estado alterado para **{estado}** neste servidor."],
            cor=discord.Color.blurple(),
            delay=12,
        )
        await self.logger.log(
            interaction.guild,
            "⚙️ Rejoin automático",
            f"Estado alterado para **{estado}**.",
            discord.Color.blurple(),
            autor=str(interaction.user),
        )

    # ══════════════════════════════════════════════════════════════════════
    # /backup sincronizar-membros
    # ══════════════════════════════════════════════════════════════════════

    @grupo_backup.command(
        name="sincronizar-membros",
        description="Grava no banco o snapshot de cargos de todos os membros atuais",
    )
    @apenas_administrador()
    async def sincronizar_membros(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        quantidade = await sincronizar_todos_os_membros(interaction.guild)
        estado_rejoin = (
            "ligado" if rejoin_esta_ativo(interaction.guild.id) else "desligado"
        )
        await self._enviar_card(
            interaction,
            titulo="✅ Snapshots sincronizados",
            linhas=[
                f"Membros salvos: **{quantidade}**",
                f"Rejoin automático: **{estado_rejoin}**",
            ],
            cor=discord.Color.green(),
            delay=15,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BackupCog(bot))
