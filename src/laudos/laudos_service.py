"""
Regras de negócio das consultas e laudos psicológicos.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from sqlalchemy import func, select

from src.config import CARGOS
from src.database.connection import async_session
from src.database.models import ConsultaLaudo, Laudo
from src.recrutamento.recrutamento_service import resolver_id_fivem

NOMES_CARGOS_PSICOLOGO = (
    "🩺・Psicólogo",
    "👑・Responsável Psicólogo・🧠",
)


def membro_e_psicologo(membro: discord.Member) -> bool:
    """True se o membro tem cargo de psicólogo ou responsável."""
    if membro.guild_permissions.administrator:
        return True
    ids_permitidos = {
        CARGOS[nome] for nome in NOMES_CARGOS_PSICOLOGO if nome in CARGOS
    }
    return any(cargo.id in ids_permitidos for cargo in membro.roles)


async def buscar_consulta_aberta(discord_id_psicologo: int) -> ConsultaLaudo | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(ConsultaLaudo)
            .where(
                ConsultaLaudo.discord_id_psicologo == discord_id_psicologo,
                ConsultaLaudo.status == "ABERTA",
            )
            .order_by(ConsultaLaudo.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def iniciar_consulta(
    *,
    psicologo: discord.Member,
    paciente: discord.Member,
) -> tuple[bool, str, ConsultaLaudo | None]:
    """
    Abre uma consulta ABERTA para o psicólogo.
    Só permite uma consulta aberta por vez por psicólogo.
    """
    if not membro_e_psicologo(psicologo):
        return (
            False,
            "Apenas psicólogos autorizados podem iniciar consulta.",
            None,
        )

    if paciente.bot:
        return False, "Não é possível iniciar consulta com um bot.", None

    if paciente.id == psicologo.id:
        return False, "Você não pode gerar laudo para si mesmo.", None

    consulta_existente = await buscar_consulta_aberta(psicologo.id)
    if consulta_existente is not None:
        return (
            False,
            (
                f"Você já tem uma consulta **aberta** com <@{consulta_existente.discord_id_paciente}> "
                f"(#{consulta_existente.id}). Finalize o laudo ou cancele antes de iniciar outra."
            ),
            consulta_existente,
        )

    id_fivem_psicologo = await resolver_id_fivem(psicologo.id)
    id_fivem_paciente = await resolver_id_fivem(paciente.id)

    if not id_fivem_psicologo:
        return (
            False,
            "Seu ID FiveM não foi encontrado no banco. Vincule o ID antes de atender.",
            None,
        )
    if not id_fivem_paciente:
        return (
            False,
            (
                f"O paciente {paciente.mention} não tem ID FiveM registrado. "
                "Peça para o paciente vincular o passaporte ou informe o recrutamento."
            ),
            None,
        )

    try:
        async with async_session() as sessao:
            nova_consulta = ConsultaLaudo(
                discord_id_psicologo=psicologo.id,
                discord_id_paciente=paciente.id,
                id_fivem_psicologo=id_fivem_psicologo,
                id_fivem_paciente=id_fivem_paciente,
                status="ABERTA",
                iniciada_em=datetime.now(timezone.utc),
            )
            sessao.add(nova_consulta)
            await sessao.commit()
            await sessao.refresh(nova_consulta)
            return (
                True,
                (
                    f"Consulta **#{nova_consulta.id}** iniciada com {paciente.mention}.\n"
                    f"Passaporte do paciente: `{id_fivem_paciente}`.\n"
                    "Agora você pode clicar em **Gerar Laudo**."
                ),
                nova_consulta,
            )
    except Exception as erro:
        return False, f"Falha ao gravar a consulta no banco: `{type(erro).__name__}`.", None


async def cancelar_consulta_aberta(discord_id_psicologo: int) -> tuple[bool, str]:
    consulta = await buscar_consulta_aberta(discord_id_psicologo)
    if consulta is None:
        return False, "Você não tem consulta aberta para cancelar."
    try:
        async with async_session() as sessao:
            registro = await sessao.get(ConsultaLaudo, consulta.id)
            if registro is None or registro.status != "ABERTA":
                return False, "A consulta já não está aberta."
            registro.status = "CANCELADA"
            registro.finalizada_em = datetime.now(timezone.utc)
            await sessao.commit()
            return True, f"Consulta **#{consulta.id}** cancelada."
    except Exception as erro:
        return False, f"Falha ao cancelar no banco: `{type(erro).__name__}`."


def montar_texto_laudo(
    *,
    discord_id_paciente: int,
    id_fivem_paciente: str,
    discord_id_psicologo: int,
    id_fivem_psicologo: str,
    registro_profissional: str,
    parecer: str,
    motivo: str,
) -> str:
    emoji_parecer = "✅ APROVADO" if parecer == "APROVADO" else "❌ REPROVADO"
    return (
        "📋 **LAUDO PSICOLÓGICO — AVALIAÇÃO PARA PORTE DE ARMA DE FOGO**\n\n"
        "👤 **Identificação do Avaliado**\n"
        f"• Nome: <@{discord_id_paciente}>\n"
        f"• Passaporte: `{id_fivem_paciente}`\n\n"
        "🥼 **Psicólogo Responsável**\n"
        f"• Nome: <@{discord_id_psicologo}>\n"
        f"• Passaporte: `{id_fivem_psicologo}`\n"
        f"• Registro Profissional: `{registro_profissional}`\n\n"
        "📊 **Resultado da Avaliação**\n"
        f"• **Parecer Final:** {emoji_parecer}\n"
        f"• Motivo: {motivo}"
    )


async def gerar_laudo(
    *,
    psicologo: discord.Member,
    parecer: str,
    motivo: str,
) -> tuple[bool, str, Laudo | None, str | None]:
    """
    Finaliza a consulta aberta e cria o laudo.
    Retorna (ok, mensagem_ui, laudo, texto_publicado).
    """
    if not membro_e_psicologo(psicologo):
        return False, "Apenas psicólogos autorizados podem gerar laudo.", None, None

    parecer_normalizado = parecer.strip().upper()
    if parecer_normalizado not in ("APROVADO", "REPROVADO"):
        return False, "Parecer inválido. Use APROVADO ou REPROVADO.", None, None

    motivo_limpo = (motivo or "").strip()
    if len(motivo_limpo) < 10:
        return (
            False,
            "Descreva o motivo com pelo menos 10 caracteres.",
            None,
            None,
        )

    consulta = await buscar_consulta_aberta(psicologo.id)
    if consulta is None:
        return (
            False,
            "Você precisa **Iniciar Consulta** antes de gerar o laudo.",
            None,
            None,
        )

    ano = datetime.now(timezone.utc).year
    id_ref = consulta.id_fivem_psicologo or str(psicologo.id)[-5:]
    registro_profissional = f"CRP/{id_ref} {ano}"

    texto_laudo = montar_texto_laudo(
        discord_id_paciente=consulta.discord_id_paciente,
        id_fivem_paciente=consulta.id_fivem_paciente or "—",
        discord_id_psicologo=consulta.discord_id_psicologo,
        id_fivem_psicologo=consulta.id_fivem_psicologo or "—",
        registro_profissional=registro_profissional,
        parecer=parecer_normalizado,
        motivo=motivo_limpo,
    )

    try:
        async with async_session() as sessao:
            registro_consulta = await sessao.get(ConsultaLaudo, consulta.id)
            if registro_consulta is None or registro_consulta.status != "ABERTA":
                return (
                    False,
                    "A consulta aberta não foi encontrada (pode ter sido finalizada por outro fluxo).",
                    None,
                    None,
                )

            novo_laudo = Laudo(
                consulta_id=registro_consulta.id,
                discord_id_psicologo=registro_consulta.discord_id_psicologo,
                discord_id_paciente=registro_consulta.discord_id_paciente,
                id_fivem_psicologo=registro_consulta.id_fivem_psicologo,
                id_fivem_paciente=registro_consulta.id_fivem_paciente,
                parecer=parecer_normalizado,
                motivo=motivo_limpo[:1500],
                registro_profissional=registro_profissional,
            )
            sessao.add(novo_laudo)
            registro_consulta.status = "FINALIZADA"
            registro_consulta.finalizada_em = datetime.now(timezone.utc)
            await sessao.commit()
            await sessao.refresh(novo_laudo)

            return (
                True,
                f"Laudo **#{novo_laudo.id}** gerado ({parecer_normalizado}).",
                novo_laudo,
                texto_laudo,
            )
    except Exception as erro:
        return (
            False,
            f"Falha ao gravar o laudo no banco: `{type(erro).__name__}`.",
            None,
            None,
        )


async def contar_laudos_psicologo(discord_id_psicologo: int) -> int:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.count())
            .select_from(Laudo)
            .where(Laudo.discord_id_psicologo == discord_id_psicologo)
        )
        return int(resultado.scalar_one() or 0)
