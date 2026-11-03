# Kaki staging: Microsoft 365 Business Basic onboarding

Scope: read-only delegated Microsoft Graph integration for a *single user's own Exchange Online mailbox*. Shared mailbox integration will need Mail.Read.Shared and /users/{mailbox} endpoint and is NOT available in this revision.

## 1. Render staging

Open https://dashboard.render.com/web/srv-dbkvf37f3r2c7381kge0 and select Environment.
Set DATABASE_URL to the Render PostgreSQL Internal Database URL from https://dashboard.render.com/d/dpg-dbkv4f0u01pc73ed9fig-a.
Set SESSION_SECRET to a unique cryptographically random string (40+ characters).
Set KAKI_BOOTSTRAP_TOKEN to a separate unique secret.
Set KAKI_TOKEN_ENCRYPTION_KEY to a fresh Fernet key. Generate with `python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())'`.
Keep APP_ENV=staging, KAKI_AUTO_CREATE_SCHEMA=false, DISABLE_SEND=true, and KAKI_ENABLE_MAIL_SYNC=false.
Do NOT paste these secret values in GitHub or a chat.

## 2. Microsoft Entra registration

Sign in to https://entra.microsoft.com as the administrator of the Microsoft 365 Business Basic tenant.
Go to Entra ID > App registrations > New registration.
Name: Kaki Sales Agent - Test.
Supported account types: Accounts in this organisational directory only (Single tenant).
Redirect platform: Web.
Exact redirect URI: https://kaki-secure-staging.onrender.com/integrations/callback/microsoft
Under API permissions > Microsoft Graph > Delegated permissions add User.Read and Mail.Read.
offline_access is requested by the OAuth authorization flow to allow refresh tokens.
If the tenant restricts user consent, an admin must grant consent.
Under Certificates & secrets create a client secret and copy its **Value**, not its ID.
From Overview note the Application (client) ID and Directory (tenant) ID.

## 3. Add Entra IDs to Render

MICROSOFT_TENANT_ID = Directory (tenant) ID
MICROSOFT_OAUTH_CLIENT_ID = Application (client) ID
MICROSOFT_OAUTH_CLIENT_SECRET = Client secret Value
KAKI_ENABLE_OAUTH = true
KAKI_PUBLIC_URL = https://kaki-secure-staging.onrender.com (already configured)
Save and deploy. The staging start command applies Alembic migrations to a **new empty database**.

## 4. First Kaki account

After Render reports Live, open https://kaki-secure-staging.onrender.com/setup.
Enter the bootstrap token, a test administrator email, a strong password and your test company name.
After setup, remove KAKI_BOOTSTRAP_TOKEN from Render environment to disable the bootstrap route.

## 5. Test Graph

Log in and open https://kaki-secure-staging.onrender.com/integrations.
Select Connect Outlook. Sign in to the Business Basic mailbox user and approve delegated access.
Verify that an Outlook connection appears. Click Test Microsoft Graph.
The diagnostic must return JSON with status=ok, mailbox_readable=true, sending_enabled=false.
No email is sent, and the diagnostic reads only minimal message metadata.
After this passes, for an explicitly authorised test mailbox, set KAKI_ENABLE_MAIL_SYNC=true and click Read latest inbox emails.
Use fabricated or approved test messages. Imported messages are isolated by company ID in the staging schema.

## Troubleshooting

- AADSTS50011: redirect URI mismatch. Web platform URI must exactly match the staging callback path.
- AADSTS7000215: wrong or expired client secret. Use client secret Value, not ID.
- AADSTS65001 / Need admin approval: tenant blocks user consent. Ask a tenant administrator to consent.
- Graph 403: Mail.Read consent missing or mailbox access denied.
- Graph 404: Exchange Online mailbox unavailable or incorrect mailbox.
- Kaki 503: missing DATABASE_URL, encryption key, or KAKI_ENABLE_OAUTH.
- Kaki login redirect after Microsoft consent: make sure SESSION_SECRET is stable and session cookie is SameSite=Lax.

Note: the current secure staging branch is a controlled pilot, not a production security certification. MFA, backups, incident response, durable background sync, and shared-mailbox support are further work.

## 6. Verified staging status and current blocker (November 3, 2026)

The secure staging application is deployed from the feature/secure-staging-pilot GitHub branch at https://kaki-secure-staging.onrender.com. Its current Render deployment is failing **before startup**, with RuntimeError: DATABASE_URL is required for migrations.

Resolve it privately in the Render dashboard:

1. Open the development PostgreSQL instance at https://dashboard.render.com/d/dpg-dbkv4f0u01pc73ed9fig-a, choose **Connect**, and copy the **Internal Database URL**.
2. Open the secure staging service at https://dashboard.render.com/web/srv-dbkvf37f3r2c7381kge0 > **Environment**.
3. Add DATABASE_URL with the copied full internal URL, then save and deploy. Do not paste this credential into GitHub, ChatGPT, or screenshots.
4. Add SESSION_SECRET (40+ random characters), KAKI_BOOTSTRAP_TOKEN (different random secret), and KAKI_TOKEN_ENCRYPTION_KEY (Fernet key). The startup command runs Alembic migrations on the new empty database. After the service reports Live, use /setup to create the owner account.
5. For Graph onboarding only, add MICROSOFT_TENANT_ID, MICROSOFT_OAUTH_CLIENT_ID, MICROSOFT_OAUTH_CLIENT_SECRET and KAKI_ENABLE_OAUTH=true. Leave KAKI_ENABLE_MAIL_SYNC=false and DISABLE_SEND=true until the read-only Graph diagnostic succeeds.

The code now uses PKCE (S256) in the Microsoft OAuth authorization code flow. GitHub CI passed 24 automated tests on SQLite and on PostgreSQL 16, but live Microsoft consent and real mailbox access have not yet been verified.

**Shared mailbox limitation:** The current onboarding connects the signed-in user's own mailbox. Reading a delegated shared mailbox needs a separately reviewed Mail.Read.Shared scope and /users/{mailbox} endpoint; automated change subscriptions for shared mailboxes require application-level permissions. Test your own mailbox first.
