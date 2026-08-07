# src/database/models.py
"""
Modelos SQLAlchemy do CMS Valley Bot.

Organização por domínio:
  - Base e utilitário de data
  - Usuário e histórico de cargos
  - Recrutamento e prova
  - Painéis e hierarquia
  - Plantão e chamada
  - GATE (eventos e presença)
  - Ranking
  - Punições
  - Snapshot de cargos (rejoin)
"""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """Classe base de todos os modelos."""

    pass


def agora() -> datetime:
    """Retorna o momento atual em UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Usuário e histórico de cargos
# ---------------------------------------------------------------------------


class Usuario(Base):
    """Membro conhecido pelo bot (chave = discord_id)."""

    __tablename__ = "usuarios"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    id_fivem: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nickname_atual: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="VISITANTE")
    ja_foi_aprovado: Mapped[bool] = mapped_column(Boolean, default=False)
    data_ultima_reprovacao: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    recrutamentos: Mapped[list[Recrutamento]] = relationship(back_populates="candidato")
    historico_cargos: Mapped[list[HistoricoCargo]] = relationship(
        back_populates="usuario"
    )


class HistoricoCargo(Base):
    """Registro de cargo adicionado ou removido de um membro."""

    __tablename__ = "historico_cargos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuarios.discord_id")
    )
    cargo: Mapped[str] = mapped_column(String(50))
    # ADICIONADO / REMOVIDO
    acao: Mapped[str] = mapped_column(String(20))
    executor_id: Mapped[int] = mapped_column(BigInteger)
    data_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)

    usuario: Mapped[Usuario] = relationship(back_populates="historico_cargos")


# ---------------------------------------------------------------------------
# Recrutamento e prova
# ---------------------------------------------------------------------------


class Recrutamento(Base):
    """Processo de recrutamento de um candidato."""

    __tablename__ = "recrutamentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_fivem: Mapped[str | None] = mapped_column(String(20), nullable=True)
    discord_id_candidato: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuarios.discord_id")
    )
    discord_id_recrutador: Mapped[int] = mapped_column(BigInteger)

    data_inicio: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )
    data_inicio_prova: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    data_fim: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ESTUDANDO | EM_PROVA | APROVADO | REPROVADO | REPROVADO_TEMPO
    status: Mapped[str] = mapped_column(String(30), default="ESTUDANDO")
    # Controla o progresso na prova (0 a N perguntas)
    pergunta_atual: Mapped[int] = mapped_column(Integer, default=0)
    # Impede reabrir o formulário indevidamente
    formulario_aberto: Mapped[bool] = mapped_column(Boolean, default=False)

    nota_percentual: Mapped[float | None] = mapped_column(Float, nullable=True)
    acertos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ENFERMEIRO / PARAMEDICO (quando aplicável)
    cargo_final: Mapped[str | None] = mapped_column(String(30), nullable=True)

    candidato: Mapped[Usuario] = relationship(back_populates="recrutamentos")
    respostas: Mapped[list[RespostaProva]] = relationship(back_populates="recrutamento")


class Pergunta(Base):
    """Pergunta da prova de recrutamento."""

    __tablename__ = "perguntas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ordem: Mapped[int] = mapped_column(Integer, unique=True)
    enunciado: Mapped[str] = mapped_column(String(500))
    # JSON: lista de textos das alternativas
    opcoes: Mapped[str] = mapped_column(String(1000))
    # Letra da alternativa correta (A, B, C...)
    resposta_correta: Mapped[str] = mapped_column(String(1))


class RespostaProva(Base):
    """Resposta dada pelo candidato em uma pergunta da prova."""

    __tablename__ = "respostas_prova"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recrutamento_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("recrutamentos.id")
    )
    numero_pergunta: Mapped[int] = mapped_column(Integer)
    resposta_escolhida: Mapped[str] = mapped_column(String(200))
    correta: Mapped[bool] = mapped_column(Boolean)

    recrutamento: Mapped[Recrutamento] = relationship(back_populates="respostas")


# ---------------------------------------------------------------------------
# Painéis persistentes e hierarquia
# ---------------------------------------------------------------------------


class PainelPostado(Base):
    """Guarda canal + message_id de cada painel persistente do bot."""

    __tablename__ = "paineis_postados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome_painel: Mapped[str] = mapped_column(String(50), unique=True)
    canal_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)


class MensagemHierarquia(Base):
    """Mensagem publicada da hierarquia (pode haver várias páginas por cargo)."""

    __tablename__ = "mensagens_hierarquia"
    __table_args__ = (UniqueConstraint("cargo_id", "pagina"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cargo_id: Mapped[int] = mapped_column(BigInteger)
    pagina: Mapped[int] = mapped_column(Integer, default=1)
    canal_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)


# ---------------------------------------------------------------------------
# Plantão e chamada
# ---------------------------------------------------------------------------


class EstadoPlantao(Base):
    """Estado atual de plantão de um membro (toggle, call, moedas, AFK)."""

    __tablename__ = "estado_plantao"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    id_fivem: Mapped[str | None] = mapped_column(String(20), nullable=True)
    toggle_ligado: Mapped[bool] = mapped_column(Boolean, default=False)

    # Preenchidos só enquanto o membro está em uma call válida
    em_call_valida: Mapped[bool] = mapped_column(Boolean, default=False)
    call_entrada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canal_atual_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Contador que enche até o limite e gera moeda
    segmento_iniciado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    segundos_acumulados: Mapped[int] = mapped_column(Integer, default=0)
    saldo_moedas: Mapped[int] = mapped_column(Integer, default=0)

    ultima_atualizacao: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora, onupdate=agora
    )
    ocioso_desde: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lembrete_1_enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    lembrete_2_enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    modo_coordenacao: Mapped[bool] = mapped_column(Boolean, default=False)
    afk_mudo_surdo_desde: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    afk_canal_referencia_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    afk_aviso_enviado: Mapped[bool] = mapped_column(Boolean, default=False)


class LogPlantao(Base):
    """Evento registrado do sistema de plantão (ligar toggle, sair da call, etc.)."""

    __tablename__ = "log_plantao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_fivem: Mapped[str | None] = mapped_column(String(20), nullable=True)
    discord_id: Mapped[int] = mapped_column(BigInteger)
    evento: Mapped[str] = mapped_column(String(30))
    canal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duracao_segundos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detalhes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class ControleChamada(Base):
    """
    Tabela singleton (uma linha só, id=1).

    Controla cooldown global e trava de concorrência da chamada.
    """

    __tablename__ = "controle_chamada"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    ultima_chamada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    chamada_em_andamento: Mapped[bool] = mapped_column(Boolean, default=False)
    doutor_em_chamada_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    chamada_iniciada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Chamada(Base):
    """Registro de uma chamada de presença realizada."""

    __tablename__ = "chamadas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doutor_id: Mapped[int] = mapped_column(BigInteger)
    total_medicos_ems: Mapped[int] = mapped_column(Integer, default=0)
    total_toggle_ligado: Mapped[int] = mapped_column(Integer, default=0)
    total_presentes: Mapped[int] = mapped_column(Integer, default=0)
    total_ausentes: Mapped[int] = mapped_column(Integer, default=0)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class FaltaChamada(Base):
    """Falta registrada em uma chamada de presença."""

    __tablename__ = "faltas_chamada"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chamada_id: Mapped[int] = mapped_column(Integer)
    motivo: Mapped[str] = mapped_column(String(100))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


# ---------------------------------------------------------------------------
# GATE — eventos e presença
# ---------------------------------------------------------------------------


class EventosGate(Base):
    """Evento da unidade GATE (treino, facxfac, dominas)."""

    __tablename__ = "eventos_gate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # treino | facxfac | dominas
    tipo: Mapped[str] = mapped_column(String(20))
    titulo: Mapped[str] = mapped_column(String(120))

    data_evento: Mapped[str] = mapped_column(String(20))
    horario: Mapped[str] = mapped_column(String(20))
    # 0 = sem limite
    limite_participantes: Mapped[int] = mapped_column(Integer, default=0)
    # Só usado em FacxFac
    adversario: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # aberto | encerrado
    status: Mapped[str] = mapped_column(String(20), default="aberto")
    criado_por: Mapped[int] = mapped_column(BigInteger)
    responsavel_id: Mapped[int] = mapped_column(BigInteger)

    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    log_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    # Só preenchido quando o evento é encerrado
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    presencas: Mapped[list[Presenca]] = relationship(
        back_populates="evento",
        cascade="all, delete-orphan",
    )


class Presenca(Base):
    """Presença de um membro em um evento GATE."""

    __tablename__ = "presencas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evento_id: Mapped[int] = mapped_column(ForeignKey("eventos_gate.id"))

    discord_id: Mapped[int] = mapped_column(BigInteger)
    id_fivem: Mapped[int] = mapped_column(Integer)

    confirmado: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )

    evento: Mapped[EventosGate] = relationship(back_populates="presencas")


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


class RankingHistorico(Base):
    """Snapshot de cada ranking postado (semanal ou mensal) para consulta posterior."""

    __tablename__ = "ranking_historico"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # semanal | mensal
    tipo: Mapped[str] = mapped_column(String(40))
    periodo_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    periodo_fim: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_recrutamentos: Mapped[int] = mapped_column(Integer, default=0)
    total_pago: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(String(4000), default="{}")
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


# ---------------------------------------------------------------------------
# Punições
# ---------------------------------------------------------------------------


class Punicao(Base):
    """Registro de advertência ou punição aplicada a um membro."""

    __tablename__ = "punicoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    id_fivem: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cargo_id: Mapped[int] = mapped_column(BigInteger)
    cargo_nome: Mapped[str] = mapped_column(String(80))
    motivo: Mapped[str] = mapped_column(String(1500))
    # Um link por linha
    links: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    executor_id: Mapped[int] = mapped_column(BigInteger)
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    removida_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    removida_por: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    motivo_remocao: Mapped[str | None] = mapped_column(String(500), nullable=True)


# ---------------------------------------------------------------------------
# Snapshot de cargos (rejoin automático)
# ---------------------------------------------------------------------------


class SnapshotCargosMembro(Base):
    """
    Último estado conhecido dos cargos de um membro.

    Usado no on_member_join para reaplicar cargos e apelido.

    role_ids e role_names ficam como texto JSON, por exemplo:
      '[1486..., 1487...]'  e  '["Doutor", "Plantão"]'

    Os atributos Python são role_ids / role_names (usados em member_snapshot).
    As colunas no banco podem se chamar cargo_ids / cargo_nomes (legado).
    """

    __tablename__ = "snapshot_cargos_membro"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # Mapeia atributo role_ids → coluna cargo_ids (compatível com banco já criado)
    role_ids: Mapped[str] = mapped_column("cargo_ids", String(2000), default="[]")
    role_names: Mapped[str | None] = mapped_column(
        "cargo_nomes", String(2000), nullable=True
    )
    nickname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )
