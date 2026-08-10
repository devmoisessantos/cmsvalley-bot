"""Parse das mensagens de LOG_BAU (formato Valley Logs)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class ItemLogBau:
    nome_bruto: str
    quantidade: int
    item_canonico: str | None  # None = desconhecido


@dataclass
class LogBauParseado:
    id_fivem: str
    nome_cidade: str
    acao: str  # PEGOU | GUARDOU | DESCONHECIDA
    itens: list[ItemLogBau] = field(default_factory=list)
    texto_bruto: str = ""


def _sem_acento(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def normalizar_nome_item(nome: str, aliases: dict[str, str]) -> str | None:
    chave = _sem_acento(nome).strip().lower()
    chave = re.sub(r"\s+", " ", chave)
    if chave in aliases:
        return aliases[chave]
    # tenta sem espaços
    chave_compacta = chave.replace(" ", "")
    for alias, canonico in aliases.items():
        if alias.replace(" ", "") == chave_compacta:
            return canonico
    return None


_RE_ID = re.compile(r"\[ID\]:\s*(\d+)", re.IGNORECASE)
_RE_NOME = re.compile(r"\[NOME\]:\s*(.+)", re.IGNORECASE)
_RE_ITEM = re.compile(
    r"(?:Item:\s*)?x\s*(\d+)\s+([^\n\r]+)",
    re.IGNORECASE,
)
_RE_ACAO_PEGOU = re.compile(r"\b(Pegou|Retirou|Tirou)\b", re.IGNORECASE)
_RE_ACAO_GUARDOU = re.compile(
    r"\b(Guardou|Devolveu|Colocou|Depositou)\b", re.IGNORECASE
)


def parsear_mensagem_log_bau(
    conteudo: str,
    aliases: dict[str, str],
) -> LogBauParseado | None:
    """
    Extrai ID, nome, ação e itens do texto do log.
    Retorna None se nem o ID for encontrado.
    """
    if not conteudo or not conteudo.strip():
        return None

    match_id = _RE_ID.search(conteudo)
    if not match_id:
        return None

    id_fivem = match_id.group(1).strip()
    match_nome = _RE_NOME.search(conteudo)
    nome_cidade = match_nome.group(1).strip() if match_nome else "—"

    if _RE_ACAO_GUARDOU.search(conteudo):
        acao = "GUARDOU"
    elif _RE_ACAO_PEGOU.search(conteudo):
        acao = "PEGOU"
    else:
        acao = "DESCONHECIDA"

    itens: list[ItemLogBau] = []
    for match_item in _RE_ITEM.finditer(conteudo):
        quantidade = int(match_item.group(1))
        nome_bruto = match_item.group(2).strip()
        # remove markdown residual
        nome_bruto = re.sub(r"[*`_]", "", nome_bruto).strip()
        canonico = normalizar_nome_item(nome_bruto, aliases)
        itens.append(
            ItemLogBau(
                nome_bruto=nome_bruto,
                quantidade=quantidade,
                item_canonico=canonico,
            )
        )

    return LogBauParseado(
        id_fivem=id_fivem,
        nome_cidade=nome_cidade,
        acao=acao,
        itens=itens,
        texto_bruto=conteudo,
    )
