# src/utils/nickname.py
"""
Ajuda a aplicar e limpar prefixos de apelido no Discord.

O Discord limita o nick a 32 caracteres. Estas funções respeitam esse limite.
"""

from __future__ import annotations

import re

from src.config import (
    CARGOS_HIERARQUIA,
    HIERARQUIA_GATE,
    PREFIXOS_NICKNAME,
)

ABERTURAS_DE_PREFIXO = "[⟦【〔"
FECHAMENTOS_DE_PREFIXO = "]⟧】〕"

# O ID FiveM vem depois de "|" (ASCII) ou "│" (barra fullwidth).
PADRAO_ID_NO_APELIDO = re.compile(r"[|│]\s*\d{1,7}")


def _normalizar_tag(tag: str) -> str:
    """Compara tags sem depender do tipo de colchete nem do espaço interno."""
    texto = (tag or "").strip()
    trocas = {"⟦": "[", "⟧": "]", "【": "[", "】": "]", "〔": "[", "〕": "]"}
    for antigo, novo in trocas.items():
        texto = texto.replace(antigo, novo)
    return re.sub(r"\s+", "", texto).lower()


def _separar_tag_do_nome(nome: str) -> tuple[str, str]:
    """Devolve `(tag, resto)` do começo do nome. Sem tag, `("", nome)`."""
    texto = (nome or "").strip()
    if not texto or texto[0] not in ABERTURAS_DE_PREFIXO:
        return "", texto

    for indice, caractere in enumerate(texto):
        if caractere in FECHAMENTOS_DE_PREFIXO:
            return texto[: indice + 1].strip(), texto[indice + 1 :].strip()

    return "", texto


def _dividir_nome_e_id(nome: str) -> tuple[str, str]:
    """Separa o nome do sufixo de ID (`| 1234`, `│ 1234`)."""
    encontrado = PADRAO_ID_NO_APELIDO.search(nome)
    if not encontrado:
        return nome.strip(), ""

    nome_sem_id = nome[: encontrado.start()].strip()
    id_do_apelido = encontrado.group(0).lstrip("|│ ").strip()
    resto = nome[encontrado.end() :].strip()
    if resto:
        return nome_sem_id, f"{id_do_apelido} {resto}"
    return nome_sem_id, id_do_apelido


def cargo_do_prefixo_do_nome(nome: str) -> str | None:
    """Cargo cujo prefixo está no começo do nome (None se a tag não é nossa)."""
    tag, _ = _separar_tag_do_nome(nome)
    if not tag:
        return None
    tag_normalizada = _normalizar_tag(tag)
    for cargo, prefixo in PREFIXOS_NICKNAME.items():
        if _normalizar_tag(prefixo) == tag_normalizada:
            return cargo
    return None


def remover_prefixo_existente(nome: str) -> str:
    """
    Remove qualquer prefixo entre colchetes do início do nome.

    Exemplo: "[HP] João Silva" → "João Silva"
    """
    nome_limpo = nome.strip()

    if not nome_limpo:
        return nome_limpo

    primeiro_caractere = nome_limpo[0]
    if primeiro_caractere not in ABERTURAS_DE_PREFIXO:
        return nome_limpo

    for indice, caractere in enumerate(nome_limpo):
        if caractere in FECHAMENTOS_DE_PREFIXO:
            return nome_limpo[indice + 1 :].strip()

    # Não achou fechamento correspondente; devolve o nome como está.
    return nome_limpo


def aplicar_prefixo(
    nome_atual: str,
    cargo: str,
    id_fivem: str | None = None,
) -> str:
    """
    Remove o prefixo antigo (se houver) e aplica o prefixo do cargo.

    O resultado nunca passa de 32 caracteres (limite do Discord). Quando o
    limite aperta, o `| ID` do apelido é preservado: o nome encurta, o
    passaporte não. Sem isso, um nick longo perdia o número e o membro deixava
    de ser encontrado pela chamada e pelos laudos.

    `id_fivem` é usado só quando o apelido ainda não tem ID: é o caso de quem
    perdeu o número em uma edição manual. O ID que já está no apelido manda.

    Se o cargo não tiver prefixo cadastrado, só corta o nome em 32 caracteres.
    """
    prefixo_do_cargo = PREFIXOS_NICKNAME.get(cargo)

    if prefixo_do_cargo is None:
        return nome_atual[:32]

    nome_sem_prefixo = remover_prefixo_existente(nome_atual)
    nome, sufixo_do_id = _dividir_nome_e_id(nome_sem_prefixo)
    if not sufixo_do_id and id_fivem:
        sufixo_do_id = str(id_fivem).strip()
    prefixo_com_espaco = f"{prefixo_do_cargo} "
    espaco_do_nome = 32 - len(prefixo_com_espaco)

    if not sufixo_do_id:
        return f"{prefixo_com_espaco}{nome[:espaco_do_nome]}"

    sufixo_com_espaco = f" | {sufixo_do_id}"
    espaco_para_o_nome = espaco_do_nome - len(sufixo_com_espaco)
    if espaco_para_o_nome < 0:
        # O ID ocupa quase tudo: mantém a tag e o ID, corta o nome.
        return f"{prefixo_com_espaco}{sufixo_com_espaco}"[:32]

    return f"{prefixo_com_espaco}{nome[:espaco_para_o_nome]}{sufixo_com_espaco}"


def aplicar_prefixo_por_cargos(
    nome_atual: str,
    nomes_de_cargos: list[str],
) -> str:
    """
    Aplica a tag do cargo mais alto da lista de cargos do membro.

    Quando nenhum cargo da lista tem prefixo, a tag antiga só é removida se
    ela for uma das tags que o bot conhece. Tag personalizada (por exemplo
    uma escrita à mão pela diretoria) fica como está, em vez de sumir.
    """
    cargo_do_prefixo = escolher_cargo_do_prefixo(nomes_de_cargos)

    if cargo_do_prefixo is not None:
        return aplicar_prefixo(nome_atual, cargo_do_prefixo)

    if cargo_do_prefixo_do_nome(nome_atual) is None:
        return nome_atual[:32]

    return remover_prefixo_existente(nome_atual)[:32]


def _prioridade_do_cargo_para_prefixo(nome_cargo: str) -> tuple[int, int]:
    """
    Quanto menor o número, mais alto o cargo para efeito de tag.

    A ordem é a mesma do painel: a hierarquia hospitalar vem inteira antes da
    GATE, e dentro de cada uma o menor índice é o cargo mais alto. Assim a tag
    sempre combina com o cargo exibido — Responsável HP continua com a tag
    RESP · HP mesmo tendo também um cargo da GATE.
    """
    if nome_cargo in CARGOS_HIERARQUIA:
        return (0, CARGOS_HIERARQUIA.index(nome_cargo))
    if nome_cargo in HIERARQUIA_GATE:
        return (1, HIERARQUIA_GATE.index(nome_cargo))
    return (2, 9999)


def escolher_cargo_do_prefixo(nomes_de_cargos: list[str]) -> str | None:
    """
    Entre os cargos informados, escolhe o que deve ditar a tag do nick.

    Só considera cargos que existem em PREFIXOS_NICKNAME.
    Nunca escolhe um cargo “mais baixo” se houver um mais alto na lista.
    """
    candidatos: list[str] = []
    for nome in nomes_de_cargos:
        if nome in PREFIXOS_NICKNAME:
            candidatos.append(nome)
    if not candidatos:
        return None
    candidatos.sort(key=_prioridade_do_cargo_para_prefixo)
    return candidatos[0]


def nomes_de_cargos_com_prefixo_do_membro(membro) -> list[str]:
    """
    Nomes dos cargos do membro que têm tag cadastrada.

    Aceita Member do Discord (atributo roles) sem importar discord aqui,
    para o utilitário continuar leve.
    """
    nomes: list[str] = []
    for cargo in getattr(membro, "roles", []) or []:
        nome = getattr(cargo, "name", None)
        if nome and nome in PREFIXOS_NICKNAME:
            nomes.append(nome)
    return nomes
