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

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from src.config import DATABASE_URL
from src.database.models import Base

# Render / Neon / proxies costumam fechar conexões ociosas.
# pre_ping + recycle evitam entregar uma conexão já morta às tasks.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=5,
    max_overflow=10,
    pool_timeout=15,
    connect_args={
        # asyncpg: timeout de estabelecimento da conexão (segundos)
        "timeout": 10,
        # timeout por comando SQL (evita hang infinito em /historico etc.)
        "command_timeout": 20,
    },
)

# expire_on_commit=False evita que os objetos "expirem" depois do commit
# e obriguem outro SELECT só para ler um atributo.
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    """
    Cria no banco todas as tabelas definidas em models.py.

    Só cria o que ainda não existe — não apaga nem altera colunas antigas.
    """
    async with engine.begin() as conexao:
        await conexao.run_sync(Base.metadata.create_all)


async def reiniciar_pool_se_preciso():
    """
    Descarta todas as conexões do pool (útil após erro de conexão fechada).
    A próxima query abre conexões novas.
    """
    await engine.dispose()
