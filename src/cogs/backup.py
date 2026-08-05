import datetime

import discord
from discord import app_commands
from discord.ext import (
    commands,
    tasks,
)

from src.config import (
    AUTO_BACKUP_INTERVAL_HOURS,
    BACKUP_DIR,
    LOG_CHANNEL_NAME,
)
from src.core.backup_manager import BackupManager
from src.core.logger import BackupLogger
from src.utils.permissions import is_authorized


class BackupCog(commands.Cog):
    backup_group = app_commands.Group(
        name="backup", description="Gerenciar backups do servidor"
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bm = BackupManager()
        self.logger = BackupLogger(LOG_CHANNEL_NAME)
        self.auto_backup_task.change_interval(hours=AUTO_BACKUP_INTERVAL_HOURS)
        self.auto_backup_task.start()

    def cog_unload(self):
        self.auto_backup_task.cancel()

    @tasks.loop(hours=24)
    async def auto_backup_task(self):
        for guild in self.bot.guilds:
            try:
                backup = self.bm.create_backup(guild, created_by="Sistema (automático)")
                self.bm.save_backup(backup)
                await self.logger.log(
                    guild,
                    "🔄 Backup automático concluído",
                    f"Cargos: {len(backup['roles'])} | Canais: {len(backup['channels'])} | "
                    f"Categorias: {len(backup['categories'])} | Membros: {len(backup['members'])}",
                    discord.Color.green(),
                )
            except Exception as e:
                print(f"Erro no backup automático de {guild.name}: {e}")

    @auto_backup_task.before_loop
    async def before_auto_backup(self):
        await self.bot.wait_until_ready()

    @backup_group.command(
        name="criar", description="Cria um backup manual do servidor agora"
    )
    @is_authorized()
    async def criar(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        backup = self.bm.create_backup(
            interaction.guild, created_by=str(interaction.user)
        )
        path = self.bm.save_backup(backup)

        embed = discord.Embed(
            title="✅ Backup criado com sucesso",
            color=discord.Color.green(),
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="Cargos", value=len(backup["roles"]))
        embed.add_field(name="Canais", value=len(backup["channels"]))
        embed.add_field(name="Categorias", value=len(backup["categories"]))
        embed.add_field(name="Membros", value=len(backup["members"]))
        embed.set_footer(text=f"Arquivo: {path.split('/')[-1]}")

        await interaction.followup.send(embed=embed)
        await self.logger.log(
            interaction.guild,
            "💾 Backup manual criado",
            "Backup criado manualmente via comando /backup criar.",
            discord.Color.blue(),
            author=str(interaction.user),
        )

    @backup_group.command(name="listar", description="Lista os backups disponíveis")
    @is_authorized()
    async def listar(self, interaction: discord.Interaction):
        files = self.bm.list_backups(interaction.guild.id)
        if not files:
            await interaction.response.send_message(
                "Nenhum backup encontrado para este servidor.", ephemeral=True
            )
            return

        description = "\n".join(f"`{i + 1}.` {f}" for i, f in enumerate(files[:20]))
        embed = discord.Embed(
            title="📂 Backups disponíveis",
            description=description,
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @backup_group.command(
        name="exportar",
        description="Baixa o backup mais recente (ou um específico) como arquivo",
    )
    @is_authorized()
    async def exportar(self, interaction: discord.Interaction, arquivo: str = None):
        filename = arquivo or self.bm.latest_backup_filename(interaction.guild.id)
        if not filename:
            await interaction.response.send_message(
                "Nenhum backup encontrado.", ephemeral=True
            )
            return
        path = f"{BACKUP_DIR}/{interaction.guild.id}/{filename}"
        try:
            await interaction.response.send_message(
                content=f"📥 Backup: `{filename}`",
                file=discord.File(path),
                ephemeral=True,
            )
        except FileNotFoundError:
            await interaction.response.send_message(
                "Arquivo não encontrado.", ephemeral=True
            )

    @backup_group.command(
        name="deletar", description="Deleta um backup específico pelo nome do arquivo"
    )
    @is_authorized()
    async def deletar(self, interaction: discord.Interaction, arquivo: str):
        success = self.bm.delete_backup(interaction.guild.id, arquivo)
        if success:
            await interaction.response.send_message(
                f"🗑️ Backup `{arquivo}` deletado.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Arquivo não encontrado.", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(BackupCog(bot))
