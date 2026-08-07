# src/database/connection.py
"""
Conexão assíncrona com o PostgreSQL.

- engine: motor SQLAlchemy
- async_session: fábrica de sessões (use com `async with`)
- init_db: cria as tabelas se ainda não existirem
"""

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from src.config import DATABASE_URL
from src.database.models import Base

engine = create_async_engine(DATABASE_URL, echo=False)

# expire_on_commit=False evita que os objetos “expirem” depois do commit
# e obriguem outro SELECT só para ler um atributo.
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    """
    Cria no banco todas as tabelas definidas em models.py.

    Só cria o que ainda não existe — não apaga nem altera colunas antigas.
    """
    async with engine.begin() as conexao:
        await conexao.run_sync(Base.metadata.create_all)
