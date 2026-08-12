"""
Recuperação de dados a partir dos canais de LOG do Discord.

Fase 1: LOG_PLANTAO → tabela log_plantao
(expansível para recrutamentos, punições, etc.)
"""

from __future__ import annotations

import logging
import re
from datetime import timezone

import discord
from sqlalchemy import select

from src.config import CANAIS
from src.database.connection import async_session
from src.database.models import LogPlantao
from src.plantao.plantao_logger import EVENTOS_PLANTAO

logger = logging.getLogger(__name__)

# titulo do card → chave do evento
_TITULO_PARA_EVENTO: dict[str, str] = {}
for chave_evento, (titulo, _cor) in EVENTOS_PLANTAO.items():
    _TITULO_PARA_EVENTO[titulo.strip().lower()] = chave_evento
    # sem emoji no início
    sem_emoji = re.sub(r"^[^\w]+", "", titulo).strip().lower()
    if sem_emoji:
        _TITULO_PARA_EVENTO[sem_emoji] = chave_evento


def extrair_texto_mensagem(mensagem: discord.Message) -> str:
    """Junta content, embeds e Components V2 (TextDisplay / Section)."""
    pedacos: list[str] = []

    if mensagem.content:
        pedacos.append(mensagem.content)

    for embed in mensagem.embeds:
        if embed.title:
            pedacos.append(embed.title)
        if embed.description:
            pedacos.append(embed.description)
        for campo in embed.fields:
            pedacos.append(f"{campo.name}: {campo.value}")

    def percorrer(objeto: object) -> None:
        if objeto is None:
            return
        if isinstance(objeto, str):
            if objeto.strip():
                pedacos.append(objeto)
            return

        for nome_atributo in ("content", "text", "label", "title", "description"):
            valor = getattr(objeto, nome_atributo, None)
            if isinstance(valor, str) and valor.strip():
                pedacos.append(valor)

        for nome_filho in ("children", "components", "items"):
            filhos = getattr(objeto, nome_filho, None)
            if filhos:
                for filho in filhos:
                    percorrer(filho)

        for nome_extra in ("item", "accessory", "accessory"):
            percorrer(getattr(objeto, nome_extra, None))

    for componente in mensagem.components:
        percorrer(componente)

    return "\n".join(pedacos)


def parsear_hms_para_segundos(texto: str) -> int | None:
    """Aceita HH:MM:SS ou H:MM:SS."""
    match = re.search(r"(\d{1,3}):(\d{2}):(\d{2})", texto)
    if not match:
        return None
    horas = int(match.group(1))
    minutos = int(match.group(2))
    segundos = int(match.group(3))
    return horas * 3600 + minutos * 60 + segundos


def parsear_discord_id(texto: str) -> int | None:
    match = re.search(r"<@!?(\d{15,25})>", texto)
    if match:
        return int(match.group(1))
    match = re.search(r"\*\*Membro:\*\*\s*`(\d{15,25})`", texto)
    if match:
        return int(match.group(1))
    match = re.search(r"Membro:\s*`(\d{15,25})`", texto)
    if match:
        return int(match.group(1))
    return None


def parsear_id_fivem(texto: str) -> str | None:
    match = re.search(r"ID FiveM:\*\*\s*`([^`]+)`", texto, re.I)
    if not match:
        match = re.search(r"ID FiveM:\s*`([^`]+)`", texto, re.I)
    if not match:
        return None
    valor = match.group(1).strip()
    if valor.upper() in ("N/A", "NA", "-", ""):
        return None
    return valor[:20]


def detectar_evento(texto: str) -> str | None:
    """Tenta achar a chave do evento pelo título do card no texto."""
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    candidatos = linhas[:5]
    # também linhas que começam com #
    for linha in linhas:
        if linha.startswith("#"):
            candidatos.insert(0, linha.lstrip("#").strip())

    for candidato in candidatos:
        chave = candidato.strip().lower()
        if chave in _TITULO_PARA_EVENTO:
            return _TITULO_PARA_EVENTO[chave]
        # match parcial
        for titulo, evento in _TITULO_PARA_EVENTO.items():
            if titulo and titulo in chave:
                return evento
            if chave and chave in titulo:
                return evento
    return None


def parsear_mensagem_plantao(
    mensagem: discord.Message,
) -> dict | None:
    """
    Extrai campos de um log de plantão.
    Retorna None se não parecer log válido.
    """
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return None

    discord_id = parsear_discord_id(texto)
    if discord_id is None:
        return None

    evento = detectar_evento(texto) or "IMPORTADO_LOG"
    id_fivem = parsear_id_fivem(texto)

    duracao = None
    match_dur = re.search(r"Dura[cç][aã]o:\*\*\s*([0-9:]+)", texto, re.I)
    if not match_dur:
        match_dur = re.search(r"Dura[cç][aã]o:\s*\*?\*?\s*([0-9:]+)", texto, re.I)
    if match_dur:
        duracao = parsear_hms_para_segundos(match_dur.group(1))

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    return {
        "discord_id": discord_id,
        "evento": evento[:30],
        "id_fivem": id_fivem,
        "duracao_segundos": duracao,
        "criado_em": criado_em,
        "detalhes": f"import_log:{mensagem.id}",
        "message_id": mensagem.id,
    }


async def mensagem_ja_importada(message_id: int) -> bool:
    marca = f"import_log:{message_id}"
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(LogPlantao.id).where(LogPlantao.detalhes == marca).limit(1)
        )
        return resultado.scalar_one_or_none() is not None


async def importar_log_plantao_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    """
    Lê o histórico do canal LOG_PLANTAO e grava em log_plantao.

    limite: máximo de mensagens a ler (None = até esgotar / limite da API em loop).
    """
    lidas = 0
    importadas = 0
    ignoradas = 0
    ja_existiam = 0
    erros = 0

    kwargs = {"limit": limite} if limite else {"limit": None}

    async for mensagem in canal.history(limit=kwargs["limit"], oldest_first=True):
        lidas += 1
        if apenas_bot_id is not None and mensagem.author.id != apenas_bot_id:
            ignoradas += 1
            continue

        try:
            if await mensagem_ja_importada(mensagem.id):
                ja_existiam += 1
                continue

            dados = parsear_mensagem_plantao(mensagem)
            if dados is None:
                ignoradas += 1
                continue

            async with async_session() as sessao:
                sessao.add(
                    LogPlantao(
                        id_fivem=dados["id_fivem"],
                        discord_id=dados["discord_id"],
                        evento=dados["evento"],
                        canal_id=None,
                        duracao_segundos=dados["duracao_segundos"],
                        detalhes=dados["detalhes"],
                        criado_em=dados["criado_em"],
                    )
                )
                await sessao.commit()
            importadas += 1
        except Exception as erro:
            erros += 1
            logger.exception("Falha ao importar msg %s: %s", mensagem.id, erro)

        if lidas % 100 == 0:
            logger.info(
                "Recuperação plantão: lidas=%s importadas=%s",
                lidas,
                importadas,
            )

    return {
        "lidas": lidas,
        "importadas": importadas,
        "ignoradas": ignoradas,
        "ja_existiam": ja_existiam,
        "erros": erros,
        "canal_id": canal.id,
    }


def id_canal_log_plantao() -> int | None:
    valor = CANAIS.get("LOG_PLANTAO")
    return int(valor) if valor else None
