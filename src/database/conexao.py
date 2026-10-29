# src/database/connection.py
"""
Conexão assíncrona com o PostgreSQL.

- engine: motor SQLAlchemy
- async_session: fábrica de sessões (use com `async with`)
- init_db: cria as tabelas se ainda não existirem

Pool:
  pool_pre_ping  → testa a conexão antes de usar (descarta mortas)
  pool_recycle   → renova conexões antigas antes do servidor cortá-las
  connect timeout → evita travar slash commands por tempo demais
"""

import logging
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from src.config import DATABASE_URL
from src.database.migracoes import (
    COMANDO_PARA_CRIAR_A_TABELA_DE_CONTROLE,
    COMANDO_PARA_LER_OS_NUMEROS_JA_APLICADOS,
    COMANDO_PARA_REGISTRAR_A_MIGRACAO,
    MIGRACOES,
)
from src.database.models import Base

registrador = logging.getLogger(__name__)


def _preparar_url_e_argumentos_de_conexao(url_do_banco: str) -> tuple[str, dict]:
    """
    Ajusta a URL do banco para o driver asyncpg.

    Neon, Render e outros provedores costumam entregar DATABASE_URL com
    ?sslmode=require. Esse parâmetro é do libpq/psycopg2. O asyncpg não
    aceita sslmode e estoura TypeError na subida do bot.

    Esta função:
    1. Garante o prefixo postgresql+asyncpg://
    2. Remove sslmode (e channel_binding) da query da URL
    3. Se o provedor pedia SSL, devolve connect_args com ssl=True
    """
    url_ajustada = url_do_banco.strip()

    if url_ajustada.startswith("postgres://"):
        url_ajustada = "postgresql://" + url_ajustada[len("postgres://") :]

    if url_ajustada.startswith("postgresql://"):
        url_ajustada = (
            "postgresql+asyncpg://" + url_ajustada[len("postgresql://") :]
        )

    partes = urlparse(url_ajustada)
    parametros = parse_qsl(partes.query, keep_blank_values=True)

    parametros_que_ficam = []
    precisa_de_ssl = False

    for nome, valor in parametros:
        nome_em_minusculo = nome.lower()
        if nome_em_minusculo == "sslmode":
            valor_em_minusculo = (valor or "").lower()
            if valor_em_minusculo not in ("disable", "allow", ""):
                precisa_de_ssl = True
            continue
        if nome_em_minusculo == "channel_binding":
            # Também é parâmetro do libpq; o asyncpg não conhece.
            continue
        if nome_em_minusculo == "ssl":
            if (valor or "").lower() in ("true", "1", "require"):
                precisa_de_ssl = True
            continue
        parametros_que_ficam.append((nome, valor))

    query_limpa = urlencode(parametros_que_ficam)
    url_limpa = urlunparse(
        (
            partes.scheme,
            partes.netloc,
            partes.path,
            partes.params,
            query_limpa,
            partes.fragment,
        )
    )

    argumentos_de_conexao = {
        "timeout": 30,
        "command_timeout": 60,
        "server_settings": {"application_name": "discord-bot"},
    }
    if precisa_de_ssl:
        argumentos_de_conexao["ssl"] = True

    return url_limpa, argumentos_de_conexao


URL_DO_BANCO_PRONTA, ARGUMENTOS_DE_CONEXAO = (
    _preparar_url_e_argumentos_de_conexao(DATABASE_URL)
)

# Render / Neon / proxies costumam fechar conexões ociosas.
# pre_ping + recycle evitam entregar uma conexão já morta às tasks.
engine = create_async_engine(
    URL_DO_BANCO_PRONTA,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args=ARGUMENTOS_DE_CONEXAO,
)

# expire_on_commit=False evita que os objetos "expirem" depois do commit
# e obriguem outro SELECT só para ler um atributo.
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    """
    Prepara o banco para o bot subir.

    Faz tres coisas, nesta ordem:

    1. Cria as tabelas que ainda nao existem, a partir de models.py. Nunca
       apaga nem altera tabela antiga.
    2. Aplica as mudancas de estrutura que ainda nao rodaram neste banco,
       lendo a lista de src/database/migracoes.py.
    3. Garante as colunas da prova de laudo (IF NOT EXISTS), para o caso de
       uma migracao ter sido marcada como aplicada sem ter criado a coluna.
    """
    async with engine.begin() as conexao:
        await conexao.run_sync(Base.metadata.create_all)

    await aplicar_as_migracoes_que_faltam()
    await garantir_estrutura_prova_laudo()


def _partir_comandos_sql(comando_sql: str) -> list[str]:
    """
    Separa um bloco SQL em comandos individuais.

    O asyncpg so aceita um comando por execute. Migrações antigas às vezes
    juntavam ALTER + CREATE no mesmo texto; cada pedaço roda sozinho.
    """
    partes: list[str] = []
    for pedaco in comando_sql.split(";"):
        texto = pedaco.strip()
        if texto:
            partes.append(texto)
    return partes


async def aplicar_as_migracoes_que_faltam() -> int:
    """
    Roda so as mudancas de estrutura que este banco ainda nao viu.

    O banco guarda numa tabela propria quais numeros ja foram aplicados, entao
    cada mudanca roda uma unica vez na vida do banco, mesmo que o bot reinicie
    varias vezes.

    Devolve quantas mudancas foram aplicadas agora.
    """
    async with engine.begin() as conexao:
        await conexao.execute(text(COMANDO_PARA_CRIAR_A_TABELA_DE_CONTROLE))

        resultado_da_consulta = await conexao.execute(
            text(COMANDO_PARA_LER_OS_NUMEROS_JA_APLICADOS)
        )
        numeros_ja_aplicados = set()
        for linha_do_banco in resultado_da_consulta:
            numeros_ja_aplicados.add(linha_do_banco[0])

        quantidade_aplicada_agora = 0

        for migracao in MIGRACOES:
            if migracao.numero in numeros_ja_aplicados:
                continue

            registrador.info(
                "Aplicando a migracao %s: %s",
                migracao.numero,
                migracao.descricao,
            )
            for comando in _partir_comandos_sql(migracao.comando_sql):
                await conexao.execute(text(comando))
            await conexao.execute(
                text(COMANDO_PARA_REGISTRAR_A_MIGRACAO),
                {"numero": migracao.numero, "descricao": migracao.descricao},
            )
            quantidade_aplicada_agora = quantidade_aplicada_agora + 1

    if quantidade_aplicada_agora == 0:
        registrador.info("Nenhuma migracao nova para aplicar.")
    else:
        registrador.info(
            "Migracoes aplicadas agora: %s",
            quantidade_aplicada_agora,
        )

    return quantidade_aplicada_agora


# Comandos idempotentes: rodam a cada startup. Cobre o caso em que a
# migracao 11 foi registrada sem as colunas existirem de fato.
_COMANDOS_ESTRUTURA_PROVA_LAUDO = (
    (
        "ALTER TABLE consultas_laudo "
        "ADD COLUMN IF NOT EXISTS status_prova VARCHAR(20) "
        "DEFAULT 'NAO_LIBERADA'"
    ),
    (
        "ALTER TABLE consultas_laudo "
        "ADD COLUMN IF NOT EXISTS prova_liberada_em TIMESTAMPTZ"
    ),
    (
        "ALTER TABLE consultas_laudo "
        "ADD COLUMN IF NOT EXISTS prova_iniciada_em TIMESTAMPTZ"
    ),
    (
        "ALTER TABLE consultas_laudo "
        "ADD COLUMN IF NOT EXISTS prova_finalizada_em TIMESTAMPTZ"
    ),
    (
        "ALTER TABLE consultas_laudo "
        "ADD COLUMN IF NOT EXISTS pergunta_atual INTEGER DEFAULT 0"
    ),
    (
        "ALTER TABLE consultas_laudo "
        "ADD COLUMN IF NOT EXISTS acertos_prova INTEGER"
    ),
    (
        "ALTER TABLE consultas_laudo "
        "ADD COLUMN IF NOT EXISTS parecer_sugerido VARCHAR(20)"
    ),
    """
CREATE TABLE IF NOT EXISTS respostas_prova_laudo (
    id SERIAL PRIMARY KEY,
    consulta_id INTEGER NOT NULL,
    numero_pergunta INTEGER NOT NULL,
    resposta_escolhida VARCHAR(1) NOT NULL,
    correta BOOLEAN NOT NULL DEFAULT FALSE,
    respondida_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""",
    (
        "CREATE INDEX IF NOT EXISTS "
        "ix_respostas_prova_laudo_consulta_id "
        "ON respostas_prova_laudo (consulta_id)"
    ),
)


async def garantir_estrutura_prova_laudo() -> None:
    """
    Garante colunas e tabela da prova de laudo, mesmo se a migracao falhou.

    Cada comando e independente e usa IF NOT EXISTS. Se a estrutura ja
    estiver correta, o Postgres so ignora. Se a migracao 11 ficou marcada
    como aplicada sem criar as colunas, isto recupera o banco no proximo
    restart sem precisar mexer em migracoes_aplicadas na mao.
    """
    async with engine.begin() as conexao:
        for comando in _COMANDOS_ESTRUTURA_PROVA_LAUDO:
            try:
                await conexao.execute(text(comando))
            except Exception as erro:
                registrador.exception(
                    "Falha ao garantir estrutura da prova de laudo: %s | %s",
                    comando[:80],
                    erro,
                )
                raise
    registrador.info("Estrutura da prova de laudo conferida (IF NOT EXISTS).")


async def reiniciar_pool_se_preciso():
    """
    Descarta todas as conexões do pool (útil após erro de conexão fechada).
    A próxima query abre conexões novas.
    """
    await engine.dispose()


async def tentar_reanimar_as_conexoes(*, contexto: str) -> bool:
    """
    Tenta reabrir as conexoes com o banco depois de uma falha, sem estourar.

    Existe porque o mesmo trecho estava copiado em varios lugares: sempre que
    uma consulta falhava, o codigo chamava reiniciar_pool_se_preciso() dentro
    de um try/except que engolia o erro em silencio.

    Aqui a tentativa e feita uma vez e o resultado sempre vira log. Se a
    reanimacao tambem falha, isso e grave e aparece no log de erro, porque
    significa que o banco esta fora e nao so a conexao estava velha.

    Parametros:
    - contexto: onde a falha original aconteceu, em palavras
      (ex.: "listar o historico de horas")

    Retorno:
    - True  quando as conexoes foram descartadas e a proxima consulta pode
      tentar de novo
    - False quando nem isso foi possivel
    """
    try:
        await reiniciar_pool_se_preciso()
    except Exception as erro_ao_reanimar:
        logging.error(
            "O banco falhou ao %s e reiniciar as conexoes tambem falhou: %s",
            contexto,
            erro_ao_reanimar,
            exc_info=erro_ao_reanimar,
        )
        return False

    logging.warning(
        "O banco falhou ao %s. As conexoes foram descartadas e a proxima "
        "consulta vai abrir conexoes novas.",
        contexto,
    )
    return True
