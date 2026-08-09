"""
Comandos de barra do domínio recrutamento.

- /recrutamento-manual — registro offline (admin)
- /recrutamento cancelar | status — destravar processos falhos
"""

from __future__ import annotations

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
from src.recrutamento.recrutamento_service import (
    STATUS_RECRUTAMENTO_ATIVOS,
    buscar_recrutamento_ativo,
    cancelar_recrutamento_ativo,
)
from src.utils.mensagens import (
    responder_erro,
    responder_info,
    responder_sucesso,
)
from src.utils.permissions import apenas_administrador

# mapeia o value do Choice pro nome real da chave em CARGOS
CARGOS_FINAIS = {
    "ENFERMEIRO": "🔰・Enfermeiro (a)",
    "PARAMEDICO": "🚑・Paramédico",
}


class RecrutamentoCog(commands.Cog):
    """Comandos de recrutamento (manual + administração de processos)."""

    grupo_recrutamento = app_commands.Group(
        name="recrutamento",
        description="Administração de processos de recrutamento",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Admin — destravar / consultar
    # ------------------------------------------------------------------

    @grupo_recrutamento.command(
        name="status",
        description="Consulta o recrutamento ativo (ou o último) de um membro",
    )
    @app_commands.describe(membro="Candidato a consultar")
    @apenas_administrador()
    async def recrutamento_status(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
    ):
        ativo = await buscar_recrutamento_ativo(membro.id)
        async with async_session() as session:
            resultado = await session.execute(
                select(Recrutamento)
                .where(Recrutamento.discord_id_candidato == membro.id)
                .order_by(Recrutamento.id.desc())
                .limit(5)
            )
            ultimos = list(resultado.scalars().all())

        if not ultimos:
            await responder_info(
                interacao,
                titulo="Sem recrutamento",
                linhas=[f"{membro.mention} nunca teve registro em `recrutamentos`."],
            )
            return

        linhas = [
            "**Ativo agora:** "
            + (
                f"`#{ativo.id}` · `{ativo.status}` · recrutador `<@{ativo.discord_id_recrutador}>`"
                if ativo
                else "_nenhum_"
            ),
            "",
            "**Últimos registros:**",
        ]
        for rec in ultimos:
            linhas.append(
                f"`#{rec.id}` · `{rec.status}` · fivem `{rec.id_fivem or '—'}` · "
                f"formulario `{'aberto' if rec.formulario_aberto else 'fechado'}` · "
                f"`{rec.data_inicio}`"
            )
        await responder_info(
            interacao,
            titulo=f"Recrutamento — {membro.display_name}",
            linhas=linhas,
            delay=45,
        )

    @grupo_recrutamento.command(
        name="cancelar",
        description="Cancela recrutamento ativo (destrava início de um novo)",
    )
    @app_commands.describe(
        membro="Candidato com processo travado",
        motivo="Motivo curto (opcional)",
    )
    @apenas_administrador()
    async def recrutamento_cancelar(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
        motivo: str = "Cancelado por administrador",
    ):
        cancelado = await cancelar_recrutamento_ativo(
            membro.id,
            motivo=motivo[:80],
            guild=interacao.guild,
            executor=interacao.user,
            apagar_respostas=True,
        )
        if cancelado is None:
            await responder_erro(
                interacao,
                titulo="Nada ativo",
                linhas=[
                    f"{membro.mention} não tem recrutamento em "
                    f"`{'` / `'.join(STATUS_RECRUTAMENTO_ATIVOS)}`.",
                    "Use `/recrutamento status` para ver o histórico.",
                ],
            )
            return

        await responder_sucesso(
            interacao,
            titulo="Recrutamento cancelado",
            linhas=[
                f"**Registro:** `#{cancelado.id}` → `CANCELADO`",
                f"**Candidato:** {membro.mention}",
                f"**Motivo:** {motivo[:80]}",
                "Cargos **PROVA** / **ESTUDANTE** removidos (Visitantes mantido).",
                "Já é possível iniciar um novo recrutamento pelo painel.",
            ],
        )

    @grupo_recrutamento.command(
        name="limpar_formulario",
        description="Desmarca formulario_aberto (prova presa) sem cancelar o processo",
    )
    @app_commands.describe(membro="Candidato com formulário preso")
    @apenas_administrador()
    async def recrutamento_limpar_formulario(
        self,
        interacao: discord.Interaction,
        membro: discord.Member,
    ):
        async with async_session() as session:
            resultado = await session.execute(
                select(Recrutamento)
                .where(
                    Recrutamento.discord_id_candidato == membro.id,
                    Recrutamento.status.in_(list(STATUS_RECRUTAMENTO_ATIVOS)),
                )
                .order_by(Recrutamento.id.desc())
                .limit(1)
            )
            recrutamento = resultado.scalar_one_or_none()
            if recrutamento is None:
                await responder_erro(
                    interacao,
                    titulo="Sem processo ativo",
                    linhas=[f"{membro.mention} não tem recrutamento ativo."],
                )
                return
            recrutamento.formulario_aberto = False
            # Se estava EM_PROVA sem conseguir continuar, volta para PROVA_LIBERADA
            if recrutamento.status == "EM_PROVA":
                recrutamento.status = "PROVA_LIBERADA"
                recrutamento.pergunta_atual = 0
            await session.commit()
            rid = recrutamento.id
            status = recrutamento.status

        await responder_sucesso(
            interacao,
            titulo="Formulário liberado",
            linhas=[
                f"**Registro:** `#{rid}`",
                f"**Status:** `{status}`",
                "`formulario_aberto` = false — o candidato pode tentar iniciar a avaliação de novo.",
            ],
        )

    # ------------------------------------------------------------------
    # Manual (legado)
    # ------------------------------------------------------------------

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
    @app_commands.choices(
        cargo=[
            app_commands.Choice(name="Enfermeiro", value="ENFERMEIRO"),
            app_commands.Choice(name="Paramédico", value="PARAMEDICO"),
        ]
    )
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
            resultado_duplicidade = await session.execute(
                select(Recrutamento).where(
                    Recrutamento.id_fivem == id_fivem,
                    Recrutamento.discord_id_candidato != membro.id,
                    Recrutamento.status.in_(
                        ["ESTUDANDO", "EM_PROVA", "PROVA_LIBERADA", "APROVADO"]
                    ),
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
            cargo_role,
            reason=f"Recrutamento manual registrado por {interaction.user}",
        )

        await interaction.followup.send(
            f"✅ Recrutamento manual registrado para {membro.mention} ({cargo.name}).",
            ephemeral=True,
        )

        try:
            canal_recrutamento = guild.get_channel(CANAIS["RECRUTAMENTOS"])
            if canal_recrutamento:
                await canal_recrutamento.send(
                    view=NovoRecrutamento(
                        candidato=membro,
                        recrutador=recrutador,
                        cargo_role=cargo_role,
                        id_fivem=id_fivem,
                        guild=guild,
                    )
                )

            canal_log = guild.get_channel(CANAIS["LOG_RECRUTAMENTOS"])
            if canal_log:
                await canal_log.send(
                    view=NovoRecrutamentoManualLog(
                        candidato=membro,
                        recrutador=recrutador,
                        executor=interaction.user,
                        cargo_role=cargo_role,
                        id_fivem=id_fivem,
                        guild=guild,
                    )
                )
        except Exception as erro:
            canal_erros = guild.get_channel(CANAIS["LOG_ERROS"])
            if canal_erros:
                await canal_erros.send(
                    f"⚠️ Falha ao registrar log de recrutamento manual: `{erro}`"
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(RecrutamentoCog(bot))
