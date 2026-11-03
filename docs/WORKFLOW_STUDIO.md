# Kaki Workflow Studio (development branch)

This feature branch adds a company-scoped workflow builder and read-only OAuth connector scaffolding to Kaki. It is not a production-ready multi-tenant release.

## Workflow designer
- Create from RFQ / basic templates; see the workflow as node cards; edit nodes and transitions; reorder; import/export JSON; duplicate; archive and restore.
- Request an AI-generated proposed change from Claude. Proposals are never saved automatically.
- Save immutable revisions, inspect history, simulate without external side effects and publish approved versions.
- Manually execute published workflows, view audit traces, approve, reject and cancel work.
- Wait nodes persist due timestamps but need a dedicated worker or cron service to resume on time.
- Outbound mail delivery from the workflow executor is intentionally blocked.

## Email providers
- Microsoft OAuth: delegated offline_access, User.Read and Mail.Read, without shared-mailbox permissions.
- Google OAuth: Google Workspace/Gmail read-only, subject to Google restricted-scope verification before broad public use.
- Set KAKI_PUBLIC_URL, KAKI_TOKEN_ENCRYPTION_KEY, a PostgreSQL DATABASE_URL, and the provider-specific MICROSOFT_OAUTH_CLIENT_ID / MICROSOFT_OAUTH_CLIENT_SECRET or GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET.
- Microsoft redirect: https://your-domain/integrations/callback/microsoft
- Google redirect: https://your-domain/integrations/callback/google
- Mailbox import requires explicit KAKI_ENABLE_MAIL_SYNC=true; keep disabled until tenancy and security tests pass.
- The existing Microsoft Graph app-only proof of concept is distinct from this OAuth connector.
- No forwarding mailbox is required.

## Production blockers
1. Durable PostgreSQL with migration tooling and backups; Render Free SQLite is ephemeral.
2. Verified individual users and company memberships. Every email, document, customer, run and connection must be tenant-scoped. The current single admin session is insufficient.
3. Reliable worker queue with idempotency, leases, retries, waiting and provider push/delta/history correlation.
4. OAuth verification, mailbox permission scoping, secret rotation, attachment safety and data retention policies.
5. Human approval gates and representative redacted test documents before enabling commercial actions.

## Tests
Use Python 3.11. Install requirements plus beautifulsoup4 and run python -m pytest -q.
