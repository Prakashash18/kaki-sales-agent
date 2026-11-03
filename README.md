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


## Company onboarding

Open **Company settings** after logging in. Create a company profile, select a document category, upload a representative PDF, DOCX, XLSX, CSV, or TXT file, and review proposed field labels. The uploaded document and extracted customer data are *not stored*: only detected label names and administrator-approved mapping aliases are saved. Scanned PDFs need OCR, not yet available.

The company profile affects the response style in Claude draft generation, but no mapping automatically commits a quotation or purchase order. The current application uses a single administrator and does not enforce cross-company row isolation. Do not place multiple unrelated real companies on a shared instance. The Render free filesystem is ephemeral; configure persistent PostgreSQL and tenant-aware authentication before storing live business data. Sample documents should be redacted until production data handling is approved.
