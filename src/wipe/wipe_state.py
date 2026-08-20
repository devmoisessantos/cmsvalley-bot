"""
Estado em memória do assistente de wipe e da execução.

Cada administrador monta as escolhas num assistente com timeout longo.
Só pode haver uma execução destrutiva por vez no processo.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from datetime import datetime

ETAPA_INICIO = "inicio"
ETAPA_CANAIS = "canais"
ETAPA_REVISAO_CANAIS = "revisao_canais"
ETAPA_MEMBROS = "membros"
ETAPA_CONFIRMACAO = "confirmacao"


@dataclass
class SessaoDoAssistenteWipe:
    """Escolhas do administrador enquanto configura o wipe."""

    usuario_id: int
    guilda_id: int
    etapa: str = ETAPA_INICIO
    # IDs dos canais de texto que serão apagados e recriados
    ids_canais_para_recriar: set[int] = field(default_factory=set)
    # Pilha para o botão Desfazer (cópias imutáveis do conjunto de IDs)
    historico_marcacoes: list[frozenset[int]] = field(default_factory=list)
    pagina_canais: int = 0
    # Lista ordenada de (canal_id, rotulo) ao abrir o assistente
    catalogo_canais: list[tuple[int, str]] = field(default_factory=list)
    caminho_backup: str | None = None
    criada_em: datetime | None = None


@dataclass
class EstadoDoWipe:
    """Retrato da execução destrutiva (depois da confirmação final)."""

    temporada: str
    iniciador_id: int
    iniciador_nome: str
    fase: str = "iniciando"
    caminho_backup: str | None = None
    membros_expulsos: int = 0
    membros_falha: int = 0
    canais_recriados: int = 0
    mapa_config_novos_ids: dict[str, int] = field(default_factory=dict)
    linhas_do_relatorio: list[str] = field(default_factory=list)
    iniciado_em: datetime | None = None
    em_andamento: bool = True


class GuardaDoWipe:
    """Guarda sessões do assistente e a execução em andamento."""

    def __init__(self) -> None:
        self.sessoes: dict[int, SessaoDoAssistenteWipe] = {}
        self.estado_execucao: EstadoDoWipe | None = None

    def definir_sessao(self, sessao: SessaoDoAssistenteWipe) -> None:
        """Grava a sessão daquele usuário."""
        self.sessoes[sessao.usuario_id] = sessao

    def obter_sessao(self, usuario_id: int) -> SessaoDoAssistenteWipe | None:
        """Devolve a sessão aberta daquele usuário, se existir."""
        return self.sessoes.get(usuario_id)

    def limpar_sessao(self, usuario_id: int) -> None:
        """Remove a sessão do assistente daquele usuário."""
        self.sessoes.pop(usuario_id, None)

    def definir_execucao(self, estado: EstadoDoWipe | None) -> None:
        """Troca o estado da execução destrutiva."""
        self.estado_execucao = estado

    def obter_execucao(self) -> EstadoDoWipe | None:
        """Estado da execução atual, se houver."""
        return self.estado_execucao

    def execucao_em_andamento(self) -> bool:
        """True se há wipe destrutivo rodando agora."""
        return self.estado_execucao is not None and self.estado_execucao.em_andamento


guarda_do_wipe = GuardaDoWipe()


def obter_estado_do_wipe() -> EstadoDoWipe | None:
    """Compatível com status: devolve a execução atual."""
    return guarda_do_wipe.obter_execucao()


def definir_estado_do_wipe(estado: EstadoDoWipe | None) -> None:
    """Grava o estado da execução."""
    guarda_do_wipe.definir_execucao(estado)


def wipe_esta_em_andamento() -> bool:
    """True quando a fase destrutiva está rodando."""
    return guarda_do_wipe.execucao_em_andamento()


def obter_sessao_assistente(usuario_id: int) -> SessaoDoAssistenteWipe | None:
    """Sessão do assistente daquele usuário."""
    return guarda_do_wipe.obter_sessao(usuario_id)


def definir_sessao_assistente(sessao: SessaoDoAssistenteWipe) -> None:
    """Salva a sessão do assistente."""
    guarda_do_wipe.definir_sessao(sessao)


def limpar_sessao_assistente(usuario_id: int) -> None:
    """Encerra a sessão do assistente."""
    guarda_do_wipe.limpar_sessao(usuario_id)


def guardar_marcacao_no_historico(sessao: SessaoDoAssistenteWipe) -> None:
    """Empilha o conjunto atual de canais marcados para o botão Desfazer."""
    foto = frozenset(sessao.ids_canais_para_recriar)
    if sessao.historico_marcacoes and sessao.historico_marcacoes[-1] == foto:
        return
    sessao.historico_marcacoes.append(foto)
    # Evita crescer sem limite numa sessão longa
    if len(sessao.historico_marcacoes) > 40:
        sessao.historico_marcacoes = sessao.historico_marcacoes[-40:]


def desfazer_ultima_marcacao(sessao: SessaoDoAssistenteWipe) -> bool:
    """
    Restaura o conjunto de marcações anterior.

    Devolve True se havia algo para desfazer.
    """
    if not sessao.historico_marcacoes:
        return False
    anterior = sessao.historico_marcacoes.pop()
    sessao.ids_canais_para_recriar = set(anterior)
    return True
