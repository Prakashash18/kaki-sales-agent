from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from app.db import Base
# Import every model so metadata includes the entire Kaki schema.
from app import operations, company_settings, workflow_core, email_connectors  # noqa: F401

config=context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata=Base.metadata
def url():
    import os
    raw=os.environ.get("DATABASE_URL")
    if not raw: raise RuntimeError("DATABASE_URL is required for migrations")
    if raw.startswith("postgres://"):raw="postgresql+psycopg://"+raw[len("postgres://"):]
    if raw.startswith("postgresql://"):raw="postgresql+psycopg://"+raw[len("postgresql://"):]
    return raw
def offline():
    context.configure(url=url(),target_metadata=target_metadata,literal_binds=True,dialect_opts={"paramstyle":"named"})
    with context.begin_transaction():context.run_migrations()
def online():
    from sqlalchemy import create_engine
    engine=create_engine(url(),poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()
    engine.dispose()
if context.is_offline_mode():offline()
else:online()
