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
    pass


# Função de data atual
def agora() -> datetime:
    return datetime.now(timezone.utc)  # Forma moderna e correta


class Usuario(Base):
    __tablename__ = "usuarios"

    id_fivem: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 👈 novo
    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nickname_atual: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="VISITANTE")
    ja_foi_aprovado: Mapped[bool] = mapped_column(Boolean, default=False)
    data_ultima_reprovacao: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    recrutamentos: Mapped[list["Recrutamento"]] = relationship(
        back_populates="candidato"
    )
    historico_cargos: Mapped[list["HistoricoCargo"]] = relationship(
        back_populates="usuario"
    )


class Recrutamento(Base):
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

    status: Mapped[str] = mapped_column(String(30), default="ESTUDANDO")
    # ESTUDANDO, EM_PROVA, APROVADO, REPROVADO, REPROVADO_TEMPO

    pergunta_atual: Mapped[int] = mapped_column(
        Integer, default=0
    )  # controla o progresso (0 a 11)
    formulario_aberto: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # trava reabertura indevida

    nota_percentual: Mapped[float | None] = mapped_column(Float, nullable=True)
    acertos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cargo_final: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )  # ENFERMEIRO / PARAMEDICO
    candidato: Mapped["Usuario"] = relationship(back_populates="recrutamentos")
    respostas: Mapped[list["RespostaProva"]] = relationship(
        back_populates="recrutamento"
    )


class Pergunta(Base):
    __tablename__ = "perguntas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ordem: Mapped[int] = mapped_column(Integer, unique=True)
    enunciado: Mapped[str] = mapped_column(String(500))
    opcoes: Mapped[str] = mapped_column(
        String(1000)
    )  # JSON: lista de textos das alternativas
    resposta_correta: Mapped[str] = mapped_column(
        String(1)
    )  # letra correspondente à posição (A, B, C...)


class RespostaProva(Base):
    __tablename__ = "respostas_prova"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recrutamento_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("recrutamentos.id")
    )
    numero_pergunta: Mapped[int] = mapped_column(Integer)
    resposta_escolhida: Mapped[str] = mapped_column(String(200))
    correta: Mapped[bool] = mapped_column(Boolean)

    recrutamento: Mapped["Recrutamento"] = relationship(back_populates="respostas")


class HistoricoCargo(Base):
    __tablename__ = "historico_cargos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuarios.discord_id")
    )
    cargo: Mapped[str] = mapped_column(String(50))
    acao: Mapped[str] = mapped_column(String(20))  # ADICIONADO / REMOVIDO
    executor_id: Mapped[int] = mapped_column(BigInteger)  # ID do bot ou do recrutador
    data_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)

    usuario: Mapped["Usuario"] = relationship(back_populates="historico_cargos")


class PainelPostado(Base):
    __tablename__ = "paineis_postados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome_painel: Mapped[str] = mapped_column(String(50), unique=True)
    canal_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)


class MensagemHierarquia(Base):
    __tablename__ = "mensagens_hierarquia"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cargo_id: Mapped[int] = mapped_column(BigInteger)
    pagina: Mapped[int] = mapped_column(Integer, default=1)  # ← NOVO
    canal_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)

    # 🔥 Nova constraint: um cargo não pode ter duas mensagens com a mesma página
    __table_args__ = (UniqueConstraint("cargo_id", "pagina"),)


class EstadoPlantao(Base):
    __tablename__ = "estado_plantao"

    id_fivem: Mapped[str | None] = mapped_column(String(20), nullable=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    toggle_ligado: Mapped[bool] = mapped_column(Boolean, default=False)

    # Preenchidos apenas enquanto o médico está DENTRO de uma call válida
    em_call_valida: Mapped[bool] = mapped_column(Boolean, default=False)
    call_entrada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canal_atual_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Contador que "enche" até 1800s e reseta, gerando moeda
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
    """Tabela singleton (1 linha só, id=1) — controla o cooldown global e o lock de concorrência."""

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
    __tablename__ = "chamadas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doutor_id: Mapped[int] = mapped_column(BigInteger)
    total_medicos_ems: Mapped[int] = mapped_column(Integer, default=0)
    total_toggle_ligado: Mapped[int] = mapped_column(Integer, default=0)
    total_presentes: Mapped[int] = mapped_column(Integer, default=0)
    total_ausentes: Mapped[int] = mapped_column(Integer, default=0)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class FaltaChamada(Base):
    __tablename__ = "faltas_chamada"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chamada_id: Mapped[int] = mapped_column(Integer)
    motivo: Mapped[str] = mapped_column(String(100))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class EventosGate(Base):
    __tablename__ = "eventos_gate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    tipo: Mapped[str] = mapped_column(String(20))  # "treino" | "facxfac" | "dominas"
    titulo: Mapped[str] = mapped_column(String(120))

    # Dados do formulário
    data_evento: Mapped[str] = mapped_column(String(20))  # "15/06/2026"
    horario: Mapped[str] = mapped_column(String(20))  # "20:00"
    limite_participantes: Mapped[int] = mapped_column(
        Integer, default=0
    )  # 0 = sem limite
    adversario: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )  # só FacxFac

    # Controle
    status: Mapped[str] = mapped_column(
        String(20), default="aberto"
    )  # aberto | encerrado
    criado_por: Mapped[int] = mapped_column(BigInteger)  # discord_id do staff
    responsavel_id: Mapped[int] = mapped_column(
        BigInteger
    )  # quem aparece como responsável

    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    log_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=agora
    )

    presencas: Mapped[list["Presenca"]] = relationship(
        back_populates="evento", cascade="all, delete-orphan"
    )


class Presenca(Base):
    __tablename__ = "presencas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evento_id: Mapped[int] = mapped_column(ForeignKey("eventos_gate.id"))

    discord_id: Mapped[int] = mapped_column(BigInteger)
    id_fivem: Mapped[int] = mapped_column(Integer)

    confirmado: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )

    evento: Mapped["EventosGate"] = relationship(back_populates="presencas")


class RankingHistorico(Base):
    """Snapshot de cada ranking postado (semanal ou mensal) para consulta posterior."""

    __tablename__ = "ranking_historico"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(String(40))  # "semanal" | "mensal"
    periodo_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    periodo_fim: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_recrutamentos: Mapped[int] = mapped_column(Integer, default=0)
    total_pago: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(String(4000), default="{}")
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Punicao(Base):
    """Registro de advertência / punição aplicada a um membro."""

    __tablename__ = "punicoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    id_fivem: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cargo_id: Mapped[int] = mapped_column(BigInteger)  # role de punição aplicada
    cargo_nome: Mapped[str] = mapped_column(String(80))
    motivo: Mapped[str] = mapped_column(String(1500))
    links: Mapped[str | None] = mapped_column(
        String(2000), nullable=True
    )  # um link por linha
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
