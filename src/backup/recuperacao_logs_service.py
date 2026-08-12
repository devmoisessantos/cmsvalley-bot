"""
Recuperação de dados a partir dos canais de LOG do Discord.

Fase 1: LOG_PLANTAO → tabela log_plantao
(expansível para recrutamentos, punições, etc.)
"""

from __future__ import annotations

import logging
import re
from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select

from src.config import CANAIS
from src.database.connection import async_session
from src.database.models import (
    Chamada,
    LogPlantao,
    Punicao,
    Recrutamento,
    Usuario,
)
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


# ── Helpers genéricos ────────────────────────────────────────────────────


def _ids_no_texto(texto: str) -> list[int]:
    return [int(x) for x in re.findall(r"<@!?(\d{15,25})>", texto)]


def _id_backtick_apos(rotulo: str, texto: str) -> int | None:
    """Procura `123` perto de um rótulo (ex.: Membro recrutado)."""
    padrao = rf"{rotulo}[^`\n]*`(\d{{15,25}})`"
    match = re.search(padrao, texto, re.I)
    return int(match.group(1)) if match else None


def _campo_backtick(rotulo: str, texto: str) -> str | None:
    padrao = rf"{rotulo}[^`\n]*`([^`]+)`"
    match = re.search(padrao, texto, re.I)
    if not match:
        return None
    return match.group(1).strip()


async def _garantir_usuario(
    sessao,
    discord_id: int,
    *,
    id_fivem: str | None = None,
    status: str = "APROVADO",
) -> None:
    resultado = await sessao.execute(
        select(Usuario).where(Usuario.discord_id == int(discord_id))
    )
    usuario = resultado.scalar_one_or_none()
    if usuario is None:
        sessao.add(
            Usuario(
                discord_id=int(discord_id),
                id_fivem=id_fivem,
                status=status,
                ja_foi_aprovado=status == "APROVADO",
            )
        )
    else:
        if id_fivem and not usuario.id_fivem:
            usuario.id_fivem = id_fivem
        if status == "APROVADO":
            usuario.status = "APROVADO"
            usuario.ja_foi_aprovado = True


# ── LOG_RECRUTAMENTOS ────────────────────────────────────────────────────


def parsear_mensagem_recrutamento(mensagem: discord.Message) -> dict | None:
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return None

    lower = texto.lower()
    # só conclusões / manuais / realizados — não só "iniciado" sem aprovação
    if (
        "recrutamento iniciado" in lower
        and "realizado" not in lower
        and "manual" not in lower
    ):
        # ainda importa como ESTUDANDO? melhor só APROVADO
        status = "ESTUDANDO"
    elif "manual" in lower or "realizado" in lower or "recrutamento" in lower:
        status = "APROVADO"
    else:
        return None

    if (
        "novo recrutamento" not in lower
        and "recrutamento manual" not in lower
        and "recrutamento realizado" not in lower
    ):
        # aceita se tiver os campos típicos
        if "membro recrutado" not in lower:
            return None

    candidato = _id_backtick_apos("Membro recrutado", texto)
    if candidato is None:
        ids = _ids_no_texto(texto)
        candidato = ids[0] if ids else None
    if candidato is None:
        return None

    recrutador = _id_backtick_apos("Recrutado por", texto)
    if recrutador is None:
        ids = _ids_no_texto(texto)
        if len(ids) >= 2:
            recrutador = ids[1]
    if recrutador is None:
        recrutador = 0

    id_fivem = _campo_backtick("ID FiveM", texto)
    if id_fivem and id_fivem.upper() in ("N/A", "NA", "-"):
        id_fivem = None

    cargo = _campo_backtick("Cargo", texto)
    # cargo às vezes é menção de role <@&id>
    if not cargo:
        match = re.search(r"Cargo:\*\*\s*<@&(\d+)>", texto)
        if not match:
            match = re.search(r"Cargo:\s*<@&(\d+)>", texto)
        cargo = match.group(1) if match else None

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    # títulos "Iniciado" → não marcar APROVADO
    if "iniciado" in lower and "manual" not in lower and "realizado" not in lower:
        status = "ESTUDANDO"

    return {
        "discord_id_candidato": int(candidato),
        "discord_id_recrutador": int(recrutador),
        "id_fivem": (id_fivem or None),
        "cargo_final": (str(cargo) if cargo else "IMPORTADO"),
        "status": status,
        "criado_em": criado_em,
        "message_id": mensagem.id,
    }


async def recrutamento_ja_importado(
    discord_id_candidato: int,
    criado_em: datetime,
) -> bool:
    """Evita duplicar: mesmo candidato + mesmo timestamp da mensagem."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Recrutamento.id)
            .where(
                Recrutamento.discord_id_candidato == int(discord_id_candidato),
                Recrutamento.data_inicio == criado_em,
            )
            .limit(1)
        )
        return resultado.scalar_one_or_none() is not None


def _normalizar_cargo_final(cargo_bruto: str | None) -> str:
    """cargo_final no banco é VARCHAR(30)."""
    if not cargo_bruto:
        return "IMPORTADO"
    texto = str(cargo_bruto).strip()
    lower = texto.lower()
    if "param" in lower:
        return "PARAMEDICO"
    if "enferm" in lower:
        return "ENFERMEIRO"
    # ID numérico de cargo → genérico curto
    if texto.isdigit():
        return "IMPORTADO"
    return texto[:30]


async def importar_log_recrutamentos_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    lidas = importadas = ignoradas = ja_existiam = erros = 0

    async for mensagem in canal.history(limit=limite, oldest_first=True):
        lidas += 1
        if apenas_bot_id is not None and mensagem.author.id != apenas_bot_id:
            ignoradas += 1
            continue
        try:
            dados = parsear_mensagem_recrutamento(mensagem)
            if dados is None:
                ignoradas += 1
                continue

            if await recrutamento_ja_importado(
                dados["discord_id_candidato"],
                dados["criado_em"],
            ):
                ja_existiam += 1
                continue

            cargo_final = _normalizar_cargo_final(dados.get("cargo_final"))
            id_fivem = dados.get("id_fivem")
            if id_fivem:
                id_fivem = str(id_fivem)[:20]
            status = str(dados["status"])[:30]

            async with async_session() as sessao:
                await _garantir_usuario(
                    sessao,
                    dados["discord_id_candidato"],
                    id_fivem=id_fivem,
                    status="APROVADO" if status == "APROVADO" else "ESTUDANDO",
                )
                sessao.add(
                    Recrutamento(
                        id_fivem=id_fivem,
                        discord_id_candidato=dados["discord_id_candidato"],
                        discord_id_recrutador=dados["discord_id_recrutador"],
                        data_inicio=dados["criado_em"],
                        data_fim=dados["criado_em"] if status == "APROVADO" else None,
                        status=status,
                        cargo_final=cargo_final,
                    )
                )
                await sessao.commit()
            importadas += 1
        except Exception as erro:
            erros += 1
            logger.exception("import recrutamento msg %s: %s", mensagem.id, erro)

    return {
        "lidas": lidas,
        "importadas": importadas,
        "ignoradas": ignoradas,
        "ja_existiam": ja_existiam,
        "erros": erros,
        "canal_id": canal.id,
    }


# ── LOG_PUNICOES ─────────────────────────────────────────────────────────


def parsear_mensagem_punicao(mensagem: discord.Message) -> dict | None:
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return None
    lower = texto.lower()
    if (
        "novo registro de punição" not in lower
        and "novo registro de punicao" not in lower
    ):
        return None  # ignora remoções nesta fase

    alvo = _id_backtick_apos("Membro", texto)
    if alvo is None:
        ids = _ids_no_texto(texto)
        alvo = ids[0] if ids else None
    if alvo is None:
        return None

    executor = _id_backtick_apos("Responsável", texto) or _id_backtick_apos(
        "Responsavel", texto
    )
    if executor is None:
        ids = _ids_no_texto(texto)
        executor = ids[1] if len(ids) >= 2 else 0

    id_fivem = _campo_backtick("ID FiveM", texto)
    if id_fivem and id_fivem.upper() in ("N/A", "NA", "-", "—"):
        id_fivem = None

    motivo_match = re.search(r"Motivo:\*\*\s*(.+?)(?:\n|$)", texto)
    if not motivo_match:
        motivo_match = re.search(r"Motivo:\s*(.+?)(?:\n|$)", texto)
    motivo = (motivo_match.group(1).strip() if motivo_match else "Importado do log")[
        :1500
    ]

    cargo_id = 0
    cargo_nome = "Punição (importada)"
    match_role = re.search(r"Puni[cç][aã]o:\*\*\s*<@&(\d+)>", texto)
    if not match_role:
        match_role = re.search(r"Puni[cç][aã]o:\s*<@&(\d+)>", texto)
    if match_role:
        cargo_id = int(match_role.group(1))
        cargo_nome = f"role:{cargo_id}"

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    return {
        "discord_id": int(alvo),
        "executor_id": int(executor),
        "id_fivem": id_fivem,
        "motivo": motivo,
        "cargo_id": cargo_id,
        "cargo_nome": cargo_nome[:80],
        "criado_em": criado_em,
        "message_id": mensagem.id,
        "channel_id": mensagem.channel.id if mensagem.channel else None,
    }


async def punicao_ja_importada(message_id: int) -> bool:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Punicao.id).where(Punicao.message_id == int(message_id)).limit(1)
        )
        return resultado.scalar_one_or_none() is not None


async def importar_log_punicoes_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    lidas = importadas = ignoradas = ja_existiam = erros = 0

    async for mensagem in canal.history(limit=limite, oldest_first=True):
        lidas += 1
        if apenas_bot_id is not None and mensagem.author.id != apenas_bot_id:
            ignoradas += 1
            continue
        try:
            if await punicao_ja_importada(mensagem.id):
                ja_existiam += 1
                continue
            dados = parsear_mensagem_punicao(mensagem)
            if dados is None:
                ignoradas += 1
                continue
            async with async_session() as sessao:
                sessao.add(
                    Punicao(
                        discord_id=dados["discord_id"],
                        id_fivem=dados["id_fivem"],
                        cargo_id=dados["cargo_id"],
                        cargo_nome=dados["cargo_nome"],
                        motivo=dados["motivo"],
                        executor_id=dados["executor_id"],
                        ativa=True,
                        channel_id=dados["channel_id"],
                        message_id=dados["message_id"],
                        criada_em=dados["criado_em"],
                    )
                )
                await sessao.commit()
            importadas += 1
        except Exception as erro:
            erros += 1
            logger.exception("import punicao msg %s: %s", mensagem.id, erro)

    return {
        "lidas": lidas,
        "importadas": importadas,
        "ignoradas": ignoradas,
        "ja_existiam": ja_existiam,
        "erros": erros,
        "canal_id": canal.id,
    }


# ── LOG_CHAMADAS ─────────────────────────────────────────────────────────


def parsear_mensagem_chamada(mensagem: discord.Message) -> dict | None:
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return None
    if (
        "registro de chamada" not in texto.lower()
        and "chamada realizada" not in texto.lower()
    ):
        return None

    doutor = None
    match = re.search(r"Respons[aá]vel:\s*<@!?(\d+)>", texto)
    if match:
        doutor = int(match.group(1))
    if doutor is None:
        ids = _ids_no_texto(texto)
        doutor = ids[0] if ids else None
    if doutor is None:
        return None

    def _int_campo(*nomes: str) -> int:
        for nome in nomes:
            match = re.search(rf"{nome}[^0-9]*(\d+)", texto, re.I)
            if match:
                return int(match.group(1))
        return 0

    total_ems = _int_campo(r"Total\s*`?/ems`?", r"Total /ems")
    total_toggle = _int_campo("Toggle")
    total_presentes = _int_campo("Presentes")
    total_ausentes = _int_campo("Faltas", "Ausentes")

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    return {
        "doutor_id": int(doutor),
        "total_medicos_ems": total_ems,
        "total_toggle_ligado": total_toggle,
        "total_presentes": total_presentes,
        "total_ausentes": total_ausentes,
        "criado_em": criado_em,
        "message_id": mensagem.id,
    }


async def chamada_ja_importada(doutor_id: int, criado_em: datetime) -> bool:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Chamada.id)
            .where(
                Chamada.doutor_id == int(doutor_id),
                Chamada.criada_em == criado_em,
            )
            .limit(1)
        )
        return resultado.scalar_one_or_none() is not None


async def importar_log_chamadas_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    lidas = importadas = ignoradas = ja_existiam = erros = 0

    async for mensagem in canal.history(limit=limite, oldest_first=True):
        lidas += 1
        if apenas_bot_id is not None and mensagem.author.id != apenas_bot_id:
            ignoradas += 1
            continue
        try:
            dados = parsear_mensagem_chamada(mensagem)
            if dados is None:
                ignoradas += 1
                continue
            if await chamada_ja_importada(dados["doutor_id"], dados["criado_em"]):
                ja_existiam += 1
                continue
            async with async_session() as sessao:
                sessao.add(
                    Chamada(
                        doutor_id=dados["doutor_id"],
                        total_medicos_ems=dados["total_medicos_ems"],
                        total_toggle_ligado=dados["total_toggle_ligado"],
                        total_presentes=dados["total_presentes"],
                        total_ausentes=dados["total_ausentes"],
                        criada_em=dados["criado_em"],
                    )
                )
                await sessao.commit()
            importadas += 1
        except Exception as erro:
            erros += 1
            logger.exception("import chamada msg %s: %s", mensagem.id, erro)

    return {
        "lidas": lidas,
        "importadas": importadas,
        "ignoradas": ignoradas,
        "ja_existiam": ja_existiam,
        "erros": erros,
        "canal_id": canal.id,
    }


def id_canal_log(chave: str) -> int | None:
    valor = CANAIS.get(chave)
    return int(valor) if valor else None
