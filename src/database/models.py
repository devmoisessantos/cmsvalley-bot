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
    Text,
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
    lembrete_3_enviado: Mapped[bool] = mapped_column(Boolean, default=False)
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


# ---------------------------------------------------------------------------
# Laudos psicológicos (porte de arma)
# ---------------------------------------------------------------------------


class ConsultaLaudo(Base):
    """Consulta psicológica em andamento ou finalizada."""

    __tablename__ = "consultas_laudo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id_psicologo: Mapped[int] = mapped_column(BigInteger, index=True)
    discord_id_paciente: Mapped[int] = mapped_column(BigInteger, index=True)
    id_fivem_psicologo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    id_fivem_paciente: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # ABERTA | FINALIZADA | CANCELADA
    status: Mapped[str] = mapped_column(String(20), default="ABERTA", index=True)
    iniciada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )
    finalizada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Laudo(Base):
    """Laudo psicológico gerado ao final de uma consulta."""

    __tablename__ = "laudos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consulta_id: Mapped[int] = mapped_column(Integer, index=True)
    discord_id_psicologo: Mapped[int] = mapped_column(BigInteger, index=True)
    discord_id_paciente: Mapped[int] = mapped_column(BigInteger, index=True)
    id_fivem_psicologo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    id_fivem_paciente: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # APROVADO | REPROVADO
    parecer: Mapped[str] = mapped_column(String(20))
    motivo: Mapped[str] = mapped_column(String(1500))
    registro_profissional: Mapped[str] = mapped_column(String(80))
    canal_laudo_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


# ---------------------------------------------------------------------------
# Baú do Hospital — contadores, casos e advertências verbais
# ---------------------------------------------------------------------------


class ContadorItemBau(Base):
    """Quantidade líquida retirada por item no ciclo atual (por passaporte FiveM)."""

    __tablename__ = "contadores_item_bau"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_fivem: Mapped[str] = mapped_column(String(20), index=True)
    nome_cidade: Mapped[str | None] = mapped_column(String(120), nullable=True)
    item_canonico: Mapped[str] = mapped_column(String(40), index=True)
    quantidade: Mapped[int] = mapped_column(Integer, default=0)
    ciclo_chave: Mapped[str] = mapped_column(
        String(32), index=True
    )  # ex: 2026-08-10_00
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )


class CasoBau(Base):
    """
    Caso de excesso de baú — um por passaporte enquanto aberto.
    Sobrevive a reset de ciclo até ser resolvido.
    itens_json guarda a dívida agregada: {"roupas": 30, "radio": 13, ...}
    """

    __tablename__ = "casos_bau"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_fivem: Mapped[str] = mapped_column(String(20), index=True)
    nome_cidade: Mapped[str | None] = mapped_column(String(120), nullable=True)
    discord_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    # Legado / resumo: "agregado" nos casos novos; item único em registros antigos
    item_canonico: Mapped[str] = mapped_column(
        String(40), index=True, default="agregado"
    )
    quantidade_atual: Mapped[int] = mapped_column(Integer, default=0)  # soma dos itens
    # JSON: {"item": quantidade, ...} — dívida que precisa ser devolvida
    itens_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AGUARDANDO | GRAVE | PRAZO_ESTOURADO | RESOLVIDO | IGNORADO | PUNIDO
    status: Mapped[str] = mapped_column(String(20), default="AGUARDANDO", index=True)
    e_grave: Mapped[bool] = mapped_column(Boolean, default=False)
    dm_falhou: Mapped[bool] = mapped_column(Boolean, default=False)
    dm_enviada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expira_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    canal_alerta_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    motivo_ignore: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolvido_por: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolvido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )


class AdvertenciaVerbalBau(Base):
    """Prontuário permanente de advertências verbais do baú (nunca reseta)."""

    __tablename__ = "advertencias_verbais_bau"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_fivem: Mapped[str] = mapped_column(String(20), index=True)
    discord_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    nome_cidade: Mapped[str | None] = mapped_column(String(120), nullable=True)
    caso_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_canonico: Mapped[str | None] = mapped_column(String(40), nullable=True)
    motivo: Mapped[str] = mapped_column(String(500))
    # VERBAL | ADV1_ESCALADA
    tipo: Mapped[str] = mapped_column(String(20), default="VERBAL")
    automatica: Mapped[bool] = mapped_column(Boolean, default=True)
    aplicada_por: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class ConfigBau(Base):
    """Overrides de configuração do baú (limites, tolerância) editáveis pelo painel admin."""

    __tablename__ = "config_bau"

    chave: Mapped[str] = mapped_column(String(80), primary_key=True)
    valor: Mapped[str] = mapped_column(String(200))
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )
    atualizado_por: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ---------------------------------------------------------------------------
# Cursos e promoções
# ---------------------------------------------------------------------------


class SolicitacaoCurso(Base):
    """
    Pedido de um ou mais cursos.
    Fluxo: AGENDADO → ACEITO → APROVADO | REPROVADO
    """

    __tablename__ = "solicitacoes_curso"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # Legado / resumo: primeira chave ou "pacote"
    chave_curso: Mapped[str] = mapped_column(String(40), index=True)
    # JSON: ["alpinista", "arcanjo", ...]
    chaves_cursos_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_ingame: Mapped[int] = mapped_column(Integer, default=0)
    moedas_debitadas: Mapped[int] = mapped_column(Integer, default=0)
    # MOEDAS | IN_GAME | GRATUITO
    forma_pagamento: Mapped[str] = mapped_column(String(20), default="MOEDAS")
    # AGENDADO | ACEITO | APROVADO | REPROVADO | CANCELADO
    status: Mapped[str] = mapped_column(String(20), default="AGENDADO", index=True)
    observacao_aluno: Mapped[str | None] = mapped_column(Text, nullable=True)
    observacao_instrutor: Mapped[str | None] = mapped_column(Text, nullable=True)
    instrutor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    aplicado_por: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mensagem_canal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mensagem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )


class SolicitacaoPromocao(Base):
    """Pedido de promoção aguardando diretoria."""

    __tablename__ = "solicitacoes_promocao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chave_trilha: Mapped[str] = mapped_column(String(60), index=True)
    cargo_de: Mapped[str] = mapped_column(String(80))
    cargo_para: Mapped[str] = mapped_column(String(80))
    # PENDENTE | APROVADA | REPROVADA | CANCELADA
    status: Mapped[str] = mapped_column(String(20), default="PENDENTE", index=True)
    resumo_checklist: Mapped[str | None] = mapped_column(Text, nullable=True)
    motivo_reprovacao: Mapped[str | None] = mapped_column(String(500), nullable=True)
    analisado_por: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mensagem_canal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mensagem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )


class HistoricoPromocao(Base):
    """Registro permanente de promoção ou rebaixamento."""

    __tablename__ = "historico_promocoes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # PROMOCAO | REBAIXAMENTO | NAO_PROMOVIDO
    tipo: Mapped[str] = mapped_column(String(20), index=True)
    cargo_de: Mapped[str | None] = mapped_column(String(80), nullable=True)
    cargo_para: Mapped[str | None] = mapped_column(String(80), nullable=True)
    motivo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    executado_por: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    solicitacao_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


# ---------------------------------------------------------------------------
# Carteira de moedas (plantão)
# ---------------------------------------------------------------------------


class MovimentacaoMoeda(Base):
    """Extrato de moedas: ganho, transferência, troca, depósito, ajuste."""

    __tablename__ = "movimentacoes_moedas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # GANHO_PLANTAO | TRANSFERENCIA_ENVIADA | TRANSFERENCIA_RECEBIDA
    # | TROCA_INGAME | DEPOSITO | AJUSTE_STAFF
    tipo: Mapped[str] = mapped_column(String(40), index=True)
    # positivo = crédito, negativo = débito (do ponto de vista do discord_id)
    valor: Mapped[int] = mapped_column(Integer)
    saldo_apos: Mapped[int] = mapped_column(Integer, default=0)
    outro_discord_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    referencia: Mapped[str | None] = mapped_column(String(200), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora, index=True
    )


class SolicitacaoDemissao(Base):
    """Pedido de demissão voluntária (hierarquia → Visitantes)."""

    __tablename__ = "solicitacoes_demissao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    membro_nome: Mapped[str] = mapped_column(String(120))
    cargo: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tipo_demissao: Mapped[str] = mapped_column(String(40), default="voluntaria")
    motivo: Mapped[str] = mapped_column(Text)
    data_solicitacao: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )
    data_efetiva: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    solicitante_nome: Mapped[str] = mapped_column(String(120))
    advertencias: Mapped[int] = mapped_column(Integer, default=0)
    # pendente | aprovada | negada
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    aprovado_por_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    aprovado_por_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mensagem_canal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mensagem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )


class PedidoDepositoMoeda(Base):
    """Pedido de depósito: $ in-game → moedas (aprovação staff)."""

    __tablename__ = "pedidos_deposito_moedas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    id_fivem: Mapped[str | None] = mapped_column(String(20), nullable=True)
    quantidade_moedas: Mapped[int] = mapped_column(Integer)
    valor_ingame: Mapped[int] = mapped_column(Integer, default=0)
    observacao: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # PENDENTE | APROVADO | RECUSADO
    status: Mapped[str] = mapped_column(String(20), default="PENDENTE", index=True)
    analisado_por: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    motivo_recusa: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mensagem_canal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mensagem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora
    )


class Ticket(Base):
    """Ticket de suporte / denúncia (canal privado + transcript)."""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # chave interna da categoria (ex: suporte_duvidas, denuncias_jogador)
    categoria_chave: Mapped[str] = mapped_column(String(60), index=True)
    categoria_rotulo: Mapped[str] = mapped_column(String(120))
    # aberto | assumido | finalizado
    status: Mapped[str] = mapped_column(String(20), default="aberto", index=True)

    autor_discord_id: Mapped[int] = mapped_column(BigInteger, index=True)
    autor_nome: Mapped[str] = mapped_column(String(120))

    canal_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    mensagem_painel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # mensagem do card 3 (botões de staff) — usada para atualizar Assumir / Call
    mensagem_botoes_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # canal de voz criado pelo botão Criar Call
    call_canal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    staff_assumiu_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    staff_assumiu_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    staff_finalizou_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    staff_finalizou_nome: Mapped[str | None] = mapped_column(String(120), nullable=True)

    consideracoes_finais: Mapped[str | None] = mapped_column(Text, nullable=True)
    senha_transcript: Mapped[str | None] = mapped_column(String(40), nullable=True)
    url_transcript: Mapped[str | None] = mapped_column(String(500), nullable=True)

    aberto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora, index=True
    )
    assumido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finalizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
