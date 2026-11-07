# src/database/migracoes.py
"""
Lista das mudancas de estrutura do banco, uma por uma, com nome e numero.

O problema que este arquivo resolve
-----------------------------------
Antes, as mudancas de coluna estavam escritas soltas dentro de `init_db()`, no
meio da funcao que cria as tabelas. Isso trazia tres dores:

1. Ninguem sabia quais mudancas ja tinham rodado no banco de producao.
2. Para acrescentar uma coluna nova, era preciso mexer no arquivo de conexao.
3. Se uma mudanca falhasse, o bot subia como se nada tivesse acontecido.

Agora cada mudanca e um item desta lista, com numero e descricao em portugues.
O banco guarda numa tabela propria quais numeros ja rodaram, entao cada mudanca
roda **uma vez so** — mesmo que o bot reinicie dez vezes por dia.

Como acrescentar uma mudanca nova
---------------------------------
Escreva um item novo no fim da lista MIGRACOES, com o proximo numero livre, uma
descricao em portugues explicando o porque, e o comando SQL. Nunca mude nem
apague um item que ja foi para producao: o banco de la ja registrou aquele
numero como feito, e reescrever o item deixaria os dois bancos diferentes.

Por que os comandos sao "IF NOT EXISTS"
---------------------------------------
Cinto e suspensorio. A tabela de controle ja impede rodar duas vezes, mas se
alguem tiver aplicado a coluna a mao no banco, o comando nao explode.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migracao:
    """Uma mudanca de estrutura do banco, com numero, motivo e comando."""

    numero: int
    descricao: str
    comando_sql: str


MIGRACOES: list[Migracao] = [
    Migracao(
        numero=1,
        descricao=(
            "Guarda o id da mensagem de botoes do ticket, para o bot conseguir "
            "editar aquela mensagem depois sem precisar procurar no canal."
        ),
        comando_sql=(
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS mensagem_botoes_id BIGINT"
        ),
    ),
    Migracao(
        numero=2,
        descricao=(
            "Guarda o id da call de atendimento criada para o ticket, para "
            "conseguir apagar a call quando o ticket for fechado."
        ),
        comando_sql="ALTER TABLE tickets ADD COLUMN IF NOT EXISTS call_canal_id BIGINT",
    ),
    Migracao(
        numero=3,
        descricao=(
            "Marca se o membro ja foi saudado no ticket, para o bot nao mandar "
            "a mensagem de boas-vindas duas vezes."
        ),
        comando_sql=(
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS saudado BOOLEAN DEFAULT FALSE"
        ),
    ),
    Migracao(
        numero=4,
        descricao=(
            "Tabela de solicitacoes de troca de moedas: guarda o Discord ID do "
            "beneficiario e o id da mensagem no canal de financas, para a DM "
            "com comprovante continuar funcionando apos restart do bot."
        ),
        comando_sql="""
CREATE TABLE IF NOT EXISTS solicitacoes_troca_moedas (
    id SERIAL PRIMARY KEY,
    discord_id_beneficiario BIGINT NOT NULL,
    id_fivem VARCHAR(20),
    quantidade_moedas INTEGER NOT NULL,
    valor_ingame INTEGER NOT NULL,
    canal_id BIGINT NOT NULL,
    mensagem_id BIGINT NOT NULL UNIQUE,
    titulo VARCHAR(200) NOT NULL,
    corpo TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pendente',
    pago_por_id BIGINT,
    pago_em TIMESTAMPTZ,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""",
    ),
    Migracao(
        numero=5,
        descricao=(
            "Tabela de solicitacoes de ingresso na GATE: candidato Paramédico "
            "pede entrada; Comandante/Subcomandante aprova ou reprova."
        ),
        comando_sql="""
CREATE TABLE IF NOT EXISTS solicitacoes_ingresso_gate (
    id SERIAL PRIMARY KEY,
    discord_id_candidato BIGINT NOT NULL,
    discord_id_recrutador BIGINT,
    status VARCHAR(20) NOT NULL DEFAULT 'pendente',
    canal_id BIGINT,
    mensagem_id BIGINT,
    motivo_reprovacao VARCHAR(500),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decidido_em TIMESTAMPTZ
)
""",
    ),
    Migracao(
        numero=6,
        descricao=(
            "Tetos diário/semanal de moedas de plantão: contadores em "
            "estado_plantao (horas continuam contando após o teto)."
        ),
        comando_sql=(
            "ALTER TABLE estado_plantao "
            "ADD COLUMN IF NOT EXISTS moedas_ganhas_no_dia "
            "INTEGER NOT NULL DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS data_moedas_dia VARCHAR(10), "
            "ADD COLUMN IF NOT EXISTS moedas_ganhas_na_semana "
            "INTEGER NOT NULL DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS chave_semana_moedas VARCHAR(16)"
        ),
    ),
    Migracao(
        numero=7,
        descricao=(
            "Curso: cotação do desconto, flag de repasse registrado e "
            "forma_pagamento maior (IN_GAME_COM_DESCONTO)."
        ),
        comando_sql=(
            "ALTER TABLE solicitacoes_curso "
            "ADD COLUMN IF NOT EXISTS cotacao_moeda INTEGER NOT NULL DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS repasse_registrado "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        ),
    ),
    Migracao(
        numero=8,
        descricao=(
            "Curso: rastreio de repasses parciais (práticos juntos, "
            "área um a um) em repasses_json."
        ),
        comando_sql=(
            "ALTER TABLE solicitacoes_curso ADD COLUMN IF NOT EXISTS repasses_json TEXT"
        ),
    ),
    Migracao(
        numero=9,
        descricao=(
            "Punições: origem (MANUAL/CHAMADA/SISTEMA) e expira_em "
            "para baixar ADV VERBAL automaticamente após 3 dias."
        ),
        comando_sql=(
            "ALTER TABLE punicoes "
            "ADD COLUMN IF NOT EXISTS origem VARCHAR(30) "
            "NOT NULL DEFAULT 'MANUAL', "
            "ADD COLUMN IF NOT EXISTS expira_em TIMESTAMPTZ"
        ),
    ),
    Migracao(
        numero=10,
        descricao=(
            "Punições: guarda os cargos do membro antes da exoneração "
            "(JSON de IDs), para devolvê-los se o recurso for aceito."
        ),
        comando_sql=(
            "ALTER TABLE punicoes ADD COLUMN IF NOT EXISTS cargos_antes_json TEXT"
        ),
    ),
    Migracao(
        numero=11,
        descricao=(
            "Laudos: colunas da prova teórica de porte na consulta "
            "(status, datas, acertos e parecer sugerido)."
        ),
        # Um único ALTER — asyncpg não executa vários comandos no mesmo execute.
        comando_sql=(
            "ALTER TABLE consultas_laudo "
            "ADD COLUMN IF NOT EXISTS status_prova VARCHAR(20) "
            "NOT NULL DEFAULT 'NAO_LIBERADA', "
            "ADD COLUMN IF NOT EXISTS prova_liberada_em TIMESTAMPTZ, "
            "ADD COLUMN IF NOT EXISTS prova_iniciada_em TIMESTAMPTZ, "
            "ADD COLUMN IF NOT EXISTS prova_finalizada_em TIMESTAMPTZ, "
            "ADD COLUMN IF NOT EXISTS pergunta_atual INTEGER "
            "NOT NULL DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS acertos_prova INTEGER, "
            "ADD COLUMN IF NOT EXISTS parecer_sugerido VARCHAR(20)"
        ),
    ),
    Migracao(
        numero=12,
        descricao=("Laudos: tabela de respostas da prova teórica de porte."),
        comando_sql="""
CREATE TABLE IF NOT EXISTS respostas_prova_laudo (
    id SERIAL PRIMARY KEY,
    consulta_id INTEGER NOT NULL,
    numero_pergunta INTEGER NOT NULL,
    resposta_escolhida VARCHAR(1) NOT NULL,
    correta BOOLEAN NOT NULL DEFAULT FALSE,
    respondida_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""",
    ),
    Migracao(
        numero=13,
        descricao=("Laudos: índice de consulta_id em respostas_prova_laudo."),
        comando_sql=(
            "CREATE INDEX IF NOT EXISTS "
            "ix_respostas_prova_laudo_consulta_id "
            "ON respostas_prova_laudo (consulta_id)"
        ),
    ),
    Migracao(
        numero=14,
        descricao=(
            "Carteira: lotes de moedas com cotação congelada no crédito. "
            "Troca e desconto consomem do menor valor para o maior."
        ),
        comando_sql="""
CREATE TABLE IF NOT EXISTS lotes_moedas (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT NOT NULL,
    cotacao INTEGER NOT NULL,
    quantidade INTEGER NOT NULL DEFAULT 0,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""",
    ),
    Migracao(
        numero=15,
        descricao=(
            "Carteira: índice e unicidade (discord_id, cotacao) em lotes_moedas."
        ),
        comando_sql="""
CREATE INDEX IF NOT EXISTS ix_lotes_moedas_discord_id
    ON lotes_moedas (discord_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_lotes_moedas_discord_cotacao
    ON lotes_moedas (discord_id, cotacao)
""",
    ),
    Migracao(
        numero=16,
        descricao=(
            "Blacklist pós-exoneração: prazo de afastamento, valor de "
            "retorno e bloqueio de novo recrutamento enquanto ativa."
        ),
        comando_sql="""
CREATE TABLE IF NOT EXISTS blacklists (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT NOT NULL,
    id_fivem VARCHAR(20),
    punicao_id INTEGER,
    cargo_referencia VARCHAR(80) NOT NULL,
    cargo_referencia_id BIGINT,
    dias_totais INTEGER NOT NULL DEFAULT 0,
    valor_retorno INTEGER NOT NULL DEFAULT 0,
    inicia_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    termina_em TIMESTAMPTZ NOT NULL,
    ativa BOOLEAN NOT NULL DEFAULT TRUE,
    paga BOOLEAN NOT NULL DEFAULT FALSE,
    paga_em TIMESTAMPTZ,
    paga_por BIGINT,
    motivo_pagamento VARCHAR(500),
    snapshot_json TEXT,
    origem VARCHAR(30) NOT NULL DEFAULT 'SISTEMA',
    criada_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_blacklists_discord_id ON blacklists (discord_id);
CREATE INDEX IF NOT EXISTS ix_blacklists_ativa ON blacklists (ativa);
""",
    ),
    Migracao(
        numero=17,
        descricao=(
            "Punição tipada: ADVERTENCIA / EXONERACAO / REBAIXAMENTO. "
            "cargo_id passa a aceitar NULL (rebaixamento não aplica cargo 🚫)."
        ),
        comando_sql="""
ALTER TABLE punicoes ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) NOT NULL DEFAULT 'ADVERTENCIA';
CREATE INDEX IF NOT EXISTS ix_punicoes_tipo ON punicoes (tipo);
ALTER TABLE punicoes ALTER COLUMN cargo_id DROP NOT NULL;
""",
    ),
    Migracao(
        numero=18,
        descricao=(
            "Usuário: bloqueio de promoção e baseline de metas após rebaixamento."
        ),
        comando_sql="""
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS bloqueio_promocao_ate TIMESTAMPTZ;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS metas_baseline_json TEXT;
""",
    ),
    Migracao(
        numero=19,
        descricao=("Histórico detalhado de punições estruturadas (rebaixamento)."),
        comando_sql="""
CREATE TABLE IF NOT EXISTS historico_punicoes (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT NOT NULL,
    tipo VARCHAR(20) NOT NULL,
    cargo_de VARCHAR(80),
    cargo_para VARCHAR(80),
    motivo VARCHAR(500),
    executado_por BIGINT,
    dias_bloqueio INTEGER,
    bloqueio_ate TIMESTAMPTZ,
    punicao_id INTEGER,
    modo VARCHAR(20),
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_historico_punicoes_discord_id
    ON historico_punicoes (discord_id);
CREATE INDEX IF NOT EXISTS ix_historico_punicoes_tipo
    ON historico_punicoes (tipo);
""",
    ),
    Migracao(
        numero=20,
        descricao=(
            "Histórico de rebaixamento: snapshot dos cargos removidos "
            "para a revogação restaurar o membro como estava."
        ),
        comando_sql="""
ALTER TABLE historico_punicoes
    ADD COLUMN IF NOT EXISTS cargos_snapshot_json TEXT;
""",
    ),
    Migracao(
        numero=21,
        descricao=(
            "Regras de moeda do plantão editáveis pelo painel financeiro "
            "(segundos por moeda, tetos diário e semanal, limite de sábado)."
        ),
        comando_sql="""
CREATE TABLE IF NOT EXISTS regras_moeda_plantao (
    id INTEGER PRIMARY KEY DEFAULT 1,
    segundos_para_moeda INTEGER NOT NULL DEFAULT 1800,
    teto_moedas_por_dia INTEGER NOT NULL DEFAULT 14,
    teto_moedas_por_semana INTEGER NOT NULL DEFAULT 84,
    hora_limite_moeda_sabado INTEGER NOT NULL DEFAULT 12,
    atualizado_em TIMESTAMPTZ,
    atualizado_por BIGINT
);
INSERT INTO regras_moeda_plantao (
    id,
    segundos_para_moeda,
    teto_moedas_por_dia,
    teto_moedas_por_semana,
    hora_limite_moeda_sabado
)
VALUES (1, 1800, 14, 84, 12)
ON CONFLICT (id) DO NOTHING;
""",
    ),
]


COMANDO_PARA_CRIAR_A_TABELA_DE_CONTROLE = """
CREATE TABLE IF NOT EXISTS migracoes_aplicadas (
    numero INTEGER PRIMARY KEY,
    descricao TEXT NOT NULL,
    aplicada_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


COMANDO_PARA_LER_OS_NUMEROS_JA_APLICADOS = "SELECT numero FROM migracoes_aplicadas"


COMANDO_PARA_REGISTRAR_A_MIGRACAO = """
INSERT INTO migracoes_aplicadas (numero, descricao)
VALUES (:numero, :descricao)
ON CONFLICT (numero) DO NOTHING
"""
