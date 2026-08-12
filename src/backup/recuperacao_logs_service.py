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

from src.config import (
    CANAIS,
    CARGOS,
    TOTAL_PERGUNTAS_PROVA,
)
from src.database.connection import async_session
from src.database.models import (
    Chamada,
    ConsultaLaudo,
    HistoricoCargo,
    HistoricoPromocao,
    Laudo,
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


def _role_ids_no_texto(texto: str) -> list[int]:
    return [int(x) for x in re.findall(r"<@&(\d+)>", texto)]


def _resolver_cargo_final_por_roles(texto: str) -> str | None:
    """
    Só ENFERMEIRO ou PARAMEDICO (valores válidos de cargo_final).
    Olha menções <@&id> no texto.
    """
    id_enf = int(CARGOS.get("🔰・Enfermeiro (a)") or 0)
    id_par = int(CARGOS.get("🚑・Paramédico") or 0)
    ids = set(_role_ids_no_texto(texto))
    # Paramédico tem prioridade se os dois aparecerem
    if id_par and id_par in ids:
        return "PARAMEDICO"
    if id_enf and id_enf in ids:
        return "ENFERMEIRO"
    lower = texto.lower()
    if "param" in lower:
        return "PARAMEDICO"
    if "enferm" in lower:
        return "ENFERMEIRO"
    return None


def _parsear_nota_percentual(texto: str) -> float | None:
    match = re.search(r"Nota:\s*`?([0-9]+(?:[.,][0-9]+)?)\s*%?", texto, re.I)
    if not match:
        match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*%", texto)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _estimar_acertos(nota: float | None) -> int | None:
    if nota is None:
        return None
    total = int(TOTAL_PERGUNTAS_PROVA or 11)
    return int(round((nota / 100.0) * total))


# ── LOG_RECRUTAMENTOS (início) ───────────────────────────────────────────


def parsear_mensagem_recrutamento_iniciado(mensagem: discord.Message) -> dict | None:
    """# Novo Recrutamento Iniciado → status ESTUDANDO (ainda sem cargo final)."""
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return None
    lower = texto.lower()
    if (
        "recrutamento iniciado" not in lower
        and "novo recrutamento iniciado" not in lower
    ):
        if "membro recrutado" not in lower:
            return None
        # manual / realizado tratados em outro fluxo
        if "aprovado" in lower or "reprovado" in lower:
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
        recrutador = ids[1] if len(ids) >= 2 else 0

    id_fivem = _campo_backtick("ID FiveM", texto)
    if id_fivem and id_fivem.upper() in ("N/A", "NA", "-"):
        id_fivem = None
    if id_fivem:
        id_fivem = str(id_fivem)[:20]

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    return {
        "discord_id_candidato": int(candidato),
        "discord_id_recrutador": int(recrutador or 0),
        "id_fivem": id_fivem,
        "status": "ESTUDANDO",
        "cargo_final": None,
        "nota_percentual": None,
        "acertos": None,
        "criado_em": criado_em,
        "message_id": mensagem.id,
    }


# ── LOG_APROVACOES ───────────────────────────────────────────────────────


def parsear_mensagem_aprovacao(mensagem: discord.Message) -> dict | None:
    """# Candidato Aprovado → APROVADO + cargo_final + nota."""
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return None
    lower = texto.lower()
    if "candidato aprovado" not in lower and "aprovado" not in lower:
        return None
    if "reprovado" in lower:
        return None

    candidato = _id_backtick_apos("Membro", texto)
    if candidato is None:
        ids = _ids_no_texto(texto)
        candidato = ids[0] if ids else None
    if candidato is None:
        return None

    executor = _id_backtick_apos("Executor", texto)
    if executor is None:
        ids = _ids_no_texto(texto)
        executor = ids[1] if len(ids) >= 2 else 0

    cargo_final = _resolver_cargo_final_por_roles(texto)
    if cargo_final is None:
        # aprovação sem Enfermeiro/Paramédico no log → não grava cargo inválido
        cargo_final = None

    nota = _parsear_nota_percentual(texto)
    acertos = _estimar_acertos(nota)

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    return {
        "discord_id_candidato": int(candidato),
        "discord_id_recrutador": int(executor or 0),
        "id_fivem": None,
        "status": "APROVADO",
        "cargo_final": cargo_final,
        "nota_percentual": nota,
        "acertos": acertos,
        "criado_em": criado_em,
        "message_id": mensagem.id,
    }


# ── LOG_REPROVACOES ──────────────────────────────────────────────────────


def parsear_mensagem_reprovacao(mensagem: discord.Message) -> dict | None:
    """# Candidato Reprovado → REPROVADO_TEMPO + nota."""
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return None
    lower = texto.lower()
    if "reprovado" not in lower:
        return None

    candidato = _id_backtick_apos("Membro", texto)
    if candidato is None:
        ids = _ids_no_texto(texto)
        candidato = ids[0] if ids else None
    if candidato is None:
        return None

    executor = _id_backtick_apos("Executor", texto)
    if executor is None:
        ids = _ids_no_texto(texto)
        executor = ids[1] if len(ids) >= 2 else 0

    nota = _parsear_nota_percentual(texto)
    acertos = _estimar_acertos(nota)

    status = "REPROVADO_TEMPO" if "24h" in lower or "24 h" in lower else "REPROVADO"

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    return {
        "discord_id_candidato": int(candidato),
        "discord_id_recrutador": int(executor or 0),
        "id_fivem": None,
        "status": status,
        "cargo_final": None,
        "nota_percentual": nota,
        "acertos": acertos,
        "criado_em": criado_em,
        "message_id": mensagem.id,
    }


async def _buscar_recrutamento_aberto(
    sessao,
    discord_id_candidato: int,
) -> Recrutamento | None:
    """Último recrutamento do candidato que ainda não está APROVADO."""
    resultado = await sessao.execute(
        select(Recrutamento)
        .where(Recrutamento.discord_id_candidato == int(discord_id_candidato))
        .order_by(Recrutamento.id.desc())
        .limit(5)
    )
    candidatos = list(resultado.scalars().all())
    for reg in candidatos:
        if reg.status in ("ESTUDANDO", "EM_PROVA", "REPROVADO", "REPROVADO_TEMPO"):
            return reg
    return candidatos[0] if candidatos else None


async def _upsert_recrutamento_from_log(dados: dict) -> str:
    """
    Cria ou atualiza recrutamento.
    Retorna: criado | atualizado | ignorado
    """
    async with async_session() as sessao:
        await _garantir_usuario(
            sessao,
            dados["discord_id_candidato"],
            id_fivem=dados.get("id_fivem"),
            status="APROVADO" if dados["status"] == "APROVADO" else "ESTUDANDO",
        )

        # anti-duplicata exata por timestamp de início
        resultado = await sessao.execute(
            select(Recrutamento)
            .where(
                Recrutamento.discord_id_candidato == dados["discord_id_candidato"],
                Recrutamento.data_inicio == dados["criado_em"],
            )
            .limit(1)
        )
        existente_mesmo_ts = resultado.scalar_one_or_none()
        if existente_mesmo_ts is not None:
            return "ignorado"

        registro = await _buscar_recrutamento_aberto(
            sessao, dados["discord_id_candidato"]
        )

        # Início: só cria se não houver aberto recente
        if dados["status"] == "ESTUDANDO":
            if registro is not None and registro.status == "ESTUDANDO":
                # atualiza fivem se veio no log
                if dados.get("id_fivem") and not registro.id_fivem:
                    registro.id_fivem = dados["id_fivem"]
                if dados.get("discord_id_recrutador"):
                    registro.discord_id_recrutador = dados["discord_id_recrutador"]
                await sessao.commit()
                return "atualizado"

            sessao.add(
                Recrutamento(
                    id_fivem=dados.get("id_fivem"),
                    discord_id_candidato=dados["discord_id_candidato"],
                    discord_id_recrutador=dados.get("discord_id_recrutador") or 0,
                    data_inicio=dados["criado_em"],
                    status="ESTUDANDO",
                    cargo_final=None,
                )
            )
            await sessao.commit()
            return "criado"

        # Aprovação / reprovação: atualiza aberto ou cria completo
        if registro is None:
            sessao.add(
                Recrutamento(
                    id_fivem=dados.get("id_fivem"),
                    discord_id_candidato=dados["discord_id_candidato"],
                    discord_id_recrutador=dados.get("discord_id_recrutador") or 0,
                    data_inicio=dados["criado_em"],
                    data_fim=dados["criado_em"],
                    status=dados["status"][:30],
                    cargo_final=dados.get("cargo_final"),
                    nota_percentual=dados.get("nota_percentual"),
                    acertos=dados.get("acertos"),
                )
            )
            await sessao.commit()
            return "criado"

        registro.status = dados["status"][:30]
        registro.data_fim = dados["criado_em"]
        if dados.get("cargo_final"):
            registro.cargo_final = dados["cargo_final"]
        if dados.get("nota_percentual") is not None:
            registro.nota_percentual = dados["nota_percentual"]
        if dados.get("acertos") is not None:
            registro.acertos = dados["acertos"]
        if dados.get("discord_id_recrutador"):
            registro.discord_id_recrutador = dados["discord_id_recrutador"]
        if dados.get("id_fivem") and not registro.id_fivem:
            registro.id_fivem = dados["id_fivem"]
        await sessao.commit()
        return "atualizado"


async def importar_canal_recrutamento_generico(
    canal: discord.TextChannel,
    *,
    parser,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    lidas = importadas = atualizadas = ignoradas = ja_existiam = erros = 0

    async for mensagem in canal.history(limit=limite, oldest_first=True):
        lidas += 1
        if apenas_bot_id is not None and mensagem.author.id != apenas_bot_id:
            ignoradas += 1
            continue
        try:
            dados = parser(mensagem)
            if dados is None:
                ignoradas += 1
                continue
            resultado = await _upsert_recrutamento_from_log(dados)
            if resultado == "criado":
                importadas += 1
            elif resultado == "atualizado":
                atualizadas += 1
            else:
                ja_existiam += 1
        except Exception as erro:
            erros += 1
            logger.exception("import recrutamento msg %s: %s", mensagem.id, erro)

    return {
        "lidas": lidas,
        "importadas": importadas,
        "atualizadas": atualizadas,
        "ignoradas": ignoradas,
        "ja_existiam": ja_existiam,
        "erros": erros,
        "canal_id": canal.id,
    }


async def importar_log_recrutamentos_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    """LOG_RECRUTAMENTOS — inícios (ESTUDANDO)."""
    return await importar_canal_recrutamento_generico(
        canal,
        parser=parsear_mensagem_recrutamento_iniciado,
        limite=limite,
        apenas_bot_id=apenas_bot_id,
    )


async def importar_log_aprovacoes_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    """LOG_APROVACOES — APROVADO + ENFERMEIRO/PARAMEDICO + nota."""
    return await importar_canal_recrutamento_generico(
        canal,
        parser=parsear_mensagem_aprovacao,
        limite=limite,
        apenas_bot_id=apenas_bot_id,
    )


async def importar_log_reprovacoes_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    """LOG_REPROVACOES — REPROVADO(_TEMPO) + nota."""
    return await importar_canal_recrutamento_generico(
        canal,
        parser=parsear_mensagem_reprovacao,
        limite=limite,
        apenas_bot_id=apenas_bot_id,
    )


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


# ── LOG_WHITELIST ────────────────────────────────────────────────────────


def parsear_mensagem_whitelist(mensagem: discord.Message) -> dict | None:
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return None
    lower = texto.lower()
    if (
        "liberação" not in lower
        and "liberacao" not in lower
        and "whitelist" not in lower
    ):
        if (
            "identificador" not in lower
            and "usuário" not in lower
            and "usuario" not in lower
        ):
            return None

    discord_id = _id_backtick_apos("Usuário", texto) or _id_backtick_apos(
        "Usuario", texto
    )
    if discord_id is None:
        ids = _ids_no_texto(texto)
        discord_id = ids[0] if ids else None
    if discord_id is None:
        return None

    id_fivem = _campo_backtick("Identificador", texto) or _campo_backtick(
        "ID FiveM", texto
    )
    if id_fivem:
        id_fivem = str(id_fivem)[:20]

    nome = _campo_backtick("Nome", texto)
    sobrenome = _campo_backtick("Sobrenome", texto)
    nick = None
    if nome and sobrenome:
        nick = f"{nome} {sobrenome}"[:100]
    elif nome:
        nick = nome[:100]

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    return {
        "discord_id": int(discord_id),
        "id_fivem": id_fivem,
        "nickname_atual": nick,
        "criado_em": criado_em,
        "message_id": mensagem.id,
    }


async def importar_log_whitelist_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    lidas = importadas = atualizadas = ignoradas = ja_existiam = erros = 0

    async for mensagem in canal.history(limit=limite, oldest_first=True):
        lidas += 1
        if apenas_bot_id is not None and mensagem.author.id != apenas_bot_id:
            ignoradas += 1
            continue
        try:
            dados = parsear_mensagem_whitelist(mensagem)
            if dados is None:
                ignoradas += 1
                continue
            async with async_session() as sessao:
                resultado = await sessao.execute(
                    select(Usuario).where(Usuario.discord_id == dados["discord_id"])
                )
                usuario = resultado.scalar_one_or_none()
                if usuario is None:
                    sessao.add(
                        Usuario(
                            discord_id=dados["discord_id"],
                            id_fivem=dados.get("id_fivem"),
                            nickname_atual=dados.get("nickname_atual"),
                            status="VISITANTE",
                            ja_foi_aprovado=False,
                        )
                    )
                    await sessao.commit()
                    importadas += 1
                else:
                    mudou = False
                    if dados.get("id_fivem") and not usuario.id_fivem:
                        usuario.id_fivem = dados["id_fivem"]
                        mudou = True
                    if dados.get("nickname_atual") and not usuario.nickname_atual:
                        usuario.nickname_atual = dados["nickname_atual"]
                        mudou = True
                    if mudou:
                        await sessao.commit()
                        atualizadas += 1
                    else:
                        ja_existiam += 1
        except Exception as erro:
            erros += 1
            logger.exception("import whitelist msg %s: %s", mensagem.id, erro)

    return {
        "lidas": lidas,
        "importadas": importadas,
        "atualizadas": atualizadas,
        "ignoradas": ignoradas,
        "ja_existiam": ja_existiam,
        "erros": erros,
        "canal_id": canal.id,
    }


# ── LOG_CARGOS → historico_cargos ────────────────────────────────────────


def parsear_mensagem_cargo(mensagem: discord.Message) -> list[dict]:
    """Uma mensagem pode gerar vários registros (adicionados + removidos)."""
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return []
    if (
        "alteração de cargo" not in texto.lower()
        and "alteracao de cargo" not in texto.lower()
    ):
        if "Adicionados:" not in texto and "Removidos:" not in texto:
            return []

    membro = _id_backtick_apos("Membro", texto)
    if membro is None:
        ids = _ids_no_texto(texto)
        membro = ids[0] if ids else None
    if membro is None:
        return []

    executores = _ids_no_texto(texto)
    executor = (
        executores[-1] if len(executores) >= 2 else (executores[0] if executores else 0)
    )

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    registros: list[dict] = []

    def _extrair_lista(rotulo: str) -> list[str]:
        match = re.search(rf"{rotulo}:\*\*\s*(.+?)(?:\n|$)", texto, re.I)
        if not match:
            match = re.search(rf"{rotulo}:\s*(.+?)(?:\n|$)", texto, re.I)
        if not match:
            return []
        bruto = match.group(1)
        # menções de cargo ou texto separado por vírgula
        nomes = re.findall(r"<@&(\d+)>", bruto)
        if nomes:
            return [f"role:{n}"[:50] for n in nomes]
        partes = [p.strip() for p in re.split(r"[,|]", bruto) if p.strip()]
        return [p[:50] for p in partes]

    for nome in _extrair_lista("Adicionados"):
        registros.append(
            {
                "discord_id": int(membro),
                "cargo": nome,
                "acao": "ADICIONADO",
                "executor_id": int(executor or 0),
                "data_hora": criado_em,
            }
        )
    for nome in _extrair_lista("Removidos"):
        registros.append(
            {
                "discord_id": int(membro),
                "cargo": nome,
                "acao": "REMOVIDO",
                "executor_id": int(executor or 0),
                "data_hora": criado_em,
            }
        )
    return registros


async def importar_log_cargos_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    lidas = importadas = ignoradas = ja_existiam = erros = 0
    atualizadas = 0

    async for mensagem in canal.history(limit=limite, oldest_first=True):
        lidas += 1
        if apenas_bot_id is not None and mensagem.author.id != apenas_bot_id:
            ignoradas += 1
            continue
        try:
            registros = parsear_mensagem_cargo(mensagem)
            if not registros:
                ignoradas += 1
                continue
            async with async_session() as sessao:
                for reg in registros:
                    await _garantir_usuario(
                        sessao, reg["discord_id"], status="VISITANTE"
                    )
                    # anti-duplicata grosseira
                    existente = await sessao.execute(
                        select(HistoricoCargo.id)
                        .where(
                            HistoricoCargo.discord_id == reg["discord_id"],
                            HistoricoCargo.cargo == reg["cargo"],
                            HistoricoCargo.acao == reg["acao"],
                            HistoricoCargo.data_hora == reg["data_hora"],
                        )
                        .limit(1)
                    )
                    if existente.scalar_one_or_none() is not None:
                        ja_existiam += 1
                        continue
                    sessao.add(
                        HistoricoCargo(
                            discord_id=reg["discord_id"],
                            cargo=reg["cargo"],
                            acao=reg["acao"],
                            executor_id=reg["executor_id"],
                            data_hora=reg["data_hora"],
                        )
                    )
                    importadas += 1
                await sessao.commit()
        except Exception as erro:
            erros += 1
            logger.exception("import cargos msg %s: %s", mensagem.id, erro)

    return {
        "lidas": lidas,
        "importadas": importadas,
        "atualizadas": atualizadas,
        "ignoradas": ignoradas,
        "ja_existiam": ja_existiam,
        "erros": erros,
        "canal_id": canal.id,
    }


# ── LOG_LAUDO → consultas_laudo + laudos ──────────────────────────────────


def parsear_mensagem_laudo(mensagem: discord.Message) -> dict | None:
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return None
    if "laudo" not in texto.lower():
        return None

    ids = _ids_no_texto(texto)
    # Paciente primeiro, psicólogo depois (ordem do template)
    paciente = None
    psicologo = None
    match_p = re.search(r"Paciente:\*\*\s*<@!?(\d+)>", texto)
    if not match_p:
        match_p = re.search(r"Paciente:\s*<@!?(\d+)>", texto)
    if match_p:
        paciente = int(match_p.group(1))
    match_s = re.search(r"Psic[oó]logo:\*\*\s*<@!?(\d+)>", texto)
    if not match_s:
        match_s = re.search(r"Psic[oó]logo:\s*<@!?(\d+)>", texto)
    if match_s:
        psicologo = int(match_s.group(1))
    if paciente is None and ids:
        paciente = ids[0]
    if psicologo is None and len(ids) >= 2:
        psicologo = ids[1]
    if paciente is None or psicologo is None:
        return None

    fivem_pac = None
    fivem_psi = None
    match = re.search(r"Paciente:.*?passaporte\s*`([^`]+)`", texto, re.I | re.S)
    if match:
        fivem_pac = match.group(1).strip()[:20]
        if fivem_pac in ("—", "-", "N/A"):
            fivem_pac = None
    match = re.search(r"Psic[oó]logo:.*?passaporte\s*`([^`]+)`", texto, re.I | re.S)
    if match:
        fivem_psi = match.group(1).strip()[:20]
        if fivem_psi in ("—", "-", "N/A"):
            fivem_psi = None

    parecer = "APROVADO" if "APROVADO" in texto.upper() else "REPROVADO"
    # se cor/texto explícito
    match = re.search(r"Parecer:\*\*\s*`([^`]+)`", texto, re.I)
    if not match:
        match = re.search(r"Parecer:\s*`([^`]+)`", texto, re.I)
    if match:
        p = match.group(1).strip().upper()
        if "APROV" in p:
            parecer = "APROVADO"
        elif "REPROV" in p:
            parecer = "REPROVADO"

    crp = _campo_backtick("CRP", texto) or "IMPORTADO"
    crp = str(crp)[:80]

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    return {
        "discord_id_paciente": int(paciente),
        "discord_id_psicologo": int(psicologo),
        "id_fivem_paciente": fivem_pac,
        "id_fivem_psicologo": fivem_psi,
        "parecer": parecer,
        "registro_profissional": crp,
        "motivo": f"import_log:{mensagem.id}",
        "criado_em": criado_em,
        "message_id": mensagem.id,
    }


async def importar_log_laudos_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    lidas = importadas = ignoradas = ja_existiam = erros = 0
    atualizadas = 0

    async for mensagem in canal.history(limit=limite, oldest_first=True):
        lidas += 1
        if apenas_bot_id is not None and mensagem.author.id != apenas_bot_id:
            ignoradas += 1
            continue
        try:
            dados = parsear_mensagem_laudo(mensagem)
            if dados is None:
                ignoradas += 1
                continue
            marca = dados["motivo"]
            async with async_session() as sessao:
                existe = await sessao.execute(
                    select(Laudo.id).where(Laudo.motivo == marca).limit(1)
                )
                if existe.scalar_one_or_none() is not None:
                    ja_existiam += 1
                    continue

                consulta = ConsultaLaudo(
                    discord_id_psicologo=dados["discord_id_psicologo"],
                    discord_id_paciente=dados["discord_id_paciente"],
                    id_fivem_psicologo=dados.get("id_fivem_psicologo"),
                    id_fivem_paciente=dados.get("id_fivem_paciente"),
                    status="FINALIZADA",
                    iniciada_em=dados["criado_em"],
                    finalizada_em=dados["criado_em"],
                )
                sessao.add(consulta)
                await sessao.flush()

                sessao.add(
                    Laudo(
                        consulta_id=consulta.id,
                        discord_id_psicologo=dados["discord_id_psicologo"],
                        discord_id_paciente=dados["discord_id_paciente"],
                        id_fivem_psicologo=dados.get("id_fivem_psicologo"),
                        id_fivem_paciente=dados.get("id_fivem_paciente"),
                        parecer=dados["parecer"],
                        motivo=marca[:1500],
                        registro_profissional=dados["registro_profissional"],
                        canal_laudo_message_id=dados["message_id"],
                        criado_em=dados["criado_em"],
                    )
                )
                await sessao.commit()
            importadas += 1
        except Exception as erro:
            erros += 1
            logger.exception("import laudo msg %s: %s", mensagem.id, erro)

    return {
        "lidas": lidas,
        "importadas": importadas,
        "atualizadas": atualizadas,
        "ignoradas": ignoradas,
        "ja_existiam": ja_existiam,
        "erros": erros,
        "canal_id": canal.id,
    }


# ── LOG_PROMOVIDOS → historico_promocoes ─────────────────────────────────


def parsear_mensagem_promocao(mensagem: discord.Message) -> dict | None:
    texto = extrair_texto_mensagem(mensagem)
    if not texto.strip():
        return None
    lower = texto.lower()

    if "membro promovido" in lower:
        tipo = "PROMOCAO"
    elif "não aprovada" in lower or "nao aprovada" in lower or "não promovido" in lower:
        tipo = "NAO_PROMOVIDO"
    elif "rebaix" in lower:
        tipo = "REBAIXAMENTO"
    else:
        return None

    ids = _ids_no_texto(texto)
    alvo = ids[0] if ids else None
    if alvo is None:
        return None
    staff = ids[1] if len(ids) >= 2 else None

    cargo_de = None
    cargo_para = None
    match = re.search(
        r"De:\*\*\s*`([^`]+)`\s*→\s*\*\*Para:\*\*\s*`([^`]+)`",
        texto,
    )
    if not match:
        match = re.search(
            r"De:\s*`([^`]+)`\s*→\s*\*?\*?Para:\*?\*?\s*`([^`]+)`",
            texto,
        )
    if match:
        cargo_de = match.group(1).strip()[:80]
        cargo_para = match.group(2).strip()[:80]

    criado_em = mensagem.created_at
    if criado_em.tzinfo is None:
        criado_em = criado_em.replace(tzinfo=timezone.utc)

    return {
        "discord_id": int(alvo),
        "tipo": tipo,
        "cargo_de": cargo_de,
        "cargo_para": cargo_para,
        "executado_por": int(staff) if staff else None,
        "motivo": f"import_log:{mensagem.id}",
        "criado_em": criado_em,
    }


async def importar_log_promocoes_do_canal(
    canal: discord.TextChannel,
    *,
    limite: int | None = None,
    apenas_bot_id: int | None = None,
) -> dict:
    lidas = importadas = ignoradas = ja_existiam = erros = 0
    atualizadas = 0

    async for mensagem in canal.history(limit=limite, oldest_first=True):
        lidas += 1
        if apenas_bot_id is not None and mensagem.author.id != apenas_bot_id:
            ignoradas += 1
            continue
        try:
            dados = parsear_mensagem_promocao(mensagem)
            if dados is None:
                ignoradas += 1
                continue
            async with async_session() as sessao:
                existe = await sessao.execute(
                    select(HistoricoPromocao.id)
                    .where(HistoricoPromocao.motivo == dados["motivo"])
                    .limit(1)
                )
                if existe.scalar_one_or_none() is not None:
                    ja_existiam += 1
                    continue
                sessao.add(
                    HistoricoPromocao(
                        discord_id=dados["discord_id"],
                        tipo=dados["tipo"],
                        cargo_de=dados.get("cargo_de"),
                        cargo_para=dados.get("cargo_para"),
                        motivo=dados["motivo"][:500],
                        executado_por=dados.get("executado_por"),
                        criado_em=dados["criado_em"],
                    )
                )
                await sessao.commit()
            importadas += 1
        except Exception as erro:
            erros += 1
            logger.exception("import promocao msg %s: %s", mensagem.id, erro)

    return {
        "lidas": lidas,
        "importadas": importadas,
        "atualizadas": atualizadas,
        "ignoradas": ignoradas,
        "ja_existiam": ja_existiam,
        "erros": erros,
        "canal_id": canal.id,
    }
