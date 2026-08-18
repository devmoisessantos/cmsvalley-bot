"""Prepara variáveis de ambiente falsas antes da importação dos módulos do bot."""

import os

os.environ.setdefault("DISCORD_TOKEN", "token_falso_para_validacao")
os.environ.setdefault("GUILD_ID", "1035704096608493608")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
