import discord
from datetime import datetime, timezone

from sqlalchemy import select

from src.config import CARGOS, CANAIS

from src.database.connection import async_session
from src.database.models import Usuario, Recrutamento
from src.panels.log_recrutamento import NovoRecrutamentoLog
from src.utils.logger import log_mudanca_cargo

async def validar_e_iniciar_recrutamento(
    interaction: discord.Interaction,
    candidato: discord.Member,
    recrutador: discord.Member,
    id_fivem: str,
):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    cargo_visitante = guild.get_role(CARGOS["Visitantes"])
    cargo_estudante = guild.get_role(CARGOS["ESTUDANTE"])
    cargo_hp = guild.get_role(CARGOS["HP S・Valley"])
    cargo_aprovado = guild.get_role(CARGOS["Aprovado"])

    # Validações do fluxo
    if cargo_visitante not in candidato.roles:
        await interaction.followup.send(
            "❌ Este membro não possui o cargo Visitantes (não concluiu a WhiteList).",
            ephemeral=True,
        )
        return

    if cargo_hp in candidato.roles or cargo_aprovado in candidato.roles:
        await interaction.followup.send(
            "❌ Este membro já foi aprovado anteriormente.", ephemeral=True
        )
        return

    async with async_session() as session:
        resultado = await session.execute(
            select(Usuario).where(Usuario.discord_id == candidato.id)
        )
        usuario = resultado.scalar_one_or_none()

        if usuario is None:
            usuario = Usuario(discord_id=candidato.id, nickname_atual=candidato.display_name)
            session.add(usuario)

        # Checa cooldown de 24h após reprovação
        if usuario.data_ultima_reprovacao:
            tempo_passado = datetime.now(timezone.utc) - usuario.data_ultima_reprovacao  # antes: datetime.now(timezone.utc)
            if tempo_passado.total_seconds() < 24 * 3600:
        
                horas_restantes = 24 - (tempo_passado.total_seconds() / 3600)
                await interaction.followup.send(
                    f"❌ Este candidato precisa aguardar mais {horas_restantes:.1f}h para tentar novamente.",
                    ephemeral=True,
                )
                return

        # Checa se já está em recrutamento ativo
        resultado_recrutamento = await session.execute(
            select(Recrutamento).where(
                Recrutamento.discord_id_candidato == candidato.id,
                Recrutamento.status.in_(["ESTUDANDO", "EM_PROVA"]),
            )
        )
        recrutamento_ativo = resultado_recrutamento.scalar_one_or_none()

        if recrutamento_ativo is not None:
            await interaction.followup.send(
                "❌ Este candidato já está em processo de recrutamento com outro recrutador.",
                ephemeral=True,
            )
            return

        # Tudo certo: cria o recrutamento
        novo_recrutamento = Recrutamento(
            discord_id_candidato=candidato.id,
            discord_id_recrutador=recrutador.id,
            id_fivem=id_fivem,
            status="ESTUDANDO",
        )
        session.add(novo_recrutamento)
        usuario.status = "ESTUDANTE"
        await session.commit()

    # Aplica o cargo
    await candidato.add_roles(cargo_estudante, reason=f"Recrutamento iniciado por {recrutador}")

    # 👇 Responde AO USUÁRIO primeiro — isso garante que ele vê o sucesso mesmo se o log falhar depois
    await interaction.followup.send(
        f"✅ Recrutamento iniciado para {candidato.mention}. Cargo Estudante aplicado.",
        ephemeral=True,
    )


    # 👇 Logs vêm DEPOIS, protegidos por try/except — falha aqui não derruba a experiência do recrutador
    try:
        await log_mudanca_cargo(
            guild, candidato=candidato, executor=recrutador,
            cargos_adicionados=[cargo_estudante.mention],
        )

        canal_log_inicio = guild.get_channel(CANAIS["LOG_RECRUTAMENTOS"])
        if canal_log_inicio:
            await canal_log_inicio.send(view=NovoRecrutamentoLog(
                candidato=candidato, recrutador=recrutador,
                cargo_role=cargo_estudante, id_fivem=id_fivem, guild=guild,
            ))
    except Exception as erro:
        canal_erros = guild.get_channel(CANAIS["LOG_ERROS"])
        if canal_erros:
            await canal_erros.send(f"⚠️ Falha ao registrar log de início de recrutamento: `{erro}`")