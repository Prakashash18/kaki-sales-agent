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
    legacy = {'emails','audit','customers','products','quotes','quote_lines','purchase_orders',
              'company_profiles','document_profiles','workflows','workflow_versions',
              'workflow_runs','workflow_events','mail_connections'}
    tables = [table for table in Base.metadata.sorted_tables if table.name in legacy]
    Base.metadata.create_all(bind=connection, tables=tables)
def downgrade():
    raise RuntimeError("Database downgrade disabled: requires a separately reviewed data preservation plan")
