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
