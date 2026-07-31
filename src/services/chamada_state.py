from dataclasses import dataclass, field


@dataclass
class MedicoNaChamada:
    id_fivem: str
    discord_id: int | None
    nome_ems: str
    nome_discord: str | None = None
    confianca: float = 1.0
    origem: str = "ocr"  # "ocr" ou "manual"


@dataclass
class SessaoChamada:
    doutor_id: int
    chamada_id: int
    canal_id: int
    mensagem_id: int | None = None

    presentes_no_ems_toggle_ligado: list[MedicoNaChamada] = field(default_factory=list)
    toggle_ligado_mas_nao_no_ems: list[MedicoNaChamada] = field(default_factory=list)
    nao_reconhecidos: list[dict] = field(default_factory=list)  # {"id_fivem", "nome_ems", "confianca"}

    total_medicos_ems: int = 0
    total_toggle_ligado: int = 0


# Só pode existir 1 sessão ativa por vez (já garantido pelo lock do ControleChamada,
# mas mantemos aqui pra fácil acesso durante os callbacks dos botões)
_sessao_ativa: SessaoChamada | None = None


def definir_sessao(sessao: SessaoChamada | None):
    global _sessao_ativa
    _sessao_ativa = sessao


def obter_sessao() -> SessaoChamada | None:
    return _sessao_ativa