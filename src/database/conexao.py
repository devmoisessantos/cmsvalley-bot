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

# Render / Neon / proxies costumam fechar conexões ociosas.
# pre_ping + recycle evitam entregar uma conexão já morta às tasks.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={
        "timeout": 30,
        "command_timeout": 60,
        "server_settings": {"application_name": "discord-bot"},
    },
)

# expire_on_commit=False evita que os objetos "expirem" depois do commit
# e obriguem outro SELECT só para ler um atributo.
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    """
    Prepara o banco para o bot subir.

    Faz duas coisas, nesta ordem:

    1. Cria as tabelas que ainda nao existem, a partir de models.py. Nunca
       apaga nem altera tabela antiga.
    2. Aplica as mudancas de estrutura que ainda nao rodaram neste banco,
       lendo a lista de src/database/migracoes.py.
    """
    async with engine.begin() as conexao:
        await conexao.run_sync(Base.metadata.create_all)

    await aplicar_as_migracoes_que_faltam()


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
            await conexao.execute(text(migracao.comando_sql))
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
