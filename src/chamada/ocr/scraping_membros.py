"""
Complemento pro validacao_ids.py: varre guild.members procurando o padrão
"[TAG] Nome | idFivem" no apelido — cobre quem nunca passou pelo fluxo de
Recrutamento formal (visitantes, membros antigos, etc.), mas já tem o ID
FiveM no próprio apelido do servidor.

Isso NÃO substitui o Recrutamento — os dois são combinados: o Recrutamento
aprovado continua tendo prioridade (é o dado mais confiável, veio de um
processo formal), e a varredura de apelido preenche as lacunas.
"""

import re
from typing import (
    Dict,
    List,
    Optional,
)

import discord

# Os prefixos de cargo que aparecem no início do apelido — usados só pra
# identificar visualmente, não são obrigatórios pro parsing do ID funcionar
# (o regex do ID não depende de reconhecer o prefixo certo).
from src.config import PREFIXOS_NICKNAME
from src.services.validacao_ids import MembroConhecido

# Pega o primeiro número depois de um "|" — não ancora no fim da string
# porque apelidos reais têm lixo depois do ID às vezes (ex: "| 1763 [VL]").
_PADRAO_ID_APELIDO = re.compile(r"\|\s*(\d{1,7})")


def extrair_id_do_apelido(nome_exibido: str) -> Optional[str]:
    match = _PADRAO_ID_APELIDO.search(nome_exibido or "")
    return match.group(1) if match else None


def _extrair_nome(nome_exibido: str) -> str:
    """Tira o prefixo de cargo (se tiver) e fica só com o nome, antes do '|'."""
    nome_sem_id = nome_exibido.split("|")[0].strip()
    for tag in PREFIXOS_NICKNAME.values():
        if nome_sem_id.startswith(tag):
            nome_sem_id = nome_sem_id[len(tag) :].strip()
            break
    return nome_sem_id


def construir_membros_via_apelido(guild: discord.Guild) -> List[MembroConhecido]:
    """Varre guild.members procurando '... | idFivem' no apelido/nome de exibição."""
    membros = []
    for membro in guild.members:
        nome_exibido = membro.nick or membro.display_name or membro.name
        id_fivem = extrair_id_do_apelido(nome_exibido)
        if id_fivem is None:
            continue
        membros.append(
            MembroConhecido(
                id_fivem=id_fivem,
                nome=_extrair_nome(nome_exibido),
                discord_id=membro.id,
            )
        )
    return membros


def combinar_membros(
    membros_do_banco: List[MembroConhecido],
    membros_do_apelido: List[MembroConhecido],
) -> List[MembroConhecido]:
    """Une as duas fontes — o banco (Recrutamento aprovado) tem prioridade
    quando o mesmo id_fivem aparece nas duas, por ser o dado mais formal."""
    combinado: Dict[str, MembroConhecido] = {m.id_fivem: m for m in membros_do_apelido}
    combinado.update({m.id_fivem: m for m in membros_do_banco})
    return list(combinado.values())


def possui_prefixo_reconhecido(nome_exibido: str) -> bool:
    """True se o apelido começar com algum dos prefixos de cargo conhecidos."""
    nome_exibido = (nome_exibido or "").strip()
    return any(nome_exibido.startswith(tag) for tag in PREFIXOS_NICKNAME.values())


def construir_membros_via_apelido(guild: discord.Guild) -> List[MembroConhecido]:
    """Varre guild.members procurando '[TAG] Nome | idFivem' no apelido —
    exige o prefixo de cargo reconhecido, senão qualquer 'Nome | número' no
    servidor entraria como falso positivo (ex: 'Beka Rico | 251')."""
    membros = []
    for membro in guild.members:
        nome_exibido = membro.nick or membro.display_name or membro.name
        if not possui_prefixo_reconhecido(nome_exibido):
            continue
        id_fivem = extrair_id_do_apelido(nome_exibido)
        if id_fivem is None:
            continue
        membros.append(
            MembroConhecido(
                id_fivem=id_fivem,
                nome=_extrair_nome(nome_exibido),
                discord_id=membro.id,
            )
        )
    return membros
