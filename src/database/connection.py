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
    Cria no banco todas as tabelas definidas em models.py.

    Só cria o que ainda não existe — não apaga nem altera colunas antigas.
    Colunas novas em tabelas já existentes entram via ALTER TABLE IF NOT EXISTS.
    """
    from sqlalchemy import text

    async with engine.begin() as conexao:
        await conexao.run_sync(Base.metadata.create_all)

        # Migrações leves: colunas novas do domínio tickets
        await conexao.execute(
            text(
                "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS mensagem_botoes_id BIGINT"
            )
        )
        await conexao.execute(
            text("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS call_canal_id BIGINT")
        )
        await conexao.execute(
            text(
                "ALTER TABLE tickets "
                "ADD COLUMN IF NOT EXISTS saudado BOOLEAN DEFAULT FALSE"
            )
        )


async def reiniciar_pool_se_preciso():
    """
    Descarta todas as conexões do pool (útil após erro de conexão fechada).
    A próxima query abre conexões novas.
    """
    await engine.dispose()
