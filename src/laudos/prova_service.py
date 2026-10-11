"""
Regras da prova teórica de porte de arma (domínio laudos).

Fluxo:
  1. Psicólogo libera a prova na consulta aberta.
  2. Paciente inicia uma única vez e responde as 10 questões.
  3. Resultado vai ao canal da equipe; o laudo continua manual.
"""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import (
    select,
)

from src.config import CANAIS
from src.database.conexao import async_session
from src.database.models import (
    ConsultaLaudo,
    RespostaProvaLaudo,
)
from src.laudos.perguntas_porte import (
    NOTA_MINIMA_PORTE,
    TOTAL_PERGUNTAS_PORTE,
    obter_pergunta,
    texto_da_opcao,
)

registrador = logging.getLogger(__name__)


def barra_progresso_porte(atual: int, total: int) -> str:
    """Barra visual da posição na prova de porte."""
    preenchido = "▰" * atual
    vazio = "▱" * max(0, total - atual)
    return f"{preenchido}{vazio}  `{atual}/{total}`"


async def buscar_consulta_prova_liberada(
    discord_id_paciente: int,
) -> ConsultaLaudo | None:
    """Consulta aberta do paciente com prova liberada e ainda não iniciada."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(ConsultaLaudo)
            .where(
                ConsultaLaudo.discord_id_paciente == discord_id_paciente,
                ConsultaLaudo.status == "ABERTA",
                ConsultaLaudo.status_prova == "LIBERADA",
            )
            .order_by(ConsultaLaudo.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def buscar_consulta_prova_em_andamento(
    discord_id_paciente: int,
) -> ConsultaLaudo | None:
    """Consulta em que o paciente já está respondendo a prova."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(ConsultaLaudo)
            .where(
                ConsultaLaudo.discord_id_paciente == discord_id_paciente,
                ConsultaLaudo.status == "ABERTA",
                ConsultaLaudo.status_prova == "EM_ANDAMENTO",
            )
            .order_by(ConsultaLaudo.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def liberar_prova_da_consulta(
    discord_id_psicologo: int,
) -> tuple[bool, str, ConsultaLaudo | None]:
    """
    Marca a consulta aberta do psicólogo como prova liberada.

    Só funciona com consulta ABERTA e status_prova NAO_LIBERADA.
    """
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
        consulta = resultado.scalar_one_or_none()
        if consulta is None:
            return (
                False,
                "Você precisa **Iniciar Consulta** antes de liberar a "
                "avaliação.",
                None,
            )

        if consulta.status_prova == "LIBERADA":
            return (
                False,
                (
                    f"A prova da consulta **#{consulta.id}** já está "
                    f"**liberada** para <@{consulta.discord_id_paciente}>."
                ),
                consulta,
            )

        if consulta.status_prova == "EM_ANDAMENTO":
            return (
                False,
                (
                    f"O paciente <@{consulta.discord_id_paciente}> já está "
                    f"**fazendo** a prova da consulta **#{consulta.id}**."
                ),
                consulta,
            )

        if consulta.status_prova == "FINALIZADA":
            return (
                False,
                (
                    f"A prova da consulta **#{consulta.id}** já foi "
                    f"**finalizada** ({consulta.acertos_prova or 0}/"
                    f"{TOTAL_PERGUNTAS_PORTE})."
                ),
                consulta,
            )

        consulta.status_prova = "LIBERADA"
        consulta.prova_liberada_em = datetime.now(timezone.utc)
        await sessao.commit()
        await sessao.refresh(consulta)
        return (
            True,
            (
                f"Prova liberada na consulta **#{consulta.id}** para "
                f"<@{consulta.discord_id_paciente}>.\n"
                "O paciente pode iniciar em "
                "**#avaliação-psicológica**."
            ),
            consulta,
        )


async def iniciar_prova_paciente(
    discord_id_paciente: int,
) -> tuple[bool, str, ConsultaLaudo | None]:
    """
    Reserva a única tentativa do paciente e marca a prova como EM_ANDAMENTO.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(ConsultaLaudo)
            .where(
                ConsultaLaudo.discord_id_paciente == discord_id_paciente,
                ConsultaLaudo.status == "ABERTA",
            )
            .order_by(ConsultaLaudo.id.desc())
            .limit(1)
        )
        consulta = resultado.scalar_one_or_none()
        if consulta is None:
            return (
                False,
                "Você não tem consulta aberta com prova liberada.",
                None,
            )

        if consulta.status_prova == "NAO_LIBERADA":
            return (
                False,
                "Sua avaliação ainda não foi liberada pelo psicólogo.",
                consulta,
            )

        if consulta.status_prova == "EM_ANDAMENTO":
            return (
                False,
                "Sua avaliação já foi iniciada e não pode ser reaberta.",
                consulta,
            )

        if consulta.status_prova == "FINALIZADA":
            return (
                False,
                "Você já concluiu a avaliação desta consulta.",
                consulta,
            )

        consulta.status_prova = "EM_ANDAMENTO"
        consulta.prova_iniciada_em = datetime.now(timezone.utc)
        consulta.pergunta_atual = 0
        await sessao.commit()
        await sessao.refresh(consulta)
        return True, "Prova iniciada.", consulta


async def gravar_resposta_prova(
    *,
    consulta_id: int,
    discord_id_paciente: int,
    numero_pergunta: int,
    letra_escolhida: str,
) -> tuple[bool, str, bool]:
    """
    Grava a resposta e avança o ponteiro da pergunta.

    Retorna (ok, mensagem, correta).
    """
    letra = letra_escolhida.strip().upper()
    if letra not in ("A", "B", "C"):
        return False, "Alternativa inválida.", False

    pergunta = obter_pergunta(numero_pergunta)
    if pergunta is None:
        return False, "Pergunta inválida.", False

    correta = letra == pergunta.resposta_correta

    async with async_session() as sessao:
        consulta = await sessao.get(ConsultaLaudo, consulta_id)
        if consulta is None:
            return False, "Consulta não encontrada.", False
        if consulta.discord_id_paciente != discord_id_paciente:
            return False, "Esta prova não é sua.", False
        if consulta.status != "ABERTA":
            return False, "A consulta não está mais aberta.", False
        if consulta.status_prova != "EM_ANDAMENTO":
            return False, "A prova não está em andamento.", False
        if consulta.pergunta_atual >= numero_pergunta:
            return False, "Esta pergunta já foi respondida.", False
        if consulta.pergunta_atual + 1 != numero_pergunta:
            return (
                False,
                "Responda as perguntas na ordem.",
                False,
            )

        sessao.add(
            RespostaProvaLaudo(
                consulta_id=consulta_id,
                numero_pergunta=numero_pergunta,
                resposta_escolhida=letra,
                correta=correta,
            )
        )
        consulta.pergunta_atual = numero_pergunta
        await sessao.commit()
        return True, "Resposta registrada.", correta


async def finalizar_prova_paciente(
    *,
    consulta_id: int,
    discord_id_paciente: int,
) -> tuple[bool, str, int, str]:
    """
    Soma acertos, grava parecer sugerido e marca a prova como FINALIZADA.

    Retorna (ok, mensagem, acertos, parecer_sugerido).
    """
    async with async_session() as sessao:
        consulta = await sessao.get(ConsultaLaudo, consulta_id)
        if consulta is None:
            return False, "Consulta não encontrada.", 0, "REPROVADO"
        if consulta.discord_id_paciente != discord_id_paciente:
            return False, "Esta prova não é sua.", 0, "REPROVADO"
        if consulta.status_prova != "EM_ANDAMENTO":
            return False, "A prova não está em andamento.", 0, "REPROVADO"
        if consulta.pergunta_atual < TOTAL_PERGUNTAS_PORTE:
            return (
                False,
                "Ainda faltam perguntas para concluir.",
                0,
                "REPROVADO",
            )

        resultado = await sessao.execute(
            select(RespostaProvaLaudo).where(
                RespostaProvaLaudo.consulta_id == consulta_id
            )
        )
        respostas = list(resultado.scalars().all())
        acertos = sum(1 for resposta in respostas if resposta.correta)
        parecer = (
            "APROVADO" if acertos >= NOTA_MINIMA_PORTE else "REPROVADO"
        )

        consulta.acertos_prova = acertos
        consulta.parecer_sugerido = parecer
        consulta.status_prova = "FINALIZADA"
        consulta.prova_finalizada_em = datetime.now(timezone.utc)
        await sessao.commit()
        return (
            True,
            f"Prova finalizada: {acertos}/{TOTAL_PERGUNTAS_PORTE}.",
            acertos,
            parecer,
        )


async def listar_respostas_da_consulta(
    consulta_id: int,
) -> list[RespostaProvaLaudo]:
    """Todas as respostas da prova, ordenadas pela pergunta."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(RespostaProvaLaudo)
            .where(RespostaProvaLaudo.consulta_id == consulta_id)
            .order_by(RespostaProvaLaudo.numero_pergunta.asc())
        )
        return list(resultado.scalars().all())


async def obter_resumo_prova_consulta(
    consulta_id: int,
) -> tuple[int | None, str | None, str]:
    """
    Lê acertos e parecer sugerido da consulta.

    Retorna (acertos, parecer_sugerido, status_prova).
    """
    async with async_session() as sessao:
        consulta = await sessao.get(ConsultaLaudo, consulta_id)
        if consulta is None:
            return None, None, "NAO_LIBERADA"
        return (
            consulta.acertos_prova,
            consulta.parecer_sugerido,
            consulta.status_prova,
        )


async def publicar_resultado_prova(
    *,
    guild: discord.Guild,
    consulta: ConsultaLaudo,
    acertos: int,
    parecer_sugerido: str,
) -> discord.Message | None:
    """
    Publica no canal da equipe o desempenho e o feedback de cada erro.

    O paciente não recebe gabarito durante a prova; só a equipe vê os erros.
    """
    canal_id = CANAIS.get("CANAL_RESULTADO_AVALIACAO") or 0
    canal = guild.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        registrador.warning(
            "[laudos] CANAL_RESULTADO_AVALIACAO não configurado ou ausente."
        )
        return None

    respostas = await listar_respostas_da_consulta(consulta.id)
    linhas_erros: list[str] = []
    for resposta in respostas:
        if resposta.correta:
            continue
        pergunta = obter_pergunta(resposta.numero_pergunta)
        if pergunta is None:
            continue
        escolhida = texto_da_opcao(pergunta, resposta.resposta_escolhida)
        correta = texto_da_opcao(pergunta, pergunta.resposta_correta)
        linhas_erros.append(
            f"**Q{resposta.numero_pergunta}** — escolheu "
            f"`{resposta.resposta_escolhida}`) {escolhida}\n"
            f"- Correta: `{pergunta.resposta_correta}`) {correta}\n"
            f"- {pergunta.feedback_erro}"
        )

    apto = parecer_sugerido == "APROVADO"
    cor = discord.Color.green() if apto else discord.Color.red()
    titulo_resultado = (
        "✅ Apto na prova teórica"
        if apto
        else "❌ Não apto na prova teórica"
    )

    corpo = (
        f"- **Paciente:** <@{consulta.discord_id_paciente}> "
        f"(`{consulta.discord_id_paciente}`)\n"
        f"- **Psicólogo:** <@{consulta.discord_id_psicologo}>\n"
        f"- **Consulta:** `#{consulta.id}`\n"
        f"- **Acertos:** `{acertos}/{TOTAL_PERGUNTAS_PORTE}` "
        f"(mínimo `{NOTA_MINIMA_PORTE}`)\n"
        f"- **Parecer sugerido:** `{parecer_sugerido}`"
    )

    componentes: list = [
        discord.ui.TextDisplay(f"# 🧠 Resultado — Prova de Porte\n{titulo_resultado}"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(corpo),
    ]

    if linhas_erros:
        componentes.append(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
        )
        componentes.append(
            discord.ui.TextDisplay("### Respostas incorretas")
        )
        # Discord limita tamanho; empacota em blocos
        bloco = ""
        for linha in linhas_erros:
            candidato = f"{bloco}\n\n{linha}" if bloco else linha
            if len(candidato) > 1800:
                componentes.append(discord.ui.TextDisplay(bloco))
                bloco = linha
            else:
                bloco = candidato
        if bloco:
            componentes.append(discord.ui.TextDisplay(bloco))
    else:
        componentes.append(
            discord.ui.TextDisplay("_Nenhum erro — todas as respostas corretas._")
        )

    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(
            *componentes,
            accent_color=cor,
        )
    )
    return await canal.send(view=layout)


def montar_texto_dm_resultado_prova(
    *,
    acertos: int | None,
    parecer_sugerido: str | None,
    status_prova: str,
    parecer_laudo: str,
    motivo: str,
) -> str:
    """Texto enviado na DM do paciente junto com o laudo."""
    if status_prova == "FINALIZADA" and acertos is not None:
        bloco_prova = (
            f"**Prova teórica:** `{acertos}/{TOTAL_PERGUNTAS_PORTE}` "
            f"(mínimo `{NOTA_MINIMA_PORTE}`)\n"
            f"**Parecer da prova:** `{parecer_sugerido or '—'}`"
        )
    elif status_prova == "NAO_LIBERADA":
        bloco_prova = "**Prova teórica:** não realizada nesta consulta."
    else:
        bloco_prova = f"**Prova teórica:** status `{status_prova}`."

    return (
        "# 📋 Laudo psicológico — CMS Valley\n"
        f"**Parecer final do psicólogo:** `{parecer_laudo}`\n"
        f"**Motivo:** {motivo}\n\n"
        f"{bloco_prova}\n\n"
        "-# Este documento é informativo para o processo de porte de arma "
        "no servidor."
    )
