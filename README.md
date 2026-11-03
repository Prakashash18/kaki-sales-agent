# Kaki Sales Agent MVP

Outlook email triage and human-approved response dashboard for Sea Hover, built with FastAPI, SQLAlchemy, Anthropic and Microsoft Graph.

## Features
- Login-protected dashboard and audit logging
- Safe classification of RFQ, purchase orders, enquiries and follow-ups
- Demo emails; optional real Outlook inbox sync via Microsoft Graph application credentials
- Human approval before outbound emails; email sending disabled by default

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Export the .env variables before starting
uvicorn app.main:app --reload
pytest -q
```

## Render
Use this repository to create a Render Python web service; build: `pip install -r requirements.txt`; start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Set `ADMIN_PASSWORD`, `SESSION_SECRET`, and `APP_ENV=production` in Render. `DISABLE_SEND=true` must remain set while testing. Default SQLite on Render Free is ephemeral and must NOT be used for real customer data; add a PostgreSQL `DATABASE_URL` before live mailbox sync. Do not commit secrets.

## Microsoft Graph
An Entra administrator must grant app-only permissions for Mail.Read and optionally Mail.ReadWrite and Mail.Send, restricted to the target mailbox with appropriate Exchange Online application scoping. Configure `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_MAILBOX`. Do not enable sending until the scope and approval process have been verified. Current MVP uses manual sync, not webhooks, and flags attachments for manual review (it does not parse PO PDFs).
