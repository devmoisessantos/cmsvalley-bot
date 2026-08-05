from datetime import (
    datetime,
    timezone,
)

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from src.config import (
    CANAIS,
    CARGOS,
)
from src.database.connection import async_session
from src.database.models import (
    Recrutamento,
    Usuario,
)
from src.recrutamento.recrutamento_class import NovoRecrutamento
from src.recrutamento.recrutamento_logs import NovoRecrutamentoManualLog

# mapeia o value do Choice pro nome real da chave em CARGOS
CARGOS_FINAIS = {
    "ENFERMEIRO": "🔰・Enfermeiro (a)",
    "PARAMEDICO": "🚑・Paramédico",
}


class RecrutamentoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="recrutamento-manual",
        description="Registra manualmente um Recrutamento Realizado (uso em caso de bot fora do ar)",
    )
    @app_commands.describe(
        recrutador="Quem realizou o recrutamento (ex: [ REC ] Leo Valley | 1186)",
        membro="Membro recrutado (ex: guxta valley | 1763)",
        id_fivem="ID FiveM do membro (ex: 1763)",
        cargo="Cargo final do candidato",
    )
    @app_commands.choices(cargo=[
        app_commands.Choice(name="Enfermeiro", value="ENFERMEIRO"),
        app_commands.Choice(name="Paramédico", value="PARAMEDICO"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def recrutamento_manual(
        self,
        interaction: discord.Interaction,
        recrutador: discord.Member,
        membro: discord.Member,
        id_fivem: str,
        cargo: app_commands.Choice[str],
    ):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        cargo_role = guild.get_role(CARGOS[CARGOS_FINAIS[cargo.value]])
        if cargo_role is None:
            await interaction.followup.send(
                "❌ Cargo final não encontrado no servidor. Confira o CARGOS no config.py.",
                ephemeral=True,
            )
            return

        async with async_session() as session:
            # checa duplicidade de id_fivem, igual ao fluxo normal
            resultado_duplicidade = await session.execute(
                select(Recrutamento).where(
                    Recrutamento.id_fivem == id_fivem,
                    Recrutamento.discord_id_candidato != membro.id,
                    Recrutamento.status.in_(
                        ["ESTUDANDO", "EM_PROVA", "PROVA_LIBERADA", "APROVADO"]),
                )
            )
            conflito = resultado_duplicidade.scalar_one_or_none()
            if conflito is not None:
                await interaction.followup.send(
                    f"⚠️ O ID FiveM `{id_fivem}` já está associado a <@{conflito.discord_id_candidato}>. "
                    f"Confira antes de continuar.",
                    ephemeral=True,
                )
                return

            resultado = await session.execute(
                select(Usuario).where(Usuario.discord_id == membro.id)
            )
            usuario = resultado.scalar_one_or_none()

            if usuario is None:
                usuario = Usuario(
                    discord_id=membro.id,
                    nickname_atual=membro.display_name,
                    id_fivem=id_fivem,
                )
                session.add(usuario)
            else:
                usuario.id_fivem = id_fivem
                usuario.nickname_atual = membro.display_name

            usuario.status = "APROVADO"
            usuario.ja_foi_aprovado = True

            novo_recrutamento = Recrutamento(
                discord_id_candidato=membro.id,
                discord_id_recrutador=recrutador.id,
                id_fivem=id_fivem,
                status="APROVADO",
                cargo_final=cargo.value,
                data_fim=datetime.now(timezone.utc),
            )
            session.add(novo_recrutamento)
            await session.commit()

        await membro.add_roles(
            cargo_role, reason=f"Recrutamento manual registrado por {interaction.user}"
        )

        await interaction.followup.send(
            f"✅ Recrutamento manual registrado para {membro.mention} ({cargo.name}).",
            ephemeral=True,
        )

        try:
            canal_recrutamento = guild.get_channel(CANAIS["RECRUTAMENTOS"])
            if canal_recrutamento:
                await canal_recrutamento.send(view=NovoRecrutamento(
                    candidato=membro, recrutador=recrutador,
                    cargo_role=cargo_role, id_fivem=id_fivem, guild=guild,
                ))

            canal_log = guild.get_channel(CANAIS["LOG_RECRUTAMENTOS"])
            if canal_log:
                await canal_log.send(view=NovoRecrutamentoManualLog(
                    candidato=membro, recrutador=recrutador, executor=interaction.user,
                    cargo_role=cargo_role, id_fivem=id_fivem, guild=guild,
                ))
        except Exception as erro:
            canal_erros = guild.get_channel(CANAIS["LOG_ERROS"])
            if canal_erros:
                await canal_erros.send(f"⚠️ Falha ao registrar log de recrutamento manual: `{erro}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(RecrutamentoCog(bot))
