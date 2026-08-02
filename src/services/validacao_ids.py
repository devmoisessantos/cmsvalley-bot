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
from typing import Dict, List, Optional


@dataclass
class MembroConhecido:
    """Um Recrutamento aprovado, montado a partir do banco (join com Usuario pro nome)."""
    id_fivem: str
    nome: str  # Usuario.nickname_atual do candidato (pode vir vazio se nunca setou apelido)
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
    for i, digito in enumerate(id_str):
        for alternativa in _DIGITOS_CONFUNDIVEIS.get(digito, []):
            candidatos.append(id_str[:i] + alternativa + id_str[i + 1:])
    return candidatos


def _candidatos_por_corte(id_str: str) -> List[str]:
    """Remove o primeiro ou o último dígito — cobre o caso de um dígito extra grudado pelo OCR."""
    if len(id_str) <= 1:
        return []
    return [id_str[1:], id_str[:-1]]


def _buscar_por_nome(nome_lido: str, membros: List[MembroConhecido]) -> Optional[MembroConhecido]:
    candidatos = [m for m in membros if m.nome]  # ignora quem não tem apelido cadastrado
    nomes = [m.nome for m in candidatos]
    proximos = get_close_matches(nome_lido, nomes, n=1, cutoff=0.75)
    if not proximos:
        return None
    nome_encontrado = proximos[0]
    return next((m for m in candidatos if m.nome == nome_encontrado), None)


def validar_medico(id_lido: str, nome_lido: str, membros: List[MembroConhecido]) -> MedicoValidado:
    por_id = {m.id_fivem: m for m in membros}

    # 1. bate direto
    if id_lido in por_id:
        return MedicoValidado(id_lido, nome_lido, status="confirmado", membro=por_id[id_lido])

    # 2. tenta corrigir por confusão de dígito (ex: 710515 -> 110515)
    for candidato_str in _candidatos_por_digito(id_lido) + _candidatos_por_corte(id_lido):
        if candidato_str in por_id:
            return MedicoValidado(
                id_lido, nome_lido,
                status="corrigido",
                id_corrigido=candidato_str,
                membro=por_id[candidato_str],
                motivo=f"ID lido como {id_lido}, corrigido pra {candidato_str} (confusão de dígito)",
            )

    # 3. não bateu por número nenhum — tenta pelo nome
    membro_por_nome = _buscar_por_nome(nome_lido, membros)
    if membro_por_nome:
        return MedicoValidado(
            id_lido, nome_lido,
            status="corrigido",
            id_corrigido=membro_por_nome.id_fivem,
            membro=membro_por_nome,
            motivo=f"ID não batia, encontrado pelo nome ('{nome_lido}' ~ '{membro_por_nome.nome}')",
        )

    # 4. não encontrado de nenhuma forma
    return MedicoValidado(
        id_lido, nome_lido,
        status="nao_encontrado",
        motivo="Não encontrado entre os Recrutamentos aprovados — pode ser médico de outro hospital (ex: Norte).",
    )


def validar_medicos(medicos_api: List[dict], membros: List[MembroConhecido]) -> List[MedicoValidado]:
    """Valida a lista inteira devolvida pela API (`resultado["medicos"]`).

    `medicos_api` vem com `id` como int (formato da API) — convertido pra
    string aqui, já que é assim que a coluna id_fivem existe no banco.
    """
    return [validar_medico(str(m["id"]), m["nome"], membros) for m in medicos_api]