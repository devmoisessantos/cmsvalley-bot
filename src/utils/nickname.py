# src/utils/nickname.py
"""
Ajuda a aplicar e limpar prefixos de apelido no Discord.

O Discord limita o nick a 32 caracteres. Estas funções respeitam esse limite.
"""

from __future__ import annotations

from src.config import (
    CARGOS_HIERARQUIA,
    HIERARQUIA_GATE,
    PREFIXOS_NICKNAME,
)

ABERTURAS_DE_PREFIXO = "[⟦【〔"
FECHAMENTOS_DE_PREFIXO = "]⟧】〕"


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


def aplicar_prefixo(nome_atual: str, cargo: str) -> str:
    """
    Remove o prefixo antigo (se houver) e aplica o prefixo do cargo.

    O resultado nunca passa de 32 caracteres (limite do Discord).
    Se o cargo não tiver prefixo cadastrado, só corta o nome em 32 caracteres.
    """
    prefixo_do_cargo = PREFIXOS_NICKNAME.get(cargo)

    if prefixo_do_cargo is None:
        return nome_atual[:32]

    nome_sem_prefixo = remover_prefixo_existente(nome_atual)
    prefixo_com_espaco = f"{prefixo_do_cargo} "
    limite_do_nome = 32 - len(prefixo_com_espaco)

    return f"{prefixo_com_espaco}{nome_sem_prefixo[:limite_do_nome]}"


def _prioridade_do_cargo_para_prefixo(nome_cargo: str) -> tuple[int, int]:
    """
    Quanto menor o número, mais alto o cargo para efeito de tag.

    GATE vem antes do hospital: quem está na GATE usa a tag GATE,
    não a tag hospitalar (ex.: [ PAR ] vira 【G · GATE】 no ingresso).
    """
    if nome_cargo in HIERARQUIA_GATE:
        return (0, HIERARQUIA_GATE.index(nome_cargo))
    if nome_cargo in CARGOS_HIERARQUIA:
        return (1, CARGOS_HIERARQUIA.index(nome_cargo))
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
