import discord
from sqlalchemy import select

from src.config import CARGOS
from src.database.connection import async_session
from src.database.models import Recrutamento
from src.utils.logger import log_mudanca_cargo
from src.utils.error_handling import LoggingViewMixin
from src.panels.aprovacao_panel import possui_cargo_recrutador_ou_superior


async def liberar_avaliacao_click(interaction: discord.Interaction):
    if not possui_cargo_recrutador_ou_superior(interaction.user):
        await interaction.response.send_message(
            "❌ Você não possui permissão para liberar avaliações.", ephemeral=True
        )
        return
    
    await interaction.response.defer(ephemeral=True)

    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento).where(
                Recrutamento.discord_id_recrutador == interaction.user.id,
                Recrutamento.status == "ESTUDANDO",
            )
        )
        recrutamentos_ativos = resultado.scalars().all()

    if not recrutamentos_ativos:
        await interaction.followup.send(
            "❌ Você não possui nenhum candidato aguardando liberação de prova no momento.",
            ephemeral=True,
        )
        return

    if len(recrutamentos_ativos) == 1:
        await _liberar_para_recrutamento(interaction, recrutamentos_ativos[0])
        return

    # Mais de um candidato ativo: mostra um menu pra escolher qual liberar
    view = SelecionarCandidatoLiberacaoView(recrutamentos_ativos, interaction.guild)
    await interaction.followup.send(
        "Você possui mais de um candidato em fase de estudo. Selecione qual deseja liberar para a prova:",
        view=view, ephemeral=True,
    )


async def _liberar_para_recrutamento(interaction: discord.Interaction, recrutamento: Recrutamento):
    guild = interaction.guild
    candidato = guild.get_member(recrutamento.discord_id_candidato)

    if candidato is None:
        await interaction.followup.send("❌ Candidato não encontrado no servidor.", ephemeral=True)
        return

    cargo_estudante = guild.get_role(CARGOS["ESTUDANTE"])
    cargo_prova = guild.get_role(CARGOS["PROVA"])

    await candidato.remove_roles(cargo_estudante, reason=f"Prova liberada por {interaction.user}")
    await candidato.add_roles(cargo_prova, reason=f"Prova liberada por {interaction.user}")

    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento).where(Recrutamento.id == recrutamento.id)
        )
        registro = resultado.scalar_one()
        registro.status = "PROVA_LIBERADA"
        await session.commit()

    await log_mudanca_cargo(
        guild, candidato=candidato, executor=interaction.user,
        cargos_removidos=[cargo_estudante.mention],
        cargos_adicionados=[cargo_prova.mention],
    )

    mensagem = f"✅ Prova liberada para {candidato.mention}. Ele já pode iniciar a avaliação."
    await interaction.followup.send(mensagem, ephemeral=True)


class SelecionarCandidatoLiberacaoView(LoggingViewMixin, discord.ui.View):
    def __init__(self, recrutamentos: list[Recrutamento], guild: discord.Guild):
        super().__init__(timeout=60)
        self.recrutamentos_por_id = {r.id: r for r in recrutamentos}

        options = []
        for r in recrutamentos:
            membro = guild.get_member(r.discord_id_candidato)
            nome = membro.display_name if membro else f"ID {r.discord_id_candidato}"
            options.append(discord.SelectOption(label=nome, value=str(r.id)))

        select = discord.ui.Select(placeholder="Selecione o candidato", options=options)
        select.callback = self.selecionar
        self.add_item(select)

    async def selecionar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        recrutamento_id = int(interaction.data["values"][0])
        recrutamento = self.recrutamentos_por_id[recrutamento_id]
        await _liberar_para_recrutamento(interaction, recrutamento)