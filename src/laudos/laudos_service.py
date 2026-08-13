"""
Regras de negócio das consultas e laudos psicológicos.
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import (
    func,
    select,
)

from src.config import CARGOS
from src.database.connection import async_session
from src.database.models import (
    ConsultaLaudo,
    EstadoPlantao,
    Laudo,
    Recrutamento,
    Usuario,
)
from src.plantao.ocr.scraping_membros import extrair_id_do_apelido

NOMES_CARGOS_PSICOLOGO = (
    "🩺・Psicólogo",
    "👑・Responsável Psicólogo・🧠",
)


def membro_e_psicologo(membro: discord.Member) -> bool:
    """True se o membro tem cargo de psicólogo ou responsável."""
    if membro.guild_permissions.administrator:
        return True
    ids_permitidos = {CARGOS[nome] for nome in NOMES_CARGOS_PSICOLOGO if nome in CARGOS}
    return any(cargo.id in ids_permitidos for cargo in membro.roles)


async def _buscar_id_fivem_no_banco(discord_id: int) -> str | None:
    """
    Ordem: usuarios → estado_plantao → qualquer recrutamento com passaporte.
    (Visitantes novos quase nunca têm recrutamento APROVADO.)
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario.id_fivem).where(
                Usuario.discord_id == discord_id,
                Usuario.id_fivem.is_not(None),
            )
        )
        valor = resultado.scalar_one_or_none()
        if valor:
            return str(valor).strip() or None

        resultado = await sessao.execute(
            select(EstadoPlantao.id_fivem).where(
                EstadoPlantao.discord_id == discord_id,
                EstadoPlantao.id_fivem.is_not(None),
            )
        )
        valor = resultado.scalar_one_or_none()
        if valor:
            return str(valor).strip() or None

        resultado = await sessao.execute(
            select(Recrutamento.id_fivem)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.id_fivem.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        valor = resultado.scalar_one_or_none()
        if valor:
            return str(valor).strip() or None
    return None


async def _persistir_id_fivem_no_usuario(
    membro: discord.Member,
    id_fivem: str,
) -> None:
    """
    Grava o passaporte em usuarios (e preenche se a linha ainda não existir).
    Nunca sobrescreve um id_fivem que já estiver salvo.
    """
    id_limpo = str(id_fivem).strip()[:20]
    if not id_limpo:
        return

    nickname = (membro.nick or membro.display_name or membro.name or "")[:100]

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario).where(Usuario.discord_id == membro.id)
        )
        usuario = resultado.scalar_one_or_none()
        if usuario is None:
            sessao.add(
                Usuario(
                    discord_id=membro.id,
                    id_fivem=id_limpo,
                    nickname_atual=nickname or None,
                    status="VISITANTE",
                    ja_foi_aprovado=False,
                )
            )
        else:
            if not usuario.id_fivem:
                usuario.id_fivem = id_limpo
            if nickname and not usuario.nickname_atual:
                usuario.nickname_atual = nickname
        await sessao.commit()


async def resolver_e_persistir_id_fivem(membro: discord.Member) -> str | None:
    """
    Resolve o passaporte FiveM do membro e grava em usuarios se ainda não tinha.

    Fontes (nessa ordem):
      1. Tabela usuarios
      2. estado_plantao
      3. recrutamento (qualquer status com id)
      4. Apelido no padrão `Nome | 12345`
    """
    id_banco = await _buscar_id_fivem_no_banco(membro.id)
    if id_banco:
        # Garante que usuarios também tem o valor (visitante antigo só no plantão, etc.)
        await _persistir_id_fivem_no_usuario(membro, id_banco)
        return id_banco

    nome_exibido = membro.nick or membro.display_name or membro.name or ""
    id_do_apelido = extrair_id_do_apelido(nome_exibido)
    if id_do_apelido:
        await _persistir_id_fivem_no_usuario(membro, id_do_apelido)
        return str(id_do_apelido).strip()

    return None


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
    id_fivem_paciente_manual: str | None = None,
) -> tuple[bool, str, ConsultaLaudo | None]:
    """
    Abre uma consulta ABERTA para o psicólogo.
    Só permite uma consulta aberta por vez por psicólogo.

    id_fivem_paciente_manual: passaporte digitado pelo psicólogo quando
    o paciente (visitante) ainda não tem ID no banco/apelido.
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

    id_fivem_psicologo = await resolver_e_persistir_id_fivem(psicologo)
    id_fivem_paciente = await resolver_e_persistir_id_fivem(paciente)

    # Passaporte informado na hora (visitante sem ID no nick/banco)
    if not id_fivem_paciente and id_fivem_paciente_manual:
        id_manual = str(id_fivem_paciente_manual).strip()
        if id_manual.isdigit() and 1 <= len(id_manual) <= 7:
            await _persistir_id_fivem_no_usuario(paciente, id_manual)
            id_fivem_paciente = id_manual

    if not id_fivem_psicologo:
        return (
            False,
            (
                "Seu ID FiveM não foi encontrado. "
                "Coloque o passaporte no apelido no formato `Nome | 12345` "
                "ou vincule o ID no banco antes de atender."
            ),
            None,
        )
    if not id_fivem_paciente:
        return (
            False,
            (
                f"O paciente {paciente.mention} não tem ID FiveM reconhecido.\n"
                "Peça o apelido no padrão **`Nome | passaporte`** "
                "(ex.: `João Silva | 1382`) **ou** informe o passaporte no modal."
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
        return (
            False,
            f"Falha ao gravar a consulta no banco: `{type(erro).__name__}`.",
            None,
        )


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
    emoji_parecer = "✅ **APROVADO**" if parecer == "APROVADO" else "❌ **REPROVADO**"
    return (
        "### `👤` **Identificação do Paciente:**\n"
        f"> - **Nome:** <@{discord_id_paciente}> · **Passaporte:** `{id_fivem_paciente}`\n\n"
        "### `🥼` **Psicólogo Responsável:**\n"
        f"> - **Nome:** <@{discord_id_psicologo}>\n"
        f"> - **Passaporte:** `{id_fivem_psicologo}`\n"
        f"> - **Registro Profissional:** `{registro_profissional}`\n\n"
        "### `📊` **Resultado da Avaliação:**\n"
        f"> - **Parecer Final:** {emoji_parecer}\n"
        f"> - **Motivo:** {motivo}"
    )


def montar_yaml_para_copiar(
    *,
    discord_id_paciente: int,
    id_fivem_paciente: str,
    discord_id_psicologo: int,
    id_fivem_psicologo: str,
    registro_profissional: str,
    parecer: str,
    motivo: str,
) -> str:
    """
    Texto para o responsável copiar e colar no servidor Valley Roleplay.
    Só vai na ephemeral — o canal de laudos continua com o card formatado.
    """
    emoji_parecer = "✅ **APROVADO**" if parecer == "APROVADO" else "❌ **REPROVADO**"
    corpo = (
        "# 📋 CMS Valley — LAUDO PSICOLÓGICO\n"
        "> 📌 **Finalidade:** Avaliação para porte de arma de fogo."
        "\n\n"
        "👤 **Identificação do Avaliado**"
        "\n"
        f"• Nome: <@{discord_id_paciente}> • FID: `{id_fivem_paciente}`"
        "\n\n"
        "🥼 **Psicólogo Responsável**"
        "\n"
        f"• Nome: <@{discord_id_psicologo}> • FID: `{id_fivem_psicologo}`"
        "\n"
        f"• Registro Profissional: `{registro_profissional}`"
        "\n\n"
        "📊 **Resultado da Avaliação**"
        "\n"
        f"• **Parecer Final:** {emoji_parecer}"
        "\n"
        f"• Motivo: {motivo}"
    )
    return corpo


async def gerar_laudo(
    *,
    psicologo: discord.Member,
    parecer: str,
    motivo: str,
) -> tuple[bool, str, Laudo | None, str | None, str | None]:
    """
    Finaliza a consulta aberta e cria o laudo.
    Retorna (ok, mensagem_ui, laudo, texto_publicado, texto_yaml_copiar).
    """
    if not membro_e_psicologo(psicologo):
        return (
            False,
            "Apenas psicólogos autorizados podem gerar laudo.",
            None,
            None,
            None,
        )

    parecer_normalizado = parecer.strip().upper()
    if parecer_normalizado not in ("APROVADO", "REPROVADO"):
        return False, "Parecer inválido. Use APROVADO ou REPROVADO.", None, None, None

    motivo_limpo = (motivo or "").strip()
    if len(motivo_limpo) < 10:
        return (
            False,
            "Descreva o motivo com pelo menos 10 caracteres.",
            None,
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
            None,
        )

    ano = datetime.now(timezone.utc).year
    # 5 primeiros dígitos do Discord ID do psicólogo → CRP/(85910) 2026
    primeiros_cinco_discord = str(psicologo.id)[:5]
    registro_profissional = f"CRP-{ano}/{primeiros_cinco_discord}"

    dados_texto = dict(
        discord_id_paciente=consulta.discord_id_paciente,
        id_fivem_paciente=consulta.id_fivem_paciente or "—",
        discord_id_psicologo=consulta.discord_id_psicologo,
        id_fivem_psicologo=consulta.id_fivem_psicologo or "—",
        registro_profissional=registro_profissional,
        parecer=parecer_normalizado,
        motivo=motivo_limpo,
    )
    texto_laudo = montar_texto_laudo(**dados_texto)
    texto_yaml = montar_yaml_para_copiar(**dados_texto)

    try:
        async with async_session() as sessao:
            registro_consulta = await sessao.get(ConsultaLaudo, consulta.id)
            if registro_consulta is None or registro_consulta.status != "ABERTA":
                return (
                    False,
                    "A consulta aberta não foi encontrada (pode ter sido finalizada por outro fluxo).",
                    None,
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
                texto_yaml,
            )
    except Exception as erro:
        return (
            False,
            f"Falha ao gravar o laudo no banco: `{type(erro).__name__}`.",
            None,
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
