from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (importing registers every table on Base.metadata)
from app.db import Base, engine

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

# The database address comes from app settings (DESK_DATABASE_URL), not from alembic.ini.
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
