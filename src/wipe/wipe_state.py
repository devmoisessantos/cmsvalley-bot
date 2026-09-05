"""
Estado em memória da execução do wipe.

Só pode haver uma operação destrutiva por vez no processo do bot.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from datetime import datetime


@dataclass
class EstadoDoWipe:
    """Retrato da execução atual (backup ou limpar-cargos)."""

    temporada: str
    iniciador_id: int
    iniciador_nome: str
    fase: str = "iniciando"
    caminho_backup_discord: str | None = None
    caminho_backup_banco: str | None = None
    membros_processados: int = 0
    membros_preservados: int = 0
    membros_limpos: int = 0
    membros_falha: int = 0
    tabelas_esvaziadas: int = 0
    linhas_do_relatorio: list[str] = field(default_factory=list)
    iniciado_em: datetime | None = None
    em_andamento: bool = True


class GuardaDoWipe:
    """Guarda a execução em andamento."""

    def __init__(self) -> None:
        self.estado_execucao: EstadoDoWipe | None = None

    def definir_execucao(self, estado: EstadoDoWipe | None) -> None:
        """Troca o estado da execução."""
        self.estado_execucao = estado

    def obter_execucao(self) -> EstadoDoWipe | None:
        """Estado da execução atual, se houver."""
        return self.estado_execucao

    def execucao_em_andamento(self) -> bool:
        """True se há operação de wipe rodando agora."""
        return self.estado_execucao is not None and self.estado_execucao.em_andamento


guarda_do_wipe = GuardaDoWipe()


def obter_estado_do_wipe() -> EstadoDoWipe | None:
    """Devolve a execução atual."""
    return guarda_do_wipe.obter_execucao()


def definir_estado_do_wipe(estado: EstadoDoWipe | None) -> None:
    """Grava o estado da execução."""
    guarda_do_wipe.definir_execucao(estado)


def wipe_esta_em_andamento() -> bool:
    """True quando backup ou limpar-cargos está rodando."""
    return guarda_do_wipe.execucao_em_andamento()
