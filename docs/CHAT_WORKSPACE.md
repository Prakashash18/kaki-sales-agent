# Chat-first Kaki workspace

The secure pilot opens `/chat` after login and from `/`. Chat is the common
entry point for the existing operations. Task forms and records open beside
the conversation on desktop, or in a task view with a **Chat** return button
on mobile. The original routes remain available as direct links.

## Supported tasks

- Create and review quotations, add lines, approve and download PDFs.
- Register and review purchase orders, inspect discrepancies and approve.
- Manage customer and product records.
- Create, edit, simulate, publish and run workflows; review execution and approvals.
- Invite staff, review roles, revoke access and manage document mappings.
- Browse connections and inbox, import/sync SharePoint data and export backups.
- Ask questions of previously imported, company-scoped SharePoint snapshots.

Examples: `Create a quotation`, `Register a purchase order`, `Add a product`,
`Show workflows and approvals`, `Invite a teammate`, `Show quotation 3`,
`Review workflow run 1`, `Which jobs are completed?`.

The pilot uses guided intent matching and the existing snapshot search. It
does **not** provide an open-ended LLM conversation or infer arbitrary field
values from prose. Users complete structured details in the task panel.
No AI API key is needed. Conversations are held in the current browser page
and clear on refresh or **New conversation**; no chat transcript is stored.

## Execution and security

`POST /chat/message` checks authentication, company membership and CSRF. It
returns an allowlisted task descriptor or a read-only snapshot answer. It
never executes writes or approvals. Restricted task shortcuts also check
the user's required role. The original controllers recheck permissions,
CSRF and validation when a user explicitly submits a task form.

Task pages are embedded only for same-origin browser iframe requests. The
response uses `X-Frame-Options: SAMEORIGIN` and `frame-ancestors 'self'` in
that case; ordinary and cross-site requests retain framing denial. Parent
task URLs are restricted to known local route prefixes. Chat output and
source records use text nodes, not generated HTML. No model output is
executed or used to select arbitrary URLs.

Provider sign-in remains an explicit task form and uses a top-level
navigation when configured. Microsoft credentials and production deployment
settings were not changed. Sending stays disabled in the secure pilot.
Database restore is intentionally not available through chat.

## Validation

Automated checks cover task dispatch, no writes from chat messages,
authentication, CSRF, action allowlisting, message limits, role denial,
company-scoped search, sending refusal and same-origin frame policy.
Browser smoke tests cover quotation creation and approval, workflow
execution and approval inside chat, source search, mobile task return,
inline forms and responsive layout.
