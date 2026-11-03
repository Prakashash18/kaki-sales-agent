"""Initial Kaki schema for a clean PostgreSQL development database.

This baseline is ONLY for a new empty database. It fails if application
tables already exist: preserving/migrating existing user data requires a
separate reviewed baseline process.
"""
from alembic import op
from sqlalchemy import inspect
revision = "0001_kaki_baseline"
down_revision = None
branch_labels = None
depends_on = None
def upgrade():
    connection=op.get_bind()
    from app.db import Base
    from app import operations,company_settings,workflow_core,email_connectors  # noqa
    existing=set(inspect(connection).get_table_names())-{"alembic_version"}
    if existing:
        raise RuntimeError("Refusing to initialise non-empty database: "+", ".join(sorted(existing)))
    Base.metadata.create_all(bind=connection)
def downgrade():
    raise RuntimeError("Database downgrade disabled: requires a separately reviewed data preservation plan")
