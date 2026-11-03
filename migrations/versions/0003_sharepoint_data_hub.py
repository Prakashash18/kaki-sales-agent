"""Read-only SharePoint Data Hub source and snapshot records."""
from alembic import op
from sqlalchemy import inspect
revision="0003_sharepoint_data_hub"
down_revision="0002_secure_tenant_auth"
branch_labels=None
depends_on=None
def upgrade():
    from app.db import Base
    from app import data_hub  # noqa
    con=op.get_bind()
    existing=set(inspect(con).get_table_names())
    for name in ("data_hub_sources","data_hub_records"):
        if name in existing:
            raise RuntimeError("Table already exists: "+name)
    tables=[t for t in Base.metadata.sorted_tables if t.name in ("data_hub_sources","data_hub_records")]
    Base.metadata.create_all(bind=con,tables=tables)
def downgrade():
    raise RuntimeError("Data Hub deletion requires a reviewed backup and migration")
