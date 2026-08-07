# src/utils/nickname.py
"""
Ajuda a aplicar e limpar prefixos de apelido no Discord.

O Discord limita o nick a 32 caracteres. Estas funções respeitam esse limite.
"""

from src.config import PREFIXOS_NICKNAME

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
