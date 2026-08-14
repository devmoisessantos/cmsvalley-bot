"""Lógica de envio em massa / unitário e limite de uso do painel de notificação.

Não monta interface — só resolve destinos e dispara as DMs.
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

from src.utils.notificacao import (
    COR_AVISO,
    COR_INFO,
    COR_PUNICAO,
    COR_SUCESSO,
    enviar_dm_card,
)

logger = logging.getLogger(__name__)

# Limite por usuário que usa o painel (não por destinatário)
LIMITE_NOTIFICACOES_POR_HORA = 5
_janela_segundos = 3600

# Histórico simples em memória: id_executor → lista de timestamps unix
_historico_envios: dict[int, list[float]] = {}

CORES_DISPONIVEIS: dict[str, discord.Color] = {
    "info": COR_INFO,
    "sucesso": COR_SUCESSO,
    "aviso": COR_AVISO,
    "erro": COR_PUNICAO,
}

NOMES_CORES = {
    "info": "Info (azul)",
    "sucesso": "Sucesso (verde)",
    "aviso": "Aviso (laranja)",
    "erro": "Erro / alerta (vermelho)",
}


@dataclass
class SessaoNotificacao:
    """Estado do fluxo ephemeral de um diretor."""

    id_do_executor: int
    tipo_destino: str | None = None  # "membro" | "cargo"
    id_do_membro: int | None = None
    mencao_do_membro: str | None = None
    id_do_cargo: int | None = None
    nome_do_cargo: str | None = None
    titulo: str = ""
    linhas_corpo: list[str] = field(default_factory=list)
    chave_cor: str = "info"


_sessoes: dict[int, SessaoNotificacao] = {}


def obter_sessao(id_do_executor: int) -> SessaoNotificacao:
    if id_do_executor not in _sessoes:
        _sessoes[id_do_executor] = SessaoNotificacao(id_do_executor=id_do_executor)
    return _sessoes[id_do_executor]


def limpar_sessao(id_do_executor: int) -> None:
    _sessoes.pop(id_do_executor, None)


def registrar_envio_do_executor(id_do_executor: int) -> None:
    agora = time.time()
    lista = _historico_envios.setdefault(id_do_executor, [])
    lista.append(agora)
    # descarta timestamps fora da janela
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


def mensagem_esta_pronta(sessao: SessaoNotificacao) -> bool:
    titulo_ok = bool(sessao.titulo.strip())
    corpo_ok = bool(sessao.linhas_corpo) and any(
        linha.strip() for linha in sessao.linhas_corpo
    )
    return titulo_ok and corpo_ok


def cor_da_sessao(sessao: SessaoNotificacao) -> discord.Color:
    return CORES_DISPONIVEIS.get(sessao.chave_cor, COR_INFO)


def resumo_destino(sessao: SessaoNotificacao) -> str:
    if sessao.tipo_destino == "membro" and sessao.id_do_membro:
        return f"{sessao.mencao_do_membro or 'membro'} (`{sessao.id_do_membro}`)"
    if sessao.tipo_destino == "cargo" and sessao.id_do_cargo:
        nome = sessao.nome_do_cargo or "cargo"
        return f"Cargo **{nome}** (`{sessao.id_do_cargo}`)"
    return "*Ainda não definido*"


def resumo_corpo(sessao: SessaoNotificacao, limite: int = 280) -> str:
    if not sessao.linhas_corpo:
        return "*Vazio*"
    texto = "\n".join(sessao.linhas_corpo)
    if len(texto) > limite:
        return texto[: limite - 3] + "..."
    return texto


async def resolver_membros_destino(
    guilda: discord.Guild,
    sessao: SessaoNotificacao,
) -> list[discord.Member]:
    """Resolve a lista final de membros que receberão a DM."""
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
        # members já exclui bots na prática do cache; filtramos bots mesmo assim
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
    Envia o card da sessão para todos os destinos resolvidos.
    Conta um uso no limite por hora do executor (uma vez por clique em Enviar).
    """
    resultado = ResultadoEnvioNotificacao()
    membros = await resolver_membros_destino(guilda, sessao)
    resultado.total = len(membros)

    if not membros:
        return resultado

    titulo = sessao.titulo.strip()[:200]
    linhas = [linha for linha in sessao.linhas_corpo if linha.strip()]
    cor = cor_da_sessao(sessao)

    for indice, membro in enumerate(membros):
        enviou = await enviar_dm_card(
            membro,
            titulo=titulo,
            linhas=linhas,
            cor=cor,
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
