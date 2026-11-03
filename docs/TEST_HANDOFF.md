# Kaki single-company acceptance and test handoff

## Target deployment
One Render paid web service for one company, with multiple staff users. Kaki's own SQLite database resides at /var/data/kaki.db on a **Render persistent disk mounted at /var/data**. SharePoint Excel files remain authoritative for inventory, pricing, customers and job tracking. No PostgreSQL service is required.

**Important:** this repository branch is a development candidate. Installing a paid plan and attaching the persistent disk must be completed in the Render dashboard; this repo does not create or charge for one. The existing live Kaki instance is unchanged.

## Prerequisites before first boot
- Choose paid Render web service plan and attach a persistent disk at /var/data (start small, monitor disk use).
- Set start command: python -m alembic upgrade head && uvicorn app.secure_main:app --host 0.0.0.0 --port $PORT
- Keep exactly one instance and one worker. Persistent disk is only accessible at runtime, so migrations must run in the start command after mount.
- Set APP_ENV=production, KAKI_SINGLE_COMPANY=true, DATABASE_URL=sqlite:////var/data/kaki.db, KAKI_AUTO_CREATE_SCHEMA=false, KAKI_ALLOWED_HOSTS=your-real-domain, KAKI_PUBLIC_URL=https://your-real-domain.
- Set SESSION_SECRET (40+ random chars), KAKI_BOOTSTRAP_TOKEN (separate random secret), KAKI_TOKEN_ENCRYPTION_KEY (Fernet key) in Render secrets.
- Set DISABLE_SEND=true, KAKI_ENABLE_LEGACY_GRAPH_SYNC=false, KAKI_ENABLE_MAIL_SYNC=false, KAKI_ENABLE_OAUTH=false.
- Set KAKI_ENABLE_DUE_WORKER=true for persistent wait-step resumption (single app worker); monitor logs.
- Set KAKI_ENABLE_BACKUP_EXPORT=true to allow owner-only online SQLite backup download.

**Do not share secrets or database URLs in GitHub, chat, emails or screenshots.**

## Employee onboarding (no Microsoft connection needed)
1. Open /setup and enter the bootstrap token, company name, owner email, and strong password.
2. Remove KAKI_BOOTSTRAP_TOKEN from Render after owner setup.
3. Sign in; open /settings and create an invitation link for each employee. Share each link privately. Use roles owner/admin/manager/staff/viewer.
4. Open /ops and create a synthetic customer, SKU and price. Create a quotation, add lines, export its PDF, approve with a manager role, register a matching PO and approve it. Staff must be denied manager-only approval and product-management actions.
5. Open /flows, create a test RFQ workflow, adjust nodes or chat-generated proposed definitions, simulate, save a new version, publish, start and approve/reject.
6. Verify history and audit trace in /flows/runs/{id}.
7. Export an owner backup from /settings. Restore and verify in an isolated test copy (not the live database).
8. Test restart/redeployment persistence after the paid persistent disk is attached. Workflow runs, users and approvals should survive.

## Microsoft 365 onboarding — when ready
1. Register a Web application in Microsoft Entra. For one-company-per-deployment, single-tenant registration is sufficient. Kaki's redirect URI must equal https://YOUR_DOMAIN/integrations/callback/microsoft.
2. Add delegated Microsoft Graph User.Read, Mail.Read, and **Sites.Read.All** for browsing SharePoint sites and files, plus offline_access. Administrator consent may be necessary. Sites.Read.All is a broad read scope; tighten to selected sites for production when the design supports it.
3. Set MICROSOFT_TENANT_ID, MICROSOFT_OAUTH_CLIENT_ID and MICROSOFT_OAUTH_CLIENT_SECRET privately. Turn KAKI_ENABLE_OAUTH=true only after consent setup.
4. Sign in to Kaki as owner/admin, open /integrations, click Connect Outlook, complete Microsoft OAuth, then click Test Microsoft Graph (read-only).
5. Open /data-hub, search a SharePoint site, browse a document library, select an XLSX workbook, choose a business category and sheet, review and approve suggested column mapping, then import.
6. Verify inventory, price list, customers and job rows in Data Hub search and Ask Kaki (e.g. "Which jobs are completed?"); change the SharePoint Excel file and click Sync changes to verify update. Files are read-only from Kaki.
7. Keep KAKI_ENABLE_MAIL_SYNC=false until test data access and storage permissions are reviewed. Keep outbound sending disabled.

## Smoke and negative tests
- Before Microsoft connection, /flows, /ops, /settings and /data-hub must load with owner credentials; /system/readiness reports which integration steps remain unconfigured.
- Wrong password / repeated login → rejected/throttled.
- Staff cannot invite or publish; manager approval works.
- Company creation and company switching → HTTP 403 in single-company mode.
- No SharePoint connection → Data Hub loads and shows connection instructions.
- Unauthorized Data Hub import and browse → HTTP 403.
- Arbitrary workflow node and unapproved send → rejected.
- Database integrity check, online backup and restore → verified.
- Microsoft OAuth and Graph requests → mocked in CI; real Microsoft consent remains unverified.
- Failure recovery: simulate interruption during wait, restart service, and confirm due worker resumes when enabled.
- No automatic sending to customers.

## Remaining external and production release gates
- Render plan/disk are not yet changed. Test-ready branch alone cannot provide durable live storage.
- Live Microsoft consent and SharePoint data access need customer authorisation; cannot be tested without an Entra app registration and mailbox.
- Offsite **automated** backup, tested restore/rotation, monitoring/alerts, MFA and independent security testing should precede business-critical use.
- Automatic SharePoint sync scheduler and write-back are not enabled: the initial pilot uses explicit administrator sync.
- This release is a single-company pilot, not a certified production system.
