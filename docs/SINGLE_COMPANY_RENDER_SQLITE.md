# Kaki: One company per Render deployment

## Configuration
Use a paid Render web service with a persistent disk mounted at `/var/data`.
Set:
- `APP_ENV=production`
- `KAKI_SINGLE_COMPANY=true`
- `DATABASE_URL=sqlite:////var/data/kaki.db`
- `KAKI_AUTO_CREATE_SCHEMA=false`
- `DISABLE_SEND=true`
- `KAKI_ENABLE_MAIL_SYNC=false`
- `KAKI_ENABLE_OAUTH=false` until Microsoft Graph consent is configured
- `SESSION_SECRET` at least 40 cryptographically random characters
- `KAKI_BOOTSTRAP_TOKEN` a separate random one-time bootstrap token
- `KAKI_TOKEN_ENCRYPTION_KEY` a Fernet encryption key for connector credentials
- `KAKI_PUBLIC_URL` the HTTPS URL of this company's Kaki instance
- `KAKI_ALLOWED_HOSTS` its own Render domain
- Microsoft Entra IDs/client credentials for this company's deployment (or share a Kaki app registration if you manage one across deployments)

Use start command `python -m alembic upgrade head && uvicorn app.secure_main:app --host 0.0.0.0 --port $PORT`.
Migrations must run **after** the disk mounts, not during the build or predeploy step. Keep one web worker/instance (Render persistent disks cannot be mounted by multiple instances). SQLite uses WAL and a busy timeout; database migrations must be tested against a copied database before upgrades.

## SharePoint master data
Inventory, price lists, customer lists, jobs and company files remain in SharePoint Excel.
The initial source integration is read-only; Kaki's Graph permissions for SharePoint are to be reviewed and added explicitly. This branch does not yet implement SharePoint workbook discovery or live import.
Use Graph for data retrieval and allow user-approved write-back only after the relevant Excel workbook structures and concurrency handling have been tested.

## Security and data durability
- Owner completes one-time /setup. Subsequent company creation and company switching are blocked.
- Employees are invited with individual company accounts and access roles.
- Database tables remain company-scoped; the single-company guard rejects deployments with multiple company profiles.
- SQLite stores logins, approval state, encrypted provider tokens, executions, and audit events.
- **Render disk snapshots are not a substitute for database-aware backups.** Back up SQLite using its online backup API into external encrypted storage, include restore verification, and test disaster recovery.
- A persistent disk disables zero-downtime deploys and is limited to a single service instance.
- A paid web service and persistent disk both incur charges; confirm pricing before upgrading or attaching the disk.
- Do not upload or import live confidential business records until Microsoft Graph consent and access tests pass.

## Existing data
Do not point the new SQLite database at a PostgreSQL database. This is an independent deployment. Preserve the old PostgreSQL database until you explicitly determine whether to export or decommission it.
