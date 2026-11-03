"""Add per-account company memberships, invitations and isolated mail records."""
from alembic import op
from sqlalchemy import inspect
revision = "0002_secure_tenant_auth"
down_revision = "0001_kaki_baseline"
branch_labels = None
depends_on = None

def upgrade():
    from app.db import Base
    from app import identity, secure_email_api  # noqa: F401
    con = op.get_bind()
    new_tables = [
        "kaki_accounts",
        "kaki_memberships",
        "kaki_invitations",
        "kaki_login_failures",
        "tenant_messages",
    ]
    existing = set(inspect(con).get_table_names())
    for name in new_tables:
        if name in existing:
            raise RuntimeError("Refusing to recreate table: " + name)
    sorted_tables = [tbl for tbl in Base.metadata.sorted_tables if tbl.name in new_tables]
    Base.metadata.create_all(bind=con, tables=sorted_tables)

def downgrade():
    raise RuntimeError("Reverting identity schema requires a reviewed data-preservation procedure")
