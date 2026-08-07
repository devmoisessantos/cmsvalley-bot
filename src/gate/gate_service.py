# src/gate/gate_service.py
"""
Lógica de banco e regras de negócio dos eventos GATE.

Painéis e cogs só chamam estas funções — não mexem no SQL direto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import discord
from sqlalchemy import select

from src.config import (
    CARGOS_CRIACAO_EVENTO_GATE,
    HIERARQUIA_GATE,
)
from src.database.connection import async_session
from src.database.models import (
    EventosGate,
    Presenca,
    agora,
)


@dataclass
class ResultadoPresenca:
    """Retorno padronizado da confirmação/cancelamento de presença."""

    ok: bool
    mensagem: str
    presenca: Presenca | None = None


# ---------------------------------------------------------------------------
# Permissões
# ---------------------------------------------------------------------------


def tem_permissao_criar_evento(membro: discord.Member) -> bool:
    """True se o membro pode criar/encerrar eventos GATE."""
    nomes_dos_cargos = {cargo.name for cargo in membro.roles}
    return bool(nomes_dos_cargos.intersection(CARGOS_CRIACAO_EVENTO_GATE))


def membro_pertence_a_gate(membro: discord.Member) -> bool:
    """True se o membro tem algum cargo da hierarquia GATE."""
    nomes_dos_cargos = {cargo.name for cargo in membro.roles}
    return bool(nomes_dos_cargos.intersection(HIERARQUIA_GATE))


# Alias antigo (imports legados / clareza)
tem_permissao_gate = tem_permissao_criar_evento


# ---------------------------------------------------------------------------
# Eventos
# ---------------------------------------------------------------------------


async def buscar_evento_por_id(evento_id: int) -> EventosGate | None:
    """Busca um evento pelo ID. Retorna None se não existir."""
    async with async_session() as sessao:
        return await sessao.get(EventosGate, evento_id)


async def criar_evento(
    tipo: str,
    titulo: str,
    data_evento: str,
    horario: str,
    limite_participantes: int,
    adversario: str | None,
    criado_por: int,
    responsavel_id: int,
) -> EventosGate:
    """Cria um evento GATE com status aberto e devolve o registro salvo."""
    async with async_session() as sessao:
        evento = EventosGate(
            tipo=tipo,
            titulo=titulo,
            data_evento=data_evento,
            horario=horario,
            limite_participantes=limite_participantes,
            adversario=adversario,
            status="aberto",
            criado_por=criado_por,
            responsavel_id=responsavel_id,
        )
        sessao.add(evento)
        await sessao.commit()
        await sessao.refresh(evento)
        return evento


async def listar_eventos_abertos() -> list[EventosGate]:
    """Lista todos os eventos ainda abertos, do mais antigo ao mais novo."""
    async with async_session() as sessao:
        resultado_consulta = await sessao.execute(
            select(EventosGate)
            .where(EventosGate.status == "aberto")
            .order_by(EventosGate.created_at)
        )
        return list(resultado_consulta.scalars().all())


async def listar_ultimos_eventos(limite: int = 10) -> list[EventosGate]:
    """Lista os últimos eventos (abertos ou encerrados), mais recentes primeiro."""
    async with async_session() as sessao:
        resultado_consulta = await sessao.execute(
            select(EventosGate).order_by(EventosGate.created_at.desc()).limit(limite)
        )
        return list(resultado_consulta.scalars().all())


async def encerrar_evento(evento_id: int) -> EventosGate | None:
    """
    Marca o evento como encerrado.

    Retorna o evento atualizado, ou None se não existir / já estiver encerrado.
    """
    async with async_session() as sessao:
        evento_no_banco = await sessao.get(EventosGate, evento_id)

        if evento_no_banco is None:
            return None
        if evento_no_banco.status == "encerrado":
            return None

        evento_no_banco.status = "encerrado"
        evento_no_banco.closed_at = agora()
        await sessao.commit()
        await sessao.refresh(evento_no_banco)
        return evento_no_banco


async def salvar_log_message_id(evento_id: int, message_id: int):
    """Guarda o ID da mensagem de log do evento no banco."""
    async with async_session() as sessao:
        evento_no_banco = await sessao.get(EventosGate, evento_id)
        if evento_no_banco is None:
            return
        evento_no_banco.log_message_id = message_id
        await sessao.commit()


# ---------------------------------------------------------------------------
# Presenças
# ---------------------------------------------------------------------------


async def listar_presencas(evento_id: int) -> list[Presenca]:
    """Lista todas as presenças confirmadas de um evento."""
    async with async_session() as sessao:
        resultado_consulta = await sessao.execute(
            select(Presenca).where(Presenca.evento_id == evento_id)
        )
        return list(resultado_consulta.scalars().all())


async def contar_presencas(evento_id: int) -> int:
    """Quantidade de presenças confirmadas neste evento."""
    presencas = await listar_presencas(evento_id)
    return len(presencas)


async def confirmar_presenca(
    evento_id: int,
    discord_id: int,
    id_fivem: int,
    *,
    membro_e_da_gate: bool = True,
) -> ResultadoPresenca:
    """
    Registra a presença de um membro em um evento.

    Regras:
    - evento precisa existir e estar aberto
    - membro precisa ser da hierarquia GATE
    - não pode já ter confirmado
    - se houver limite (> 0), não pode ultrapassar
    """
    async with async_session() as sessao:
        evento = await sessao.get(EventosGate, evento_id)

        if evento is None:
            return ResultadoPresenca(
                ok=False,
                mensagem="Este evento não existe mais.",
            )

        if evento.status != "aberto":
            return ResultadoPresenca(
                ok=False,
                mensagem="Este evento já foi encerrado. Não é possível confirmar presença.",
            )

        if not membro_e_da_gate:
            return ResultadoPresenca(
                ok=False,
                mensagem="Apenas membros da hierarquia GATE podem confirmar presença.",
            )

        resultado_consulta = await sessao.execute(
            select(Presenca).where(
                Presenca.evento_id == evento_id,
                Presenca.discord_id == discord_id,
            )
        )
        ja_existe = resultado_consulta.scalar_one_or_none()
        if ja_existe is not None:
            return ResultadoPresenca(
                ok=False,
                mensagem="Você já confirmou presença neste evento.",
            )

        if evento.limite_participantes > 0:
            resultado_total = await sessao.execute(
                select(Presenca).where(Presenca.evento_id == evento_id)
            )
            total_atual = len(list(resultado_total.scalars().all()))
            if total_atual >= evento.limite_participantes:
                return ResultadoPresenca(
                    ok=False,
                    mensagem=(
                        f"O limite de **{evento.limite_participantes}** "
                        "participantes foi atingido."
                    ),
                )

        presenca = Presenca(
            evento_id=evento_id,
            discord_id=discord_id,
            id_fivem=id_fivem,
            confirmado=True,
        )
        sessao.add(presenca)
        await sessao.commit()

        return ResultadoPresenca(
            ok=True,
            mensagem=f"Presença confirmada com ID FiveM `{id_fivem}`.",
            presenca=presenca,
        )


async def cancelar_presenca(evento_id: int, discord_id: int) -> ResultadoPresenca:
    """
    Remove a presença do membro neste evento.

    Não permite cancelar se o evento já estiver encerrado.
    """
    async with async_session() as sessao:
        evento = await sessao.get(EventosGate, evento_id)
        if evento is None:
            return ResultadoPresenca(ok=False, mensagem="Este evento não existe mais.")

        if evento.status != "aberto":
            return ResultadoPresenca(
                ok=False,
                mensagem="Este evento já foi encerrado. Não é possível cancelar a presença.",
            )

        resultado_consulta = await sessao.execute(
            select(Presenca).where(
                Presenca.evento_id == evento_id,
                Presenca.discord_id == discord_id,
            )
        )
        presenca = resultado_consulta.scalar_one_or_none()

        if presenca is None:
            return ResultadoPresenca(
                ok=False,
                mensagem="Você não tinha presença registrada neste evento.",
            )

        await sessao.delete(presenca)
        await sessao.commit()

        return ResultadoPresenca(
            ok=True,
            mensagem="Sua presença foi cancelada.",
        )


async def listar_presencas_do_membro(
    discord_id: int,
) -> list[tuple[EventosGate, Presenca]]:
    """Presenças do membro em eventos ainda abertos."""
    async with async_session() as sessao:
        resultado_consulta = await sessao.execute(
            select(Presenca, EventosGate)
            .join(EventosGate, Presenca.evento_id == EventosGate.id)
            .where(
                Presenca.discord_id == discord_id,
                EventosGate.status == "aberto",
            )
            .order_by(EventosGate.created_at)
        )
        pares = []
        for presenca, evento in resultado_consulta.all():
            pares.append((evento, presenca))
        return pares


# ---------------------------------------------------------------------------
# Validação dos campos do modal
# ---------------------------------------------------------------------------


def validar_data(valor: str) -> tuple[bool, str]:
    """Aceita apenas DD/MM/AAAA com data real. Retorna (valido, mensagem_de_erro)."""
    valor_limpo = valor.strip()

    try:
        data_informada = datetime.strptime(valor_limpo, "%d/%m/%Y")
    except ValueError:
        return (
            False,
            "Data inválida. Use o formato `DD/MM/AAAA` (ex: `15/06/2026`).",
        )

    data_de_hoje = datetime.now().date()
    if data_informada.date() < data_de_hoje:
        return (
            False,
            f"A data `{valor_limpo}` já passou. Informe uma data futura.",
        )

    return True, ""


def validar_horario(valor: str) -> tuple[bool, str]:
    """Aceita apenas HH:MM em formato 24h."""
    valor_limpo = valor.strip()

    try:
        datetime.strptime(valor_limpo, "%H:%M")
    except ValueError:
        return (
            False,
            "Horário inválido. Use o formato `HH:MM`, 24h (ex: `20:00`).",
        )

    return True, ""


def validar_limite(valor: str) -> tuple[bool, str, int]:
    """0 = sem limite. Retorna (valido, mensagem_de_erro, valor_convertido)."""
    valor_limpo = (valor or "0").strip()

    if not valor_limpo.isdigit():
        return False, "Limite precisa ser um número inteiro (0 = sem limite).", 0

    limite_inteiro = int(valor_limpo)

    if limite_inteiro < 0:
        return False, "Limite não pode ser negativo.", 0

    if limite_inteiro > 25:
        return (
            False,
            "Limite muito alto (máximo 25). Use `0` para sem limite.",
            0,
        )

    return True, "", limite_inteiro


def validar_adversario(valor: str | None) -> tuple[bool, str]:
    """Usado no FacXFac — garante que o nome do adversário não veio em branco."""
    if valor is None or not valor.strip():
        return False, "O nome do adversário não pode ficar em branco."
    return True, ""
