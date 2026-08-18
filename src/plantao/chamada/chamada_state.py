"""
Onde fica guardada a chamada que esta acontecendo agora.

So existe uma chamada por vez no servidor. Antes, esse dado ficava numa variavel
`global`, o que a constituicao do projeto proibe. Hoje quem guarda e a classe
`GuardaDaSessaoAtiva`.

As funcoes `definir_sessao` e `obter_sessao` continuam existindo como uma porta
de entrada simples, porque muitos arquivos ja chamavam por esses nomes.
"""

from dataclasses import (
    dataclass,
    field,
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
    print_ems_url: str | None = (
        None  # URL do print do EMS enviado pelo doutor, guardado pra referência
    )
    print_ems_mensagem: object | None = (
        None  # discord.Message — guardado pra apagar só no final
    )

    # NOVO: TODOS os identificados como Hospital Sul (banco OU apelido),
    # independente de estar com toggle ligado — é o "identificados no EMS" real.
    reconhecidos: list[MedicoNaChamada] = field(default_factory=list)

    presentes_no_ems_toggle_ligado: list[MedicoNaChamada] = field(default_factory=list)
    toggle_ligado_mas_nao_no_ems: list[MedicoNaChamada] = field(default_factory=list)
    nao_reconhecidos: list[dict] = field(default_factory=list)
    medicos_norte: list[dict] = field(
        default_factory=list
    )  # confirmados como Hospital Norte
    bypass_presenca: list[MedicoNaChamada] = field(default_factory=list)

    total_medicos_ems: int = 0
    total_toggle_ligado: int = 0

    membros_conhecidos: list = field(
        default_factory=list
    )  # cache de MembroConhecido pra busca manual

    faltantes_ids: set[int] = field(
        default_factory=set
    )  # marcados na Etapa 3, ajustável até finalizar
    etapa_atual: int = 1
    # Trava o botão "Finalizar" pra não enviar a chamada duas vezes
    finalizando: bool = False


class GuardaDaSessaoAtiva:
    """
    Guarda a unica sessao de chamada que pode estar acontecendo no momento.

    Por que uma classe e nao uma variavel solta
    -------------------------------------------
    Antes isso era uma variavel de modulo trocada com `global`. O problema do
    `global` e que, lendo a funcao, ninguem descobre de onde o valor vem nem
    quem mais mexe nele — e num bot com varios botoes acontecendo ao mesmo
    tempo, isso e receita de dor de cabeca.

    Aqui o valor mora dentro de um objeto com nome. Quem le
    `guarda_da_sessao_ativa.definir(...)` sabe exatamente o que esta mudando.

    So existe uma sessao por vez de propósito: o lock do ControleChamada ja
    garante isso, e este guarda serve para os botoes conseguirem alcancar a
    sessao sem passar ela de mao em mao por dez funcoes.
    """

    def __init__(self) -> None:
        self.sessao: SessaoChamada | None = None

    def definir(self, sessao: SessaoChamada | None) -> None:
        """Troca a sessao guardada; passar None limpa a sessao encerrada."""
        self.sessao = sessao

    def obter(self) -> SessaoChamada | None:
        """Devolve a sessao em curso, ou None quando nao ha chamada rolando."""
        return self.sessao


guarda_da_sessao_ativa = GuardaDaSessaoAtiva()


def definir_sessao(sessao: SessaoChamada | None) -> None:
    """
    Substitui a sessao ativa em memoria, inclusive para limpar uma encerrada.

    Continua existindo com este nome porque varios arquivos ja chamam ela
    assim. Por dentro, agora ela so repassa o pedido para o guarda.
    """
    guarda_da_sessao_ativa.definir(sessao)


def obter_sessao() -> SessaoChamada | None:
    """Devolve a sessao ativa ou None quando nenhuma chamada esta em curso."""
    return guarda_da_sessao_ativa.obter()
