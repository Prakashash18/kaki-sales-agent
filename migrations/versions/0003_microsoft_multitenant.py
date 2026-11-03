"""Bind each Kaki company to a verified Microsoft Entra tenant.

Revision ID: 0003_microsoft_multitenant
Revises: 0002_secure_tenant_auth
"""
from alembic import op
from sqlalchemy import inspect

revision = "0003_microsoft_multitenant"
down_revision = "0002_secure_tenant_auth"
branch_labels = None
depends_on = None

def upgrade():
    from app.db import Base
    from app import microsoft_multitenant  # noqa: F401
    connection=op.get_bind()
    table=Base.metadata.tables["microsoft_tenant_bindings"]
    if "microsoft_tenant_bindings" in inspect(connection).get_table_names():
        raise RuntimeError("Microsoft tenant bindings already exist; review migration state")
    table.create(bind=connection)

def downgrade():
    raise RuntimeError("Tenant-binding downgrade requires a reviewed migration")
