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
from src.database.conexao import async_session
from src.database.models import (
    Recrutamento,
    Usuario,
)
from src.recrutamento.recrutamento_class import NovoRecrutamento
from src.recrutamento.recrutamento_logger import NovoRecrutamentoManualLog
from src.recrutamento.recrutamento_service import (
    STATUS_RECRUTAMENTO_ATIVOS,
    buscar_recrutamento_ativo,
    cancelar_recrutamento_ativo,
)
from src.utils.formatacao import formatar_data_hora_local
from src.utils.mensagens import (
    responder_aviso,
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
        """Mostra o processo atual e os últimos registros do candidato.

        Consulta o banco para apresentar tanto o recrutamento ativo quanto um
        pequeno histórico, ajudando administradores a diferenciar uma falha de
        um processo já concluído antes de tomar qualquer ação corretiva.
        """
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

        if ativo:
            linhas = [
                f"**Candidato:** {membro.mention}",
                f"**Processo ativo:** `#{ativo.id}`",
                f"**Status:** `{ativo.status}`",
                f"**Recrutador:** <@{ativo.discord_id_recrutador}>",
                f"**FiveM:** `{ativo.id_fivem or '—'}`",
                f"**Formulário:** `{'aberto' if ativo.formulario_aberto else 'fechado'}"
                f"`",
                f"**Início:** `{formatar_data_hora_local(ativo.data_inicio)}`",
            ]
        else:
            linhas = [
                f"**Candidato:** {membro.mention}",
                "**Processo ativo:** _nenhum_",
            ]

        linhas.append("")
        linhas.append("**Histórico recente**")
        for registro in ultimos:
            linhas.append(
                f"`#{registro.id}` · **{registro.status}** · "
                f"FiveM `{registro.id_fivem or '—'}` · "
                f"form. `{'aberto' if registro.formulario_aberto else 'fechado'}` · "
                f"`{formatar_data_hora_local(registro.data_inicio)}`"
            )

        await responder_info(
            interacao,
            titulo=f"Recrutamento · {membro.display_name}",
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
        """Cancela um processo pendente e restaura o candidato para recomeçar.

        Delega a remoção de cargos temporários e das respostas ao serviço, que
        também registra a decisão. O motivo é limitado antes de ser gravado
        para manter o registro administrativo curto e seguro para exibição.
        """
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
        """Destrava uma prova marcada como formulário aberto no banco.

        Se o processo ficou em prova, retorna-o à etapa liberada e zera a
        pergunta atual; isso permite uma nova tentativa sem apagar todo o
        recrutamento. A alteração é persistida antes da confirmação no Discord.
        """
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
            id_do_cargo = recrutamento.id
            status = recrutamento.status

        await responder_sucesso(
            interacao,
            titulo="Formulário liberado",
            linhas=[
                f"**Registro:** `#{id_do_cargo}`",
                f"**Status:** `{status}`",
                "`formulario_aberto` = false — o candidato pode tentar iniciar a "
                "avaliação de novo.",
            ],
        )

    # ------------------------------------------------------------------
    # Manual (legado)
    # ------------------------------------------------------------------

    @app_commands.command(
        name="recrutamento-manual",
        description="Registra manualmente um Recrutamento Realizado (uso em caso de "
        "bot fora do ar)",
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
        """Reconstrói manualmente uma aprovação quando o fluxo automático falhou.

        Evita associar um mesmo ID FiveM a outro processo ativo, atualiza os
        registros no banco e atribui os cargos finais no Discord. Também publica
        os painéis e logs para que a aprovação manual mantenha o mesmo rastro
        administrativo do recrutamento realizado pelo bot.
        """
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        cargo_role = guild.get_role(CARGOS[CARGOS_FINAIS[cargo.value]])
        if cargo_role is None:
            await responder_erro(
                interaction,
                titulo="Não encontrado",
                linhas=[
                    "Cargo final não encontrado no servidor. Confira o CARGOS no "
                    "config.py.",
                ],
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
                await responder_aviso(
                    interaction,
                    titulo="Já em andamento",
                    linhas=[
                        f"O ID FiveM `{id_fivem}` já está associado a "
                        f"<@{conflito.discord_id_candidato}>. "
                        f"Confira antes de continuar.",
                    ],
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

        cargos_manual = [cargo_role]
        if cargo.value == "PARAMEDICO":
            cargo_enfermeiro = guild.get_role(CARGOS.get("🔰・Enfermeiro (a)", 0) or 0)
            if cargo_enfermeiro is not None:
                cargos_manual.append(cargo_enfermeiro)
        await membro.add_roles(
            *cargos_manual,
            reason=f"Recrutamento manual registrado por {interaction.user}",
        )

        await responder_sucesso(
            interaction,
            titulo="Recrutamento registrado",
            linhas=[
                f"Recrutamento manual registrado para {membro.mention} ({cargo.name}).",
            ],
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
    """Adiciona ao bot os comandos administrativos de recrutamento."""
    await bot.add_cog(RecrutamentoCog(bot))
