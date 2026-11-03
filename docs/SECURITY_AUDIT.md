# Kaki security review — 2026-11-03

Scope: Workflow Studio feature branch, single-administrator prototype, Microsoft / Gmail OAuth scaffolding.

## Tests performed
- Automated GitHub Actions security and workflow regression tests on SQLite and PostgreSQL 16.
- PostgreSQL Alembic baseline migration successfully applied to a fresh CI PostgreSQL 16 database.
- 17 automated tests passed in PostgreSQL CI (and SQLite CI).
- Negative tests verify arbitrary workflow node types are rejected; unapproved send paths fail validation; unauthenticated workflow and connector pages are denied; legacy sending is blocked even when DISABLE_SEND=false; and single-company OAuth is disabled by default.

## Critical / high findings still open
1. **Critical: tenant isolation is not implemented.** Current user is a global administrator and company switching is global. Emails, products, customers, quotes, and audit records are not consistently scoped to company_id. Do not onboard unrelated companies or real users. Add identity provider, user-company memberships, scoped queries, row-level access controls and cross-tenant regression tests.
2. **High: authentication is demo-only.** Single password, no per-user identities, MFA, rate limits, invitation lifecycle, user revocation or role-based approvals. Replace before production.
3. **High: credential and mailbox permissions.** No provider verification/tenant consent review. OAuth is disabled by default. Keep KAKI_ALLOW_SINGLE_COMPANY_OAUTH=false and KAKI_ENABLE_MAIL_SYNC=false.
4. **High: no hardened durable job execution.** Timed waits require managed workers, leases, retries, deduplication, crash recovery and locked idempotent state transitions.
5. **High: data protection incomplete.** No documented retention/deletion protocol, backup/restore tests, encrypted business-document storage or comprehensive attachment scanner. Do not import real customer content.
6. **Medium: file parsing protections.** Uploaded PDF/DOCX/XLSX parses in process. Restrict resource usage and use isolation / malware scanning before broad access.
7. **Medium: content security policy / session hardening and operational logs** need further review and browser testing.

## PostgreSQL implementation status
- PostgreSQL-compatible models already present; migration tooling added via Alembic.
- Initial revision 0001_kaki_baseline refuses to overwrite a non-empty database.
- The schema is created in CI PostgreSQL 16 using python -m alembic upgrade head.
- A **new Render Free PostgreSQL development database** named kaki-workflow-dev-db was created in Singapore on November 3, 2026; expiry December 3, 2026. The live Kaki web service is NOT connected to it.
- Actual Render database credentials must be configured in the service's private environment, and migrations must be run in a controlled job prior to deployment. Keep this database for synthetic test data only.
- Render Free PostgreSQL has no backups, expires after 30 days and is unsuitable for live transactional records.

## Release gate
Do not merge/deploy with live mail access until: user/tenant isolation, PostgreSQL migrations and backup plan, role permissions, OAuth review, secret management, end-to-end security tests, and durable background worker are completed.
