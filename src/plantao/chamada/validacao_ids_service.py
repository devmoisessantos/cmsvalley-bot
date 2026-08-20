"""
Módulo pra colar no cmsvalley-bot: valida e tenta corrigir as entradas
devolvidas pela API de OCR, cruzando com os Recrutamentos aprovados no
banco (tabela `recrutamentos`, coluna `id_fivem`) — em vez de simplesmente
descartar o que a API marcou como "suspeito".

A API NUNCA decide se um ID é "errado de verdade" — ela só sinaliza o que
foge do intervalo esperado. Quem resolve é o bot, cruzando com o banco.

Estratégia de validação, na ordem:
  1. ID lido bate direto com um Recrutamento aprovado -> confirmado.
  2. Não bate, mas bate depois de corrigir 1 dígito confundível pela fonte
     do HUD (ex: 710515 -> 110515, "7"/"1" se parecem nessa fonte) -> corrigido.
  3. Ainda não bate -> tenta achar por NOME (fuzzy match, usando o
     Usuario.nickname_atual do candidato) -> corrigido por nome.
  4. Não bate de nenhuma forma -> não encontrado (provavelmente médico de
     outro hospital, ex: Norte).

`id_fivem` é tratado como string em tudo aqui, porque é assim que a coluna
existe no banco (String(20)) — nada de cast pra int.
"""

from dataclasses import dataclass
from difflib import get_close_matches
from typing import (
    Dict,
    List,
    Optional,
)


@dataclass
class MembroConhecido:
    """
    Um Recrutamento aprovado, montado a partir do banco (join com Usuario pro nome).
    """

    id_fivem: str
    # Usuario.nickname_atual do candidato (pode vir vazio se nunca setou apelido)
    nome: str
    discord_id: int


@dataclass
class MedicoValidado:
    id_lido: str
    nome_lido: str
    status: str  # "confirmado" | "corrigido" | "nao_encontrado"
    id_corrigido: Optional[str] = None
    membro: Optional[MembroConhecido] = None
    motivo: Optional[str] = None


# Pares de dígitos visualmente parecidos nessa fonte de HUD de jogo — usados
# pra gerar tentativas de correção quando um ID não bate com nenhum Recrutamento
# aprovado. Ajuste essa tabela se perceber outras confusões recorrentes.
_DIGITOS_CONFUNDIVEIS: Dict[str, List[str]] = {
    "0": ["8"],
    "8": ["0", "3"],
    "1": ["7"],
    "7": ["1"],
    "5": ["6"],
    "6": ["5"],
    "2": ["7"],
}


def _candidatos_por_digito(id_str: str) -> List[str]:
    """Troca UM dígito por vez pelo(s) parecido(s) — ex: '710515' -> '110515'."""
    candidatos = []
    for indice, digito in enumerate(id_str):
        for alternativa in _DIGITOS_CONFUNDIVEIS.get(digito, []):
            candidatos.append(id_str[:indice] + alternativa + id_str[indice + 1 :])
    return candidatos


def _candidatos_por_corte(id_str: str) -> List[str]:
    """
    Remove o primeiro ou o último dígito — cobre o caso de um dígito extra grudado pelo
    OCR.
    """
    if len(id_str) <= 1:
        return []
    return [id_str[1:], id_str[:-1]]


def normalizar_nome(texto: str) -> str:
    """
    Minúsculas, só letras/números/espaços — facilita comparar nomes do EMS com o
    Discord.
    """
    texto = (texto or "").lower().strip()
    limpo = []
    for caractere in texto:
        if caractere.isalnum() or caractere.isspace():
            limpo.append(caractere)
        else:
            limpo.append(" ")
    return " ".join("".join(limpo).split())


# Alias interno pra não quebrar chamadas locais antigas no mesmo arquivo
_normalizar_nome = normalizar_nome


def nomes_parecidos(nome_a: str, nome_b: str) -> bool:
    """
    True se os nomes batem de forma segura pra chamada automática:
    - iguais após normalizar
    - um contém o outro por inteiro (ex: 'asty' em 'asty detroit')
    - fuzzy alto (cutoff 0.82) — evita ligar 'Lionela' com qualquer 'Luan'

    Token solto em comum NÃO basta: gerava falso positivo demais
    (visitante / nome parecido sem ser a mesma pessoa).
    """
    primeiro_nome_limpo = normalizar_nome(nome_a)
    segundo_nome_limpo = normalizar_nome(nome_b)
    if not primeiro_nome_limpo or not segundo_nome_limpo:
        return False
    if primeiro_nome_limpo == segundo_nome_limpo:
        return True
    if (
        primeiro_nome_limpo in segundo_nome_limpo
        or segundo_nome_limpo in primeiro_nome_limpo
    ):
        return True
    proximos = get_close_matches(
        primeiro_nome_limpo, [segundo_nome_limpo], n=1, cutoff=0.82
    )
    if proximos:
        return True
    return False


# Alias usado pelo restante do módulo
_nomes_parecidos = nomes_parecidos


def _buscar_por_nome(
    nome_lido: str, membros: List[MembroConhecido]
) -> Optional[MembroConhecido]:
    """Procura membro pelo nome (nickname / apelido)."""
    candidatos = [medico for medico in membros if medico.nome]
    if not candidatos:
        return None

    # 1) Match direto / parcial / tokens
    for membro in candidatos:
        if nomes_parecidos(nome_lido, membro.nome):
            return membro

    # 2) Fuzzy clássico no conjunto de nomes (cutoff alto)
    nomes = [medico.nome for medico in candidatos]
    proximos = get_close_matches(nome_lido, nomes, n=1, cutoff=0.82)
    if not proximos:
        return None
    nome_encontrado = proximos[0]
    for membro in candidatos:
        if membro.nome == nome_encontrado:
            return membro
    return None


def nomes_ou_ids_batem_com_reconhecido(
    id_fivem: str,
    nome_ems: str,
    reconhecidos_ids: set[str],
    reconhecidos_nomes: list[str],
) -> bool:
    """
    True se a entrada 'desconhecida' na verdade já está entre os presentes
    (mesmo FID ou nome parecido). Usado pra tirar falso positivo do Norte.
    """
    id_limpo = str(id_fivem or "").strip()
    if id_limpo and id_limpo in reconhecidos_ids:
        return True
    for nome_conhecido in reconhecidos_nomes:
        if nomes_parecidos(nome_ems, nome_conhecido):
            return True
    return False


def validar_medico(
    id_lido: str, nome_lido: str, membros: List[MembroConhecido]
) -> MedicoValidado:
    """Relaciona uma leitura do OCR a um membro sem aceitar falsos positivos.

    Prioriza a coincidência exata do FiveM. Correção por dígito só vale se o
    nome do EMS também parecer com o do membro. Match só por nome exige
    semelhança alta. Quem não for do hospital nem entra em `membros`.
    """
    por_id = {medico.id_fivem: medico for medico in membros}

    # 1. bate direto no ID
    if id_lido in por_id:
        return MedicoValidado(
            id_lido, nome_lido, status="confirmado", membro=por_id[id_lido]
        )

    # 2. confusão de dígito / corte — só se o nome também bater
    for candidato_str in _candidatos_por_digito(id_lido) + _candidatos_por_corte(
        id_lido
    ):
        if candidato_str not in por_id:
            continue
        candidato = por_id[candidato_str]
        if (
            nome_lido
            and candidato.nome
            and not nomes_parecidos(nome_lido, candidato.nome)
        ):
            continue
        return MedicoValidado(
            id_lido,
            nome_lido,
            status="corrigido",
            id_corrigido=candidato_str,
            membro=candidato,
            motivo=(
                f"ID lido como {id_lido}, corrigido pra {candidato_str} "
                f"(confusão de dígito + nome compatível)"
            ),
        )

    # 3. só pelo nome (semelhança alta) — ainda vira "corrigido" pra o doutor revisar
    membro_por_nome = _buscar_por_nome(nome_lido, membros)
    if membro_por_nome:
        return MedicoValidado(
            id_lido,
            nome_lido,
            status="corrigido",
            id_corrigido=membro_por_nome.id_fivem,
            membro=membro_por_nome,
            motivo=(
                f"ID não batia, encontrado pelo nome "
                f"('{nome_lido}' ~ '{membro_por_nome.nome}')"
            ),
        )

    # 4. não encontrado
    return MedicoValidado(
        id_lido,
        nome_lido,
        status="nao_encontrado",
        motivo=(
            "Não encontrado entre os membros do cms "
            "(tag/hierarquia) — pode ser do hospital norte."
        ),
    )


def validar_medicos(
    medicos_api: List[dict], membros: List[MembroConhecido]
) -> List[MedicoValidado]:
    """Valida a lista inteira devolvida pela API (`resultado["medicos"]`).

    `medicos_api` vem com `id` como int (formato da API) — convertido pra
    string aqui, já que é assim que a coluna id_fivem existe no banco.
    """
    return [
        validar_medico(str(medico_da_api["id"]), medico_da_api["nome"], membros)
        for medico_da_api in medicos_api
    ]
