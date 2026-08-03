import discord
from dataclasses import (
    dataclass, 
    field
)

@dataclass
class MedicoNaChamada:
    id_fivem: str
    discord_id: int | None
    nome_ems: str
    nome_discord: str | None = None
    confianca: float = 1.0
    origem: str = "ocr"  # "ocr" | "manual" | "corrigido"
    motivo: str | None = None


@dataclass
class SessaoChamada:
    doutor_id: int
    chamada_id: int
    canal_id: int
    mensagem_id: int | None = None
    print_ems_url: str | None = None    # URL do print do EMS enviado pelo doutor, guardado pra referência
    print_ems_mensagem: object | None = None  # discord.Message — guardado pra apagar só no final
    
    # NOVO: TODOS os identificados como Hospital Sul (banco OU apelido),
    # independente de estar com toggle ligado — é o "identificados no EMS" real.
    reconhecidos: list[MedicoNaChamada] = field(default_factory=list)

    presentes_no_ems_toggle_ligado: list[MedicoNaChamada] = field(default_factory=list)
    toggle_ligado_mas_nao_no_ems: list[MedicoNaChamada] = field(default_factory=list)
    nao_reconhecidos: list[dict] = field(default_factory=list)
    medicos_norte: list[dict] = field(default_factory=list)  # confirmados como Hospital Norte
    bypass_presenca: list[MedicoNaChamada] = field(default_factory=list)


    total_medicos_ems: int = 0
    total_toggle_ligado: int = 0

    membros_conhecidos: list = field(default_factory=list)  # cache de MembroConhecido pra busca manual

    faltantes_ids: set[int] = field(default_factory=set)  # marcados na Etapa 3, ajustável até finalizar
    etapa_atual: int = 1


# Só pode existir 1 sessão ativa por vez (já garantido pelo lock do ControleChamada,
# mas mantemos aqui pra fácil acesso durante os callbacks dos botões)
_sessao_ativa: SessaoChamada | None = None


def definir_sessao(sessao: SessaoChamada | None):
    global _sessao_ativa
    _sessao_ativa = sessao


def obter_sessao() -> SessaoChamada | None:
    return _sessao_ativa