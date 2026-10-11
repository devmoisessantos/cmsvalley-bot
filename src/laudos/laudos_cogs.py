"""
Comandos de barra do domínio laudos — tudo no grupo `/laudo`.

Uso diário (psicólogo):
  /laudo consulta | cancelar | liberar | prova | meus

Administração:
  /laudo total | painel | painel-prova | consulta-id | laudo-id
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import (
    func,
    select,
)

from src.config import CANAIS
from src.database.conexao import async_session
from src.database.models import (
    ConsultaLaudo,
    Laudo,
    RespostaProvaLaudo,
)
from src.laudos.laudos_service import (
    buscar_consulta_aberta,
    cancelar_consulta_aberta,
    contar_laudos_psicologo,
    membro_e_psicologo,
)
from src.laudos.perguntas_porte import TOTAL_PERGUNTAS_PORTE
from src.laudos.prova_service import liberar_prova_da_consulta
from src.manutencao.manutencao_paineis import recriar_painel
from src.utils.formatacao import formatar_data_hora_local
from src.utils.mensagens import (
    responder_erro,
    responder_info,
    responder_sucesso,
)
from src.utils.permissions import apenas_administrador

registrador = logging.getLogger(__name__)


class LaudosCog(commands.Cog):
    """Consultas, prova de porte e laudos psicológicos."""

    grupo_laudo = app_commands.Group(
        name="laudo",
        description="Consultas, prova de porte e laudos psicológicos",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Uso diário — psicólogo
    # ------------------------------------------------------------------

    @grupo_laudo.command(
        name="consulta",
        description="Mostra sua consulta aberta (se houver)",
    )
    async def laudo_consulta(self, interacao: discord.Interaction):
        """Exibe paciente, passaportes, status da prova e início da consulta."""
        if not isinstance(interacao.user, discord.Member) or not membro_e_psicologo(
            interacao.user
        ):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas psicólogos podem usar este comando."],
            )
            return

        consulta = await buscar_consulta_aberta(interacao.user.id)
        if consulta is None:
            await responder_info(
                interacao,
                titulo="Nenhuma consulta aberta",
                linhas=["Inicie pelo painel **Iniciar Consulta**."],
            )
            return

        linhas = [
            f"**Paciente:** <@{consulta.discord_id_paciente}>",
            f"**Passaporte paciente:** `{consulta.id_fivem_paciente or '—'}`",
            f"**Seu passaporte:** `{consulta.id_fivem_psicologo or '—'}`",
            f"**Início:** `{formatar_data_hora_local(consulta.iniciada_em)}`",
            f"**Status da prova:** `{consulta.status_prova}`",
        ]
        if consulta.status_prova == "FINALIZADA":
            linhas.append(
                f"**Acertos:** `{consulta.acertos_prova or 0}/"
                f"{TOTAL_PERGUNTAS_PORTE}` · "
                f"**Parecer sugerido:** `{consulta.parecer_sugerido or '—'}`"
            )
        elif consulta.pergunta_atual:
            linhas.append(
                f"**Pergunta atual:** `{consulta.pergunta_atual}/"
                f"{TOTAL_PERGUNTAS_PORTE}`"
            )

        await responder_info(
            interacao,
            titulo=f"Consulta aberta #{consulta.id}",
            linhas=linhas,
            delay=45,
        )

    @grupo_laudo.command(
        name="cancelar",
        description="Cancela sua consulta aberta",
    )
    async def laudo_cancelar(self, interacao: discord.Interaction):
        """Cancela só a consulta aberta do psicólogo que pediu."""
        if not isinstance(interacao.user, discord.Member) or not membro_e_psicologo(
            interacao.user
        ):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas psicólogos podem usar este comando."],
            )
            return
        ok, mensagem = await cancelar_consulta_aberta(interacao.user.id)
        if ok:
            await responder_sucesso(
                interacao, titulo="Consulta cancelada", linhas=[mensagem]
            )
        else:
            await responder_erro(
                interacao, titulo="Não cancelado", linhas=[mensagem]
            )

    @grupo_laudo.command(
        name="liberar",
        description="Libera a prova teórica para o paciente da consulta aberta",
    )
    async def laudo_liberar(self, interacao: discord.Interaction):
        """Mesma ação do botão Liberar Avaliação no painel."""
        if not isinstance(interacao.user, discord.Member) or not membro_e_psicologo(
            interacao.user
        ):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas psicólogos podem usar este comando."],
            )
            return

        ok, mensagem, _consulta = await liberar_prova_da_consulta(
            interacao.user.id
        )
        if ok:
            await responder_sucesso(
                interacao,
                titulo="Avaliação liberada",
                linhas=[mensagem],
            )
        else:
            await responder_erro(
                interacao,
                titulo="Não liberado",
                linhas=[mensagem],
            )

    @grupo_laudo.command(
        name="prova",
        description="Status da prova teórica da sua consulta aberta",
    )
    async def laudo_prova(self, interacao: discord.Interaction):
        """Resume status, acertos e respostas da prova ligada à consulta."""
        if not isinstance(interacao.user, discord.Member) or not membro_e_psicologo(
            interacao.user
        ):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas psicólogos podem usar este comando."],
            )
            return

        consulta = await buscar_consulta_aberta(interacao.user.id)
        if consulta is None:
            await responder_info(
                interacao,
                titulo="Nenhuma consulta aberta",
                linhas=["Inicie uma consulta antes de consultar a prova."],
            )
            return

        linhas = [
            f"**Consulta:** `#{consulta.id}`",
            f"**Paciente:** <@{consulta.discord_id_paciente}>",
            f"**Status:** `{consulta.status_prova}`",
        ]
        if consulta.prova_liberada_em is not None:
            linhas.append(
                f"**Liberada em:** "
                f"`{formatar_data_hora_local(consulta.prova_liberada_em)}`"
            )
        if consulta.prova_iniciada_em is not None:
            linhas.append(
                f"**Iniciada em:** "
                f"`{formatar_data_hora_local(consulta.prova_iniciada_em)}`"
            )
        if consulta.prova_finalizada_em is not None:
            linhas.append(
                f"**Finalizada em:** "
                f"`{formatar_data_hora_local(consulta.prova_finalizada_em)}`"
            )
        if consulta.status_prova == "EM_ANDAMENTO":
            linhas.append(
                f"**Pergunta atual:** `{consulta.pergunta_atual}/"
                f"{TOTAL_PERGUNTAS_PORTE}`"
            )
        if consulta.status_prova == "FINALIZADA":
            linhas.append(
                f"**Acertos:** `{consulta.acertos_prova or 0}/"
                f"{TOTAL_PERGUNTAS_PORTE}`"
            )
            linhas.append(
                f"**Parecer sugerido:** `{consulta.parecer_sugerido or '—'}`"
            )
            async with async_session() as sessao:
                resultado = await sessao.execute(
                    select(RespostaProvaLaudo)
                    .where(RespostaProvaLaudo.consulta_id == consulta.id)
                    .order_by(RespostaProvaLaudo.numero_pergunta.asc())
                )
                respostas = list(resultado.scalars().all())
            if respostas:
                resumo = ", ".join(
                    f"Q{r.numero_pergunta}:`{r.resposta_escolhida}`"
                    f"{'✓' if r.correta else '✗'}"
                    for r in respostas
                )
                linhas.append(f"**Respostas:** {resumo}")

        await responder_info(
            interacao,
            titulo=f"Prova · consulta #{consulta.id}",
            linhas=linhas,
            delay=60,
        )

    @grupo_laudo.command(
        name="meus",
        description="Quantidade de laudos que você já emitiu",
    )
    async def laudo_meus(self, interacao: discord.Interaction):
        """Conta laudos emitidos pelo psicólogo que executou o comando."""
        if not isinstance(interacao.user, discord.Member) or not membro_e_psicologo(
            interacao.user
        ):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas psicólogos podem usar este comando."],
            )
            return
        total = await contar_laudos_psicologo(interacao.user.id)
        await responder_info(
            interacao,
            titulo="Seus laudos",
            linhas=[f"Total emitidos: **{total}**"],
        )

    # ------------------------------------------------------------------
    # Administração e manutenção
    # ------------------------------------------------------------------

    @grupo_laudo.command(
        name="total",
        description="[Admin] Total de laudos emitidos por um psicólogo",
    )
    @app_commands.describe(membro="Psicólogo a consultar")
    @apenas_administrador()
    async def laudo_total(
        self, interacao: discord.Interaction, membro: discord.Member
    ):
        """Contagem de laudos de outro membro (administração)."""
        total = await contar_laudos_psicologo(membro.id)
        await responder_info(
            interacao,
            titulo=f"Laudos · {membro.display_name}",
            linhas=[f"{membro.mention} emitiu **{total}** laudo(s)."],
        )

    @grupo_laudo.command(
        name="painel",
        description="[Admin] Republica o painel dos psicólogos",
    )
    @apenas_administrador()
    async def laudo_painel(self, interacao: discord.Interaction):
        """Apaga o painel antigo e publica de novo no CANAL_PAINEL_LAUDOS."""
        await interacao.response.defer(ephemeral=True)
        resultado = await recriar_painel(self.bot, "laudos")
        if not resultado.ok:
            await responder_erro(
                interacao,
                titulo="Falha ao republicar painel",
                linhas=[resultado.mensagem],
            )
            return
        canal_id = CANAIS.get("CANAL_PAINEL_LAUDOS") or 0
        await responder_sucesso(
            interacao,
            titulo="Painel de laudos republicado",
            linhas=[
                resultado.mensagem,
                f"Canal: <#{canal_id}>" if canal_id else "Canal não configurado.",
            ],
            delay=20,
        )

    @grupo_laudo.command(
        name="painel-prova",
        description="[Admin] Republica o painel da prova (paciente)",
    )
    @apenas_administrador()
    async def laudo_painel_prova(self, interacao: discord.Interaction):
        """Apaga e republica o painel em CANAL_AVALIACAO_PSICOLOGICA."""
        await interacao.response.defer(ephemeral=True)
        canal_id = CANAIS.get("CANAL_AVALIACAO_PSICOLOGICA") or 0
        if not canal_id:
            await responder_erro(
                interacao,
                titulo="Canal não configurado",
                linhas=[
                    "Defina `CANAL_AVALIACAO_PSICOLOGICA` em `src/config.py`.",
                ],
            )
            return

        resultado = await recriar_painel(self.bot, "laudos_prova")
        if not resultado.ok:
            await responder_erro(
                interacao,
                titulo="Falha ao republicar painel da prova",
                linhas=[resultado.mensagem],
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Painel da prova republicado",
            linhas=[
                resultado.mensagem,
                f"Canal: <#{canal_id}>",
            ],
            delay=20,
        )

    @grupo_laudo.command(
        name="consulta-id",
        description="[Admin] Consulta os dados de uma consulta pelo ID",
    )
    @app_commands.describe(consulta_id="Número da consulta (ex.: 12)")
    @apenas_administrador()
    async def laudo_consulta_id(
        self, interacao: discord.Interaction, consulta_id: int
    ):
        """Lê a linha da consulta no banco e mostra status e prova."""
        await interacao.response.defer(ephemeral=True)
        async with async_session() as sessao:
            consulta = await sessao.get(ConsultaLaudo, consulta_id)
        if consulta is None:
            await responder_erro(
                interacao,
                titulo="Consulta não encontrada",
                linhas=[f"Não há consulta com id `{consulta_id}`."],
            )
            return

        linhas = [
            f"**Status consulta:** `{consulta.status}`",
            f"**Psicólogo:** <@{consulta.discord_id_psicologo}>",
            f"**Paciente:** <@{consulta.discord_id_paciente}>",
            f"**Passaporte paciente:** `{consulta.id_fivem_paciente or '—'}`",
            f"**Passaporte psicólogo:** `{consulta.id_fivem_psicologo or '—'}`",
            f"**Início:** `{formatar_data_hora_local(consulta.iniciada_em)}`",
            f"**Status prova:** `{consulta.status_prova}`",
            f"**Pergunta atual:** `{consulta.pergunta_atual}`",
        ]
        if consulta.finalizada_em is not None:
            linhas.append(
                f"**Finalizada em:** "
                f"`{formatar_data_hora_local(consulta.finalizada_em)}`"
            )
        if consulta.acertos_prova is not None:
            linhas.append(
                f"**Acertos:** `{consulta.acertos_prova}/"
                f"{TOTAL_PERGUNTAS_PORTE}` · "
                f"**Parecer sugerido:** `{consulta.parecer_sugerido or '—'}`"
            )

        await responder_info(
            interacao,
            titulo=f"Consulta #{consulta.id}",
            linhas=linhas,
            delay=60,
        )

    @grupo_laudo.command(
        name="laudo-id",
        description="[Admin] Consulta um laudo emitido pelo ID",
    )
    @app_commands.describe(laudo_id="Número do laudo (ex.: 5)")
    @apenas_administrador()
    async def laudo_laudo_id(
        self, interacao: discord.Interaction, laudo_id: int
    ):
        """Lê o laudo no banco e mostra parecer, motivo e vínculos."""
        await interacao.response.defer(ephemeral=True)
        async with async_session() as sessao:
            laudo = await sessao.get(Laudo, laudo_id)
        if laudo is None:
            await responder_erro(
                interacao,
                titulo="Laudo não encontrado",
                linhas=[f"Não há laudo com id `{laudo_id}`."],
            )
            return

        motivo_curto = (laudo.motivo or "")[:400]
        linhas = [
            f"**Consulta:** `#{laudo.consulta_id}`",
            f"**Psicólogo:** <@{laudo.discord_id_psicologo}>",
            f"**Paciente:** <@{laudo.discord_id_paciente}>",
            f"**Passaporte paciente:** `{laudo.id_fivem_paciente or '—'}`",
            f"**Parecer:** `{laudo.parecer}`",
            f"**CRP:** `{laudo.registro_profissional}`",
            f"**Criado em:** `{formatar_data_hora_local(laudo.criado_em)}`",
            f"**Motivo:** {motivo_curto}",
        ]
        if laudo.canal_laudo_message_id:
            linhas.append(
                f"**Message ID no canal:** `{laudo.canal_laudo_message_id}`"
            )

        await responder_info(
            interacao,
            titulo=f"Laudo #{laudo.id}",
            linhas=linhas,
            delay=60,
        )

    @grupo_laudo.command(
        name="stats",
        description="[Admin] Totais de consultas e laudos no banco",
    )
    @apenas_administrador()
    async def laudo_stats(self, interacao: discord.Interaction):
        """Contagens agregadas para conferência rápida do domínio."""
        await interacao.response.defer(ephemeral=True)
        async with async_session() as sessao:
            total_consultas = int(
                (
                    await sessao.execute(
                        select(func.count()).select_from(ConsultaLaudo)
                    )
                ).scalar_one()
                or 0
            )
            abertas = int(
                (
                    await sessao.execute(
                        select(func.count())
                        .select_from(ConsultaLaudo)
                        .where(ConsultaLaudo.status == "ABERTA")
                    )
                ).scalar_one()
                or 0
            )
            provas_finalizadas = int(
                (
                    await sessao.execute(
                        select(func.count())
                        .select_from(ConsultaLaudo)
                        .where(ConsultaLaudo.status_prova == "FINALIZADA")
                    )
                ).scalar_one()
                or 0
            )
            total_laudos = int(
                (
                    await sessao.execute(select(func.count()).select_from(Laudo))
                ).scalar_one()
                or 0
            )
            aprovados = int(
                (
                    await sessao.execute(
                        select(func.count())
                        .select_from(Laudo)
                        .where(Laudo.parecer == "APROVADO")
                    )
                ).scalar_one()
                or 0
            )
            reprovados = int(
                (
                    await sessao.execute(
                        select(func.count())
                        .select_from(Laudo)
                        .where(Laudo.parecer == "REPROVADO")
                    )
                ).scalar_one()
                or 0
            )

        await responder_info(
            interacao,
            titulo="Estatísticas · laudos",
            linhas=[
                f"**Consultas (total):** `{total_consultas}`",
                f"**Consultas abertas:** `{abertas}`",
                f"**Provas finalizadas:** `{provas_finalizadas}`",
                f"**Laudos emitidos:** `{total_laudos}`",
                f"**Aprovados:** `{aprovados}` · **Reprovados:** `{reprovados}`",
            ],
            delay=45,
        )


async def setup(bot: commands.Bot):
    """Registra o grupo `/laudo` no bot."""
    await bot.add_cog(LaudosCog(bot))
