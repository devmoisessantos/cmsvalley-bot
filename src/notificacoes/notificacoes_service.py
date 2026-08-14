"""Lógica de destino, limite de uso e envio da notificação (LayoutView completa).

O conteúdo do card vem do rascunho do construtor (templates_modelo).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import (
    dataclass,
    field,
)

import discord

from src.templates.templates_modelo import (
    limpar_rascunho,
    montar_preview,
    obter_rascunho,
    resumo_dos_blocos,
)
from src.utils.notificacao import enviar_dm_view

logger = logging.getLogger(__name__)

LIMITE_NOTIFICACOES_POR_HORA = 5
_janela_segundos = 3600

_historico_envios: dict[int, list[float]] = {}


@dataclass
class SessaoNotificacao:
    """Só o destino do fluxo (o card fica no rascunho de templates_modelo)."""

    id_do_executor: int
    tipo_destino: str | None = None  # "membro" | "cargo"
    id_do_membro: int | None = None
    mencao_do_membro: str | None = None
    id_do_cargo: int | None = None
    nome_do_cargo: str | None = None


_sessoes: dict[int, SessaoNotificacao] = {}


def obter_sessao(id_do_executor: int) -> SessaoNotificacao:
    if id_do_executor not in _sessoes:
        _sessoes[id_do_executor] = SessaoNotificacao(id_do_executor=id_do_executor)
    return _sessoes[id_do_executor]


def limpar_sessao(id_do_executor: int) -> None:
    _sessoes.pop(id_do_executor, None)
    limpar_rascunho(id_do_executor)


def registrar_envio_do_executor(id_do_executor: int) -> None:
    agora = time.time()
    lista = _historico_envios.setdefault(id_do_executor, [])
    lista.append(agora)
    _historico_envios[id_do_executor] = [
        marca for marca in lista if agora - marca < _janela_segundos
    ]


def quantidade_envios_na_hora(id_do_executor: int) -> int:
    agora = time.time()
    lista = _historico_envios.get(id_do_executor, [])
    validos = [marca for marca in lista if agora - marca < _janela_segundos]
    _historico_envios[id_do_executor] = validos
    return len(validos)


def ainda_pode_enviar(id_do_executor: int) -> bool:
    return quantidade_envios_na_hora(id_do_executor) < LIMITE_NOTIFICACOES_POR_HORA


def destino_esta_pronto(sessao: SessaoNotificacao) -> bool:
    if sessao.tipo_destino == "membro":
        return sessao.id_do_membro is not None
    if sessao.tipo_destino == "cargo":
        return sessao.id_do_cargo is not None
    return False


def resumo_destino(sessao: SessaoNotificacao) -> str:
    if sessao.tipo_destino == "membro" and sessao.id_do_membro:
        return f"{sessao.mencao_do_membro or 'membro'} (`{sessao.id_do_membro}`)"
    if sessao.tipo_destino == "cargo" and sessao.id_do_cargo:
        nome = sessao.nome_do_cargo or "cargo"
        return f"Cargo **{nome}** (`{sessao.id_do_cargo}`)"
    return "*Ainda não definido*"


def rascunho_tem_conteudo(id_do_usuario: int) -> bool:
    return bool(obter_rascunho(id_do_usuario).blocos)


async def resolver_membros_destino(
    guilda: discord.Guild,
    sessao: SessaoNotificacao,
) -> list[discord.Member]:
    if sessao.tipo_destino == "membro" and sessao.id_do_membro:
        membro = guilda.get_member(sessao.id_do_membro)
        if membro is None:
            try:
                membro = await guilda.fetch_member(sessao.id_do_membro)
            except (discord.NotFound, discord.HTTPException):
                return []
        return [membro] if membro else []

    if sessao.tipo_destino == "cargo" and sessao.id_do_cargo:
        cargo = guilda.get_role(sessao.id_do_cargo)
        if cargo is None:
            return []
        return [membro for membro in cargo.members if not membro.bot]

    return []


@dataclass
class ResultadoEnvioNotificacao:
    total: int = 0
    enviados: int = 0
    falhas: int = 0
    ids_falha: list[int] = field(default_factory=list)


async def enviar_notificacao_da_sessao(
    guilda: discord.Guild,
    sessao: SessaoNotificacao,
    *,
    atraso_entre_envios: float = 0.35,
) -> ResultadoEnvioNotificacao:
    """
    Monta o LayoutView do rascunho (igual ao /templates) e envia na DM
    de cada destinatário via enviar_dm_view.
    """
    resultado = ResultadoEnvioNotificacao()
    membros = await resolver_membros_destino(guilda, sessao)
    resultado.total = len(membros)
    if not membros:
        return resultado

    rascunho = obter_rascunho(sessao.id_do_executor)
    if not rascunho.blocos:
        return resultado

    titulo_log = "Notificação por DM (painel)"
    for bloco in rascunho.blocos:
        if bloco.tipo == "titulo" and bloco.texto.strip():
            titulo_log = bloco.texto.strip()[:120]
            break
        if bloco.tipo in ("secao", "texto") and bloco.texto.strip():
            titulo_log = bloco.texto.strip().splitlines()[0][:120]
            break

    linhas_resumo = [
        f"Blocos: {len(rascunho.blocos)}",
        resumo_dos_blocos(rascunho)[:400],
    ]

    for indice, membro in enumerate(membros):
        view_dm = montar_preview(rascunho, guilda)
        enviou = await enviar_dm_view(
            membro,
            view_dm,
            titulo_log=titulo_log,
            linhas_resumo=linhas_resumo,
            guilda=guilda,
            registrar_log=True,
        )
        if enviou:
            resultado.enviados += 1
        else:
            resultado.falhas += 1
            resultado.ids_falha.append(membro.id)

        if indice < len(membros) - 1 and atraso_entre_envios > 0:
            await asyncio.sleep(atraso_entre_envios)

    registrar_envio_do_executor(sessao.id_do_executor)
    return resultado
