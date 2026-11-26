# Open WebUI Functionality

Open WebUI Functions (Filters, Actions, Events) for the SciLifeLab OpenLLM
service, installed through the Open WebUI admin interface.

## Components

### Functions

| Function | Type | Purpose |
| --- | --- | --- |
| [Request Quota Guard](functions/oi_request_quota_guard.py) | Filter | Limits user requests per minute and logs usage. |
| [Rate Limit Enforcer](functions/oi_rate_limit_enforcer.py) | Filter | Limits user requests per minute. |
| [SciLifeLab Email Verification](functions/oi_email_verification_callback.py) | Event | Emails signup verification links; adds verified users to `email-verified`. See [below](#email-verification). |
| [SciLifeLab Account Approval Notification](functions/oi_account_approval_notification.py) | Event | On `pending` → `user`, adds the user to `account-verified` and emails them. See [below](#account-approval-notification). |
| [SciLifeLab Registration Questionnaire](functions/oi_registration_questionnaire.py) | Event | Collects affiliation and planned use from pending users into `whale-auth-tools`. See [below](#registration-questionnaire). |
| [SciLifeLab ALTCHA Browser Gate](functions/oi_altcha_browser_gate.py) | Event | Proof-of-work browser check for the optional `altchaGate` NGINX proxy. See [below](#altcha-browser-gate). |

> [!Warning]
> Activate either Request Quota Guard or Rate Limit Enforcer, not both.

### Actions

- [Rate Limit Feedback](functions/oi_rate_limit_ui_button.py): button showing the
  configured request limit.

### Valves

- [oi_valves.py](functions/oi_valves.py): shared `requests_per_minute` setting
  (default `60`).
- The rate-limit Functions have `requests_per_minute` (default `60`); Request Quota
  Guard also has `log_allowed_requests` and `log_denied_requests` (default `true`).

## Installation

Admins only: Admin Panel > Functions > New Function, paste the file, save, then
enable it. Set Filters and Actions to Global to apply them across models; Event
Functions only need enabling. Edit Valves with the configuration icon.

Common rules for the Event Functions below:

- Open WebUI installs packages from each Function's `requirements` header; bake
  them into the image if runtime installation is disabled.
- A Function stays inactive, logging a warning, until its required environment
  variables are set. Set them on every replica, with secrets from Kubernetes Secrets.
- Functions that register routes need a restart of all replicas after
  installation or environment changes.
- Where a Valve has an environment default, a saved Valve value wins.

## Email verification

Sends a verification link to new `pending` signups via the Gmail API. Clicking
**Verify email** on the link page adds the user to `email-verified`; it does not
approve the account. Changing email removes the membership and invalidates old links.

**Setup**

1. Create group `email-verified` (no permissions, sharing off) and set the default
   signup role to `pending`.
2. Create a Google service account with domain-wide delegation and scope
   `https://www.googleapis.com/auth/gmail.send`.
3. Set the environment variables, install, enable, and restart all replicas.

| Environment variable | Purpose |
| --- | --- |
| `EMAIL_VERIFICATION_URL` | Public HTTPS callback URL with a non-root path, e.g. `https://openllm.example.org/auth/verify-email`. |
| `GMAIL_CREDENTIALS_JSON` | Service-account credentials JSON (Secret). |
| `GMAIL_DELEGATED_USER` | Workspace mailbox to impersonate. |
| `GMAIL_SENDER_EMAIL` | Authorized From address. |
| `WEBUI_SECRET_KEY` / `EMAIL_VERIFICATION_SECRET` | Signing secret, identical on all replicas; the latter is optional and takes precedence. |

| Valve | Default | Env default | Purpose |
| --- | --- | --- | --- |
| `token_ttl` | `24h` | `EMAIL_VERIFICATION_TOKEN_TTL` | Link lifetime (seconds or `s`/`m`/`h`/`d`); also applies to issued links. |
| `sender_name` | `SciLifeLab OpenLLM` | `EMAIL_VERIFICATION_SENDER_NAME` | From display name. |
| `email_subject` | `SciLifeLab OpenLLM: Verify your email address` | `EMAIL_VERIFICATION_SUBJECT` | Email subject. |
| `send_on_signup` | `true` | `EMAIL_VERIFICATION_SEND_ON_SIGNUP` | `false` pauses sending; links and revocation keep working. |
| `resend_cooldown` | `5m` | `EMAIL_VERIFICATION_RESEND_COOLDOWN` | Minimum time between emails per user (in memory, per process). |

**Resend:** `EMAIL_VERIFICATION_URL` + `/resend` (GET confirms, POST sends) for
signed-in, pending, unverified users. To show a button, put this on one line in
Admin Settings > General > **Pending User Overlay Content**:

```html
<form method="post" action="/auth/verify-email/resend"><button type="submit">Resend verification email</button></form>
```

Notes:

- Disabling the Function stops events but keeps its routes until restart; use
  `send_on_signup=false` to pause email only.
- With MFA enabled, pending users get no session, so resend cannot identify them.

## Account approval notification

When a user marked as pending becomes `user` (admin approval or SCIM), adds them
to `account-verified` and sends an approval email via Gmail. No routes; no restart
needed.

**Setup:** create group `account-verified` (no permissions, sharing off), set the
Gmail variables from [Email verification](#email-verification), then install and enable.

| Valve | Default | Env default | Purpose |
| --- | --- | --- | --- |
| `send_on_approval` | `true` | `ACCOUNT_APPROVAL_SEND` | `false` still assigns the group, without email. |
| `sender_name` | `SciLifeLab OpenLLM` | `ACCOUNT_APPROVAL_SENDER_NAME` | From display name. |
| `email_subject` | `SciLifeLab OpenLLM: Your account has been approved` | `ACCOUNT_APPROVAL_SUBJECT` | Email subject. |
| `login_url` | empty | `ACCOUNT_APPROVAL_LOGIN_URL` | HTTPS sign-in link; falls back to `WEBUI_URL`. |
| `require_questionnaire` | `true` | `ACCOUNT_APPROVAL_REQUIRE_QUESTIONNAIRE` | Reverts approvals of users without a [questionnaire](#registration-questionnaire) submission to `pending`. |

Notes:

- Events only carry the new role, so the Function marks pending users in their
  `info`. Users approved before the first event after installation may be unmarked
  and get no group or email.
- `require_questionnaire` undoes approval after the fact; the admin list shows
  `user` until refreshed. Turn it off where the questionnaire is not installed, or
  no one can be approved.
- Promotions to `admin` and role changes without events (e.g. OAuth role
  management) are ignored. Gmail failures are logged, not retried.

## Registration questionnaire

Serves a form at `/auth/registration-questionnaire` to signed-in `pending` users
and stores the answers in `whale-auth-tools`, with the account email as
`contactEmail`. Each account submits once; the submission ID is stored under
`scilifelab_questionnaire` in the user's `info`. Questions come from the
`whale-auth-tools` schema. It does not approve users or send Slack invitations.

The SciLifeLab Open WebUI fork redirects pending users to the form via
`POST /api/v1/registration-questionnaire/status`. With upstream images, link to
the form from **Pending User Overlay Content**:

```html
Please fill in the questionnaire so your account can be approved. <a href="/auth/registration-questionnaire">Fill in the questionnaire</a>
```

**Setup:** deploy `whale-auth-tools`, set the variables below (`openllm-charts`:
`registrationQuestionnaire.enabled=true`), install with ID
`registration_questionnaire`, enable, and restart all replicas.

| Environment variable | Purpose |
| --- | --- |
| `WHALE_AUTH_URL` | Service URL, e.g. `http://whale-auth-tools:8080`. |
| `WHALE_AUTH_API_TOKEN` | Service bearer token (Secret); never sent to browsers. |

| Valve | Default | Purpose |
| --- | --- | --- |
| `require_altcha` | `true` | Embed ALTCHA; keep on while the service has `ALTCHA_REQUIRED=true`. |
| `altcha_widget_url` | `/static/altcha/altcha-3.5.0.min.js` | Same-origin path of the ALTCHA v3 widget. |

## ALTCHA browser gate

Serves a "Checking your browser" page and verifies ALTCHA proof-of-work through
`whale-auth-tools`. Enforcement is done by the optional `altchaGate` NGINX proxy in
`openllm-charts`; the Function alone blocks nothing. JavaScript and HTTPS are required.

**Setup**

1. Bootstrap the first admin with `altchaGate.enabled=false`, then install with ID
   `altcha_browser_gate` and enable it (no Global needed).
2. Set the variables below on every replica; Redis 6.2+ must be shared.
3. Enable `altchaGate.enabled` and restart all replicas.

| Environment variable | Purpose |
| --- | --- |
| `WHALE_AUTH_URL` | Service URL, e.g. `http://whale-auth-tools:8080`. |
| `WHALE_AUTH_API_TOKEN` | Service bearer token (Secret). |
| `ALTCHA_REDIS_URL` | Shared Redis 6.2+ URL (Secret). |
| `ALTCHA_PUBLIC_ORIGINS` | Comma-separated exact HTTPS origins, e.g. `https://openllm.example.org`. |

| Valve | Default | Purpose |
| --- | --- | --- |
| `clearance_ttl` | `3600` | Seconds a verified browser stays cleared (60–86400). |

Routes: `GET /auth/verify-human`, and `POST /api/v1/altcha-gate/{challenge,verify,check}`
(`check` is used internally by the proxy).

Notes:

- With the proxy on, a disabled Function or a service/Redis outage blocks protected
  traffic. To remove the gate, set `altchaGate.enabled=false` and redeploy.
- Clearance is a SameSite=Lax cookie reused until expiry, shared across tabs.
- API, OAuth, email callbacks, static assets, and WebSockets are exempt; password
  signup/signin stay gated. It is not a rate limit or DDoS protection.

## Tests

```bash
cd openwebui-functionality
uv run --extra test pytest
```

Tests cover the Event Functions and Request Quota Guard, with Gmail, `whale-auth-tools`, Redis, and Open WebUI
internals mocked; no real emails or requests are sent.
