# src/backup/backup_cogs.py
"""
Comandos e listeners do sistema de backup.

Grupo único: /backup

  criar · listar · exportar · deletar · comparar · status
  restaurar-cargos · restaurar-canais · restaurar-membros · restaurar-tudo
  rejoin · sincronizar-membros · sincronizar-usuarios

Respostas ao usuário passam por src.utils.mensagens.
Logs de canal passam por BackupLogger (Components V2).

Apenas Administradores.
"""

from __future__ import annotations

import asyncio
import os

import discord
from discord import app_commands
from discord.ext import (
    commands,
    tasks,
)

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
from src.services.sincronizar_usuarios import (
    garantir_usuario_basico,
    sincronizar_usuarios_do_servidor,
)
from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    enviar_card,
    excluir_mensagem,
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

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    async def _carregar_backup_alvo(
        self,
        interacao: discord.Interaction,
        nome_arquivo: str | None,
    ) -> tuple[dict | None, str | None]:
        """Carrega o backup pedido ou o mais recente do servidor."""
        arquivo = nome_arquivo or self.gerenciador.nome_backup_mais_recente(
            interacao.guild.id
        )
        if not arquivo:
            return None, None

        backup = self.gerenciador.carregar_backup(interacao.guild.id, arquivo)
        return backup, arquivo

    async def _pedir_confirmacao(
        self,
        interacao: discord.Interaction,
        titulo: str,
        linhas: list[str],
    ) -> bool:
        """
        Mostra botões Confirmar/Cancelar e espera a escolha.

        A mensagem não some enquanto a pessoa não clicar.
        """
        view_dos_botoes = ConfirmView(
            autor_id=interacao.user.id,
            timeout=CONFIRMATION_TIMEOUT,
        )
        corpo = "\n".join(f"`•` {linha}" for linha in linhas if linha is not None)

        mensagem = await interacao.followup.send(
            content=f"**{titulo}**\n{corpo}",
            view=view_dos_botoes,
            ephemeral=True,
        )
        await view_dos_botoes.wait()
        asyncio.create_task(excluir_mensagem(mensagem, delay=3))
        return bool(view_dos_botoes.value)

    def _backup_de_seguranca(self, guilda: discord.Guild, autor: str) -> None:
        """Cria um backup automático antes de restaurar algo."""
        backup = self.gerenciador.criar_backup(
            guilda,
            criado_por=f"Auto (antes de restore por {autor})",
        )
        self.gerenciador.salvar_backup(backup)

    # ------------------------------------------------------------------
    # Backup automático
    # ------------------------------------------------------------------

    @tasks.loop(hours=24)
    async def tarefa_backup_automatico(self):
        for guilda in self.bot.guilds:
            try:
                backup = self.gerenciador.criar_backup(
                    guilda,
                    criado_por="Sistema (automático)",
                )
                self.gerenciador.salvar_backup(backup)
                await self.logger.log(
                    guilda,
                    "🔄 Backup automático concluído",
                    (
                        f"Cargos: {len(backup['roles'])} | "
                        f"Canais: {len(backup['channels'])} | "
                        f"Categorias: {len(backup['categories'])} | "
                        f"Membros: {len(backup['members'])}"
                    ),
                    COR_SUCESSO,
                )
            except Exception as erro:
                print(f"Erro no backup automático de {guilda.name}: {erro}")

    @tarefa_backup_automatico.before_loop
    async def _antes_do_backup_automatico(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Listeners — snapshot vivo
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(
        self,
        antes: discord.Member,
        depois: discord.Member,
    ):
        cargos_antes = {cargo.id for cargo in antes.roles}
        cargos_depois = {cargo.id for cargo in depois.roles}
        cargos_mudaram = cargos_antes != cargos_depois
        nick_mudou = antes.nick != depois.nick

        if cargos_mudaram or nick_mudou:
            await salvar_snapshot_membro(depois)

    @commands.Cog.listener()
    async def on_member_remove(self, membro: discord.Member):
        await salvar_snapshot_membro(membro)

    @commands.Cog.listener()
    async def on_member_join(self, membro: discord.Member):
        if membro.bot:
            return

        # Garante linha básica em usuarios (base das buscas do bot)
        try:
            await garantir_usuario_basico(membro)
        except Exception as erro:
            print(f"Aviso ao criar usuario no join: {erro}")

        relatorio = await restaurar_cargos_no_rejoin(membro)
        if relatorio:
            await self.logger.log(
                membro.guild,
                "🔁 Rejoin — restore de cargos",
                "\n".join(relatorio),
                discord.Color.teal(),
                autor="Sistema",
            )

    # ------------------------------------------------------------------
    # /backup criar
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="criar",
        description="Cria um backup manual do servidor agora",
    )
    @apenas_administrador()
    async def criar(self, interacao: discord.Interaction):
        await interacao.response.defer(thinking=True, ephemeral=True)

        backup = self.gerenciador.criar_backup(
            interacao.guild,
            criado_por=str(interacao.user),
        )
        caminho = self.gerenciador.salvar_backup(backup)
        nome_arquivo = caminho.split("/")[-1]

        try:
            await sincronizar_todos_os_membros(interacao.guild)
        except Exception as erro:
            print(f"Aviso ao sincronizar snapshots: {erro}")

        await enviar_card(
            interacao,
            titulo="✅ Backup criado",
            linhas=[
                f"Arquivo: `{nome_arquivo}`",
                f"Cargos: **{len(backup['roles'])}**",
                f"Canais: **{len(backup['channels'])}**",
                f"Categorias: **{len(backup['categories'])}**",
                f"Membros: **{len(backup['members'])}**",
            ],
            cor=COR_SUCESSO,
            delay=20,
        )
        await self.logger.log(
            interacao.guild,
            "💾 Backup manual criado",
            f"Arquivo `{nome_arquivo}` via /backup criar.",
            discord.Color.blue(),
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup listar
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="listar",
        description="Lista os backups disponíveis neste servidor",
    )
    @apenas_administrador()
    async def listar(self, interacao: discord.Interaction):
        arquivos = self.gerenciador.listar_backups(interacao.guild.id)

        if not arquivos:
            await enviar_card(
                interacao,
                titulo="📂 Backups",
                linhas=["Nenhum backup encontrado para este servidor."],
                cor=COR_AVISO,
                delay=12,
            )
            return

        linhas = [
            f"`{indice + 1}.` {nome}" for indice, nome in enumerate(arquivos[:20])
        ]
        await enviar_card(
            interacao,
            titulo="📂 Backups disponíveis",
            linhas=linhas,
            cor=COR_INFO,
            delay=25,
        )

    # ------------------------------------------------------------------
    # /backup exportar
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="exportar",
        description="Baixa o backup mais recente (ou um específico) como arquivo",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def exportar(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        nome = arquivo or self.gerenciador.nome_backup_mais_recente(interacao.guild.id)
        if not nome:
            await enviar_card(
                interacao,
                titulo="📥 Exportar",
                linhas=["Nenhum backup encontrado."],
                cor=COR_AVISO,
                delay=10,
            )
            return

        caminho = self.gerenciador.caminho_completo(interacao.guild.id, nome)

        # Exceção legítima: precisa anexar arquivo (não cabe só em card).
        try:
            await interacao.response.send_message(
                content=f"📥 Backup: `{nome}`",
                file=discord.File(caminho),
                ephemeral=True,
            )
            mensagem = await interacao.original_response()
            asyncio.create_task(excluir_mensagem(mensagem, delay=60))
        except FileNotFoundError:
            await enviar_card(
                interacao,
                titulo="📥 Exportar",
                linhas=["Arquivo não encontrado no disco."],
                cor=COR_ERRO,
                delay=10,
            )

    # ------------------------------------------------------------------
    # /backup deletar
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="deletar",
        description="Apaga um backup específico pelo nome do arquivo",
    )
    @app_commands.describe(arquivo="Nome exato do arquivo JSON")
    @apenas_administrador()
    async def deletar(self, interacao: discord.Interaction, arquivo: str):
        sucesso = self.gerenciador.deletar_backup(interacao.guild.id, arquivo)

        if sucesso:
            await enviar_card(
                interacao,
                titulo="🗑️ Backup deletado",
                linhas=[f"Arquivo `{arquivo}` removido com sucesso."],
                cor=COR_SUCESSO,
                delay=12,
            )
        else:
            await enviar_card(
                interacao,
                titulo="🗑️ Deletar backup",
                linhas=["Arquivo não encontrado."],
                cor=COR_ERRO,
                delay=10,
            )

    # ------------------------------------------------------------------
    # /backup comparar
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="comparar",
        description="Compara um backup com o estado atual (não altera nada)",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def comparar(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        await interacao.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interacao, arquivo)

        if not backup:
            await enviar_card(
                interacao,
                titulo="🔍 Comparar",
                linhas=["Nenhum backup encontrado para comparar."],
                cor=COR_AVISO,
            )
            return

        diff = self.comparador.comparar(interacao.guild, backup)
        resumo = self.comparador.resumir(diff)
        linhas = [linha for linha in resumo.split("\n") if linha.strip()]

        await enviar_card(
            interacao,
            titulo=f"🔍 Comparação · {nome}",
            linhas=linhas or ["Sem diferenças."],
            cor=COR_AVISO,
            delay=30,
        )

    # ------------------------------------------------------------------
    # /backup status
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="status",
        description="Mostra o status do sistema de backup",
    )
    @apenas_administrador()
    async def status(self, interacao: discord.Interaction):
        arquivos = self.gerenciador.listar_backups(interacao.guild.id)
        ultimo = arquivos[0] if arquivos else "Nenhum"

        pasta_do_servidor = os.path.join(BACKUP_DIR, str(interacao.guild.id))
        tamanho_total = 0
        if os.path.isdir(pasta_do_servidor):
            tamanho_total = sum(
                os.path.getsize(os.path.join(pasta_do_servidor, nome))
                for nome in os.listdir(pasta_do_servidor)
            )

        estado_rejoin = (
            "ligado ✅" if rejoin_esta_ativo(interacao.guild.id) else "desligado ⏸️"
        )

        await enviar_card(
            interacao,
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
            cor=COR_INFO,
            delay=25,
        )

    # ------------------------------------------------------------------
    # /backup restaurar-cargos
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="restaurar-cargos",
        description="Restaura apenas os cargos a partir de um backup",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_cargos(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        await interacao.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interacao, arquivo)

        if not backup:
            await enviar_card(
                interacao,
                titulo="🛡️ Restaurar cargos",
                linhas=["Nenhum backup encontrado."],
                cor=COR_AVISO,
            )
            return

        previa = await self.restaurador.restaurar_cargos(
            interacao.guild, backup, dry_run=True
        )
        confirmou = await self._pedir_confirmacao(
            interacao,
            titulo=f"🛡️ Prévia · cargos (`{nome}`)",
            linhas=previa[:15] + ["", "Confirma aplicar estas alterações?"],
        )
        if not confirmou:
            await enviar_card(
                interacao,
                titulo="❌ Cancelado",
                linhas=["Restauração de cargos cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interacao.guild, str(interacao.user))
        relatorio = await self.restaurador.restaurar_cargos(
            interacao.guild, backup, dry_run=False
        )
        await enviar_card(
            interacao,
            titulo="✅ Cargos restaurados",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=COR_SUCESSO,
            delay=25,
        )
        await self.logger.log(
            interacao.guild,
            "🛡️ Restauração de cargos",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup restaurar-canais
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="restaurar-canais",
        description="Restaura categorias e canais a partir de um backup",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_canais(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        await interacao.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interacao, arquivo)

        if not backup:
            await enviar_card(
                interacao,
                titulo="🛡️ Restaurar canais",
                linhas=["Nenhum backup encontrado."],
                cor=COR_AVISO,
            )
            return

        previa_categorias = await self.restaurador.restaurar_categorias(
            interacao.guild, backup, dry_run=True
        )
        previa_canais = await self.restaurador.restaurar_canais(
            interacao.guild, backup, dry_run=True
        )
        previa = (previa_categorias + previa_canais)[:15]

        confirmou = await self._pedir_confirmacao(
            interacao,
            titulo=f"🛡️ Prévia · canais (`{nome}`)",
            linhas=previa + ["", "Confirma aplicar estas alterações?"],
        )
        if not confirmou:
            await enviar_card(
                interacao,
                titulo="❌ Cancelado",
                linhas=["Restauração de canais cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interacao.guild, str(interacao.user))
        relatorio_categorias = await self.restaurador.restaurar_categorias(
            interacao.guild, backup, dry_run=False
        )
        relatorio_canais = await self.restaurador.restaurar_canais(
            interacao.guild, backup, dry_run=False
        )
        relatorio = relatorio_categorias + relatorio_canais

        await enviar_card(
            interacao,
            titulo="✅ Canais/categorias restaurados",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=COR_SUCESSO,
            delay=25,
        )
        await self.logger.log(
            interacao.guild,
            "🛡️ Restauração de canais",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup restaurar-membros
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="restaurar-membros",
        description="Restaura cargos/apelidos de membros que ainda estão no servidor",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_membros(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        await interacao.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interacao, arquivo)

        if not backup:
            await enviar_card(
                interacao,
                titulo="🛡️ Restaurar membros",
                linhas=["Nenhum backup encontrado."],
                cor=COR_AVISO,
            )
            return

        previa = await self.restaurador.restaurar_membros(
            interacao.guild, backup, dry_run=True
        )
        confirmou = await self._pedir_confirmacao(
            interacao,
            titulo=f"🛡️ Prévia · membros (`{nome}`)",
            linhas=[
                "Quem saiu do servidor não pode ser re-adicionado pelo bot.",
                *previa[:14],
                "",
                "Confirma aplicar?",
            ],
        )
        if not confirmou:
            await enviar_card(
                interacao,
                titulo="❌ Cancelado",
                linhas=["Restauração de membros cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interacao.guild, str(interacao.user))
        relatorio = await self.restaurador.restaurar_membros(
            interacao.guild, backup, dry_run=False
        )
        await enviar_card(
            interacao,
            titulo="✅ Membros restaurados",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=COR_SUCESSO,
            delay=25,
        )
        await self.logger.log(
            interacao.guild,
            "🛡️ Restauração de membros",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup restaurar-tudo
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="restaurar-tudo",
        description="Restaura cargos, categorias e canais (com backup de segurança)",
    )
    @app_commands.describe(arquivo="Nome do arquivo (deixe vazio para o mais recente)")
    @apenas_administrador()
    async def restaurar_tudo(
        self,
        interacao: discord.Interaction,
        arquivo: str | None = None,
    ):
        await interacao.response.defer(thinking=True, ephemeral=True)
        backup, nome = await self._carregar_backup_alvo(interacao, arquivo)

        if not backup:
            await enviar_card(
                interacao,
                titulo="🛡️ Restaurar tudo",
                linhas=["Nenhum backup encontrado."],
                cor=COR_AVISO,
            )
            return

        confirmou = await self._pedir_confirmacao(
            interacao,
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
            await enviar_card(
                interacao,
                titulo="❌ Cancelado",
                linhas=["Restauração completa cancelada."],
                cor=discord.Color.dark_grey(),
                delay=8,
            )
            return

        self._backup_de_seguranca(interacao.guild, str(interacao.user))
        resultado = await self.restaurador.restaurar_tudo(
            interacao.guild, backup, dry_run=False
        )
        relatorio = resultado["roles"] + resultado["categories"] + resultado["channels"]
        await enviar_card(
            interacao,
            titulo="✅ Restauração completa",
            linhas=relatorio[:20] or ["Nenhuma alteração aplicada."],
            cor=COR_SUCESSO,
            delay=30,
        )
        await self.logger.log(
            interacao.guild,
            "🛡️ Restauração completa",
            "\n".join(relatorio),
            discord.Color.gold(),
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup rejoin
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="rejoin",
        description="Liga ou desliga o restore automático de cargos ao voltar",
    )
    @app_commands.describe(ativo="True = ligado, False = desligado")
    @apenas_administrador()
    async def rejoin(self, interacao: discord.Interaction, ativo: bool):
        definir_rejoin(interacao.guild.id, ativo)
        estado = "ligado ✅" if ativo else "desligado ⏸️"

        await enviar_card(
            interacao,
            titulo="⚙️ Rejoin automático",
            linhas=[f"Estado alterado para **{estado}** neste servidor."],
            cor=COR_INFO,
            delay=12,
        )
        await self.logger.log(
            interacao.guild,
            "⚙️ Rejoin automático",
            f"Estado alterado para **{estado}**.",
            COR_INFO,
            autor=str(interacao.user),
        )

    # ------------------------------------------------------------------
    # /backup sincronizar-membros
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="sincronizar-membros",
        description="Grava no banco o snapshot de cargos de todos os membros atuais",
    )
    @apenas_administrador()
    async def sincronizar_membros(self, interacao: discord.Interaction):
        await interacao.response.defer(thinking=True, ephemeral=True)
        quantidade = await sincronizar_todos_os_membros(interacao.guild)
        estado_rejoin = (
            "ligado" if rejoin_esta_ativo(interacao.guild.id) else "desligado"
        )

        await enviar_card(
            interacao,
            titulo="✅ Snapshots sincronizados",
            linhas=[
                f"Membros salvos: **{quantidade}**",
                f"Rejoin automático: **{estado_rejoin}**",
            ],
            cor=COR_SUCESSO,
            delay=15,
        )

    # ------------------------------------------------------------------
    # /backup sincronizar-usuarios
    # ------------------------------------------------------------------

    @grupo_backup.command(
        name="sincronizar-usuarios",
        description="Alimenta a tabela usuarios com os membros atuais do servidor",
    )
    @apenas_administrador()
    async def sincronizar_usuarios(self, interacao: discord.Interaction):
        """Varre o Discord e preenche lacunas em usuarios (status, nick, id_fivem)."""
        await interacao.response.defer(thinking=True, ephemeral=True)

        resultado = await sincronizar_usuarios_do_servidor(interacao.guild)
        linhas = resultado.linhas_resumo()

        if resultado.erros:
            linhas.append(f"Avisos/erros: **{len(resultado.erros)}** (ver console)")
            for trecho in resultado.erros[:5]:
                print(f"[sincronizar-usuarios] {trecho}")

        await enviar_card(
            interacao,
            titulo="✅ Tabela usuarios sincronizada",
            linhas=linhas,
            cor=COR_SUCESSO,
            delay=40,
        )
        await self.logger.log(
            interacao.guild,
            "🗄️ Sincronização de usuarios",
            " | ".join(linhas),
            COR_SUCESSO,
            autor=str(interacao.user),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BackupCog(bot))
