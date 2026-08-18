"""Estado em memória do fluxo de aplicação de advertência (sessão ephemeral)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessaoAdvertencia:
    """Dados acumulados nas etapas antes de confirmar a punição."""

    executor_id: int
    membro_id: int | None = None
    membro_mention: str | None = None
    id_fivem: str | None = None
    cargo_nome: str | None = None
    cargo_id: int | None = None

    @property
    def etapa1_ok(self) -> bool:
        """
        Indica se a sessão já tem um membro escolhido para evitar punição sem alvo.
        """
        return self.membro_id is not None

    @property
    def etapa2_ok(self) -> bool:
        """Indica se o FiveM foi confirmado antes de avançar no fluxo disciplinar."""
        return bool(self.id_fivem)

    @property
    def pode_aplicar(self) -> bool:
        """Exige as duas etapas para impedir confirmar uma advertência incompleta."""
        return self.etapa1_ok and self.etapa2_ok


# sessões por usuário executor (discord_id → sessão)
_sessoes: dict[int, SessaoAdvertencia] = {}


def obter_sessao(executor_id: int) -> SessaoAdvertencia:
    """Recupera ou cria a sessão isolada do executor para não misturar atendimentos."""
    if executor_id not in _sessoes:
        _sessoes[executor_id] = SessaoAdvertencia(executor_id=executor_id)
    return _sessoes[executor_id]


def limpar_sessao(executor_id: int) -> None:
    """
    Descarta os dados temporários do executor após cancelar ou concluir a advertência.
    """
    _sessoes.pop(executor_id, None)
