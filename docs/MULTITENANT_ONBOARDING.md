# Multi-company onboarding — Kaki and Microsoft 365

## What is configured once in Render?
The *Kaki service* needs exactly one PostgreSQL DATABASE_URL and one shared Microsoft Entra application registration:
- DATABASE_URL: the shared PostgreSQL connection string. Store privately in the Kaki Render service.
- MICROSOFT_OAUTH_CLIENT_ID: Kaki's ONE multitenant Entra application ID.
- MICROSOFT_OAUTH_CLIENT_SECRET: Kaki's ONE app secret (use a secret manager or certificate in production).
- KAKI_TOKEN_ENCRYPTION_KEY: one encryption key for application token encryption, with a documented rotation plan.
- KAKI_PUBLIC_URL: Kaki's HTTPS base URL.
- SESSION_SECRET: Kaki's private session signing secret.
- KAKI_ENABLE_OAUTH=true to enable the OAuth flow only after database setup.

There is **no new Render environment variable, client secret or PostgreSQL database required for each company**.

## What happens when Company A / Company B connects?
1. A Kaki user signs up, creates a company and is assigned the owner role.
2. The company owner selects **Email connections → Connect Outlook**.
3. Kaki redirects the browser to Microsoft's organisational multitenant /organizations endpoint with state, nonce and PKCE.
4. The user signs in to the **company's own Microsoft 365 account** and consents, or requests administrator approval according to tenant policy.
5. Kaki redeems the authorization code and verifies the Microsoft ID token (signature with tenant-specific keys, exact issuer, audience, expiry, tenant ID, Microsoft object ID and nonce).
6. Kaki binds that company to the verified Microsoft tenant ID in PostgreSQL. Only the Kaki company owner may establish its first Microsoft tenant binding. Additional connections must be from that same tenant.
7. Kaki encrypts and stores provider tokens separately under each company's connection record.
8. Graph operations use the token associated with the currently authorised Kaki company.

Company B goes through the same steps. No developer intervention or new Render settings are required.

## One-time Microsoft Entra configuration

In https://entra.microsoft.com:
- App registrations → Kaki Sales Agent → Authentication / Supported account types
- Select **Accounts in any organisational directory (Any Microsoft Entra ID tenant — Multitenant)**.
- Register the exact Web redirect URI:
  https://kaki-secure-staging.onrender.com/integrations/callback/microsoft
- Under API permissions → Microsoft Graph → **Delegated**: User.Read and Mail.Read.
- The OAuth request also includes openid and offline_access.
- Create a client secret for testing; save its **Value** privately as MICROSOFT_OAUTH_CLIENT_SECRET.
- MICROSOFT_TENANT_ID is no longer required by the multitenant connector: the company tenant is discovered from the verified Microsoft ID token and stored in PostgreSQL.
- Set KAKI_ENABLE_MAIL_SYNC=false and DISABLE_SEND=true until explicit read-only diagnostics succeed.

## PostgreSQL data organisation

ONE PostgreSQL instance contains company profiles, memberships, workflow definitions, mailbox connections, Microsoft tenant bindings and company-specific messages. Every company's records are related by company_id. All backend queries and controls must verify membership and company scope.

Example (illustrative):
| company_id | company name | microsoft_tenant_id |
| --- | --- | --- |
| 101 | Sea Hover | verified tenant A |
| 102 | Example Engineering | verified tenant B |

These are example rows, not actual customer records.

## Current limitations / release blockers

- The multitenant registration must be selected in the actual Entra portal by Kaki's app owner. Code cannot alter an Entra app registration by itself.
- The OAuth and Graph flows have automated mocked tests; live Microsoft sign-in and tenant consent must still be verified.
- A Kaki owner initially binding a Microsoft tenant is not independently proof that they legally represent the company. For commercial launch, require domain verification or administrator confirmation / approval.
- This release reads the signed-in user's own mailbox with delegated Mail.Read. Shared mailbox and app-only background access require separate scopes and explicit security review.
- The Render staging web service must have a valid DATABASE_URL, working migrations and secure SESSION_SECRET before onboarding can run.
- The free PostgreSQL database is temporary and not appropriate for persistent commercial data. Backups and incident response must be established.
- Only the secure staging entrypoint is company-scoped. Legacy routes on main remain unsafe for unrelated companies and are not part of the multitenant release.
- Email sending is disabled.
