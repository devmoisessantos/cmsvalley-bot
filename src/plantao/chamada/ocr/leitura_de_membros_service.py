"""
Complemento pro validacao_ids.py: varre guild.members procurando o padrão
"[TAG] Nome | idFivem" no apelido — só entra quem é do hospital de verdade
(prefixo de cargo no nick ou cargo da hierarquia).

Visitante e quem só tem recrutamento antigo no banco, sem cargo/prefixo
hoje, NÃO entra na lista de candidatos da chamada.
"""

import re
from typing import (
    Dict,
    List,
    Optional,
)

import discord

from src.config import (
    CARGOS,
    CARGOS_HIERARQUIA,
    PREFIXOS_NICKNAME,
)
from src.plantao.chamada.validacao_ids_service import MembroConhecido

# Aceita "|" ASCII e "│" (barra vertical fullwidth comum em nicks do servidor).
# Não ancora no fim: apelidos reais têm lixo depois do ID (ex: "| 1763 [VL]").
_PADRAO_ID_APELIDO = re.compile(r"[|│]\s*(\d{1,7})")


def extrair_id_do_apelido(nome_exibido: str) -> Optional[str]:
    """
    Extrai o primeiro ID FiveM após separador de apelido.

    Aceita tanto ``|`` quanto ``│`` (fullwidth), padrão visto em nicks
    como ``『RES.RE』Nome│83793``.
    """
    match = _PADRAO_ID_APELIDO.search(nome_exibido or "")
    return match.group(1) if match else None


def _extrair_nome(nome_exibido: str) -> str:
    """Tira o prefixo de cargo (se tiver) e fica só com o nome, antes do separador."""
    # Aceita "|" e "│" (fullwidth) como separador do ID
    nome_sem_id = re.split(r"[|│]", nome_exibido or "", maxsplit=1)[0].strip()
    for tag in PREFIXOS_NICKNAME.values():
        if nome_sem_id.startswith(tag):
            nome_sem_id = nome_sem_id[len(tag) :].strip()
            break
    return nome_sem_id


def combinar_membros(
    membros_do_banco: List[MembroConhecido],
    membros_do_apelido: List[MembroConhecido],
) -> List[MembroConhecido]:
    """Une as duas fontes — o banco (Recrutamento aprovado) tem prioridade
    quando o mesmo id_fivem aparece nas duas, por ser o dado mais formal."""
    combinado: Dict[str, MembroConhecido] = {
        medico.id_fivem: medico for medico in membros_do_apelido
    }
    combinado.update({medico.id_fivem: medico for medico in membros_do_banco})
    return list(combinado.values())


def possui_prefixo_reconhecido(nome_exibido: str) -> bool:
    """True se o apelido começar com algum dos prefixos de cargo conhecidos."""
    nome_exibido = (nome_exibido or "").strip()
    return any(nome_exibido.startswith(tag) for tag in PREFIXOS_NICKNAME.values())


def membro_e_do_hospital(membro: discord.Member) -> bool:
    """
    True só se a pessoa for do hospital agora:
    - apelido com prefixo de cargo (ex: [ ENF ], [ DR ]), ou
    - algum cargo da hierarquia hospitalar.

    Visitante, sem prefixo e sem cargo da hierarquia, fica de fora.
    """
    if membro is None or membro.bot:
        return False
    nome_exibido = membro.nick or membro.display_name or membro.name
    if possui_prefixo_reconhecido(nome_exibido):
        return True
    ids_hierarquia = {CARGOS[nome] for nome in CARGOS_HIERARQUIA if nome in CARGOS}
    return any(cargo.id in ids_hierarquia for cargo in membro.roles)


def construir_membros_via_apelido(guild: discord.Guild) -> List[MembroConhecido]:
    """Varre guild.members com prefixo de hospital e ID FiveM no apelido."""
    membros = []
    for membro in guild.members:
        if not membro_e_do_hospital(membro):
            continue
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


def filtrar_membros_do_hospital(
    membros: List[MembroConhecido], guild: discord.Guild
) -> List[MembroConhecido]:
    """Mantém só quem está no servidor e é do hospital (prefixo ou hierarquia)."""
    filtrados: List[MembroConhecido] = []
    for conhecido in membros:
        membro = guild.get_member(conhecido.discord_id)
        if membro is None:
            continue
        if not membro_e_do_hospital(membro):
            continue
        filtrados.append(conhecido)
    return filtrados
