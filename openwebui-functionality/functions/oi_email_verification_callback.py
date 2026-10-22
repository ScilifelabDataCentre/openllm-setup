"""
title: SciLifeLab Email Verification
id: email_verification
description: Verify signup email ownership via Gmail API and the email-verified group.
version: 0.4.1
requirements: google-auth,requests,itsdangerous

Required Kubernetes environment variables (the Function stays inactive
until all are set; enable or disable it in the Open WebUI admin interface):
  EMAIL_VERIFICATION_URL=https://your-host/auth/verify-email
  GMAIL_CREDENTIALS_JSON=<Google service-account JSON from your Kubernetes Secret>
  GMAIL_DELEGATED_USER=<real Google Workspace mailbox>
  GMAIL_SENDER_EMAIL=<authorized From/Send As address>
  WEBUI_SECRET_KEY=<shared secret across pods>

Optional:
  EMAIL_VERIFICATION_SECRET=<dedicated shared signing secret>

Admin Valves (saved values override these optional environment defaults):
  token_ttl: EMAIL_VERIFICATION_TOKEN_TTL=24h (seconds or s/m/h/d suffix)
  sender_name: EMAIL_VERIFICATION_SENDER_NAME=SciLifeLab OpenLLM
  email_subject: EMAIL_VERIFICATION_SUBJECT=SciLifeLab OpenLLM: Verify your email address
  send_on_signup: EMAIL_VERIFICATION_SEND_ON_SIGNUP=true
  resend_cooldown: EMAIL_VERIFICATION_RESEND_COOLDOWN=5m

Changing token_ttl also affects existing links. Turning off send_on_signup
pauses new signup and resent emails; existing links and email-change
revocation continue working. Keep the Function enabled for those operations.
Credentials, mail identities, the callback URL, and group remain deployment settings.

Create an Open WebUI group named exactly "email-verified" beforehand,
with NO granted permissions and sharing disabled. Set default signup role
in Open WebUI to "pending"; administrator approval remains separate.

The link displays a confirmation form. Email ownership is marked verified
only on POST, avoiding activation by common email-link preview scanners.
Expired or invalid links show an error page offering a new email.

Signed-in pending users who are not yet in the group can request another
email by POSTing to EMAIL_VERIFICATION_URL + "/resend" (a GET there shows a
button). Add the button to Admin Settings > General > Pending User Overlay
Content, on a single line:
  <form method="post" action="/auth/verify-email/resend"><button type="submit">Resend verification email</button></form>
"""

import asyncio
import base64
import html
import json
import logging
import os
import re
import time
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import BackgroundTasks, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from starlette.routing import Mount

from open_webui.models.functions import Functions
from open_webui.models.groups import Groups
from open_webui.models.users import Users
from open_webui.utils.auth import get_current_user

log = logging.getLogger(__name__)

GROUP_NAME = "email-verified"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GET_ROUTE_NAME = "scilifelab_email_verification_form"
POST_ROUTE_NAME = "scilifelab_email_verification_confirm"
RESEND_GET_ROUTE_NAME = "scilifelab_email_verification_resend_form"
RESEND_POST_ROUTE_NAME = "scilifelab_email_verification_resend"
OWN_ROUTE_NAMES = {
    GET_ROUTE_NAME,
    POST_ROUTE_NAME,
    RESEND_GET_ROUTE_NAME,
    RESEND_POST_ROUTE_NAME,
}
LEGACY_ROUTE_NAME = "email_verification_verify"
REQUIRED_SETTINGS = (
    "EMAIL_VERIFICATION_URL",
    "GMAIL_CREDENTIALS_JSON",
    "GMAIL_DELEGATED_USER",
    "GMAIL_SENDER_EMAIL",
)


def duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)([smhd]?)", value.strip().lower())
    if not match or int(match.group(1)) <= 0:
        raise ValueError("Durations must be positive seconds or like 30m, 24h, 7d")
    multipliers = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
    return int(match.group(1)) * multipliers[match.group(2)]


def page(title: str, content: str, *, status_code: int = 200) -> HTMLResponse:
    """Match Open WebUI's auth layout without external resources or referrers."""
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{html.escape(title)}</title>
  <style>
    /* Mirror the auth page's max-w-md layout and neutral, pill-shaped controls. */
    @font-face {{ font-family: Inter; src: local("Inter"); }}
    :root {{
      color-scheme: light;
      --background: #fff;
      --foreground: #000;
      --muted: oklch(0.51 0 0);
      --button: oklch(0.42 0 0 / 5%);
      --button-hover: oklch(0.42 0 0 / 10%);
      --button-text: #000;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        color-scheme: dark;
        --background: #000;
        --foreground: oklch(0.94 0 0);
        --muted: oklch(0.77 0 0);
        --button: oklch(0.94 0 0 / 5%);
        --button-hover: oklch(0.94 0 0 / 10%);
        --button-text: oklch(0.85 0 0);
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--background);
      color: var(--foreground);
      font-family: -apple-system, BlinkMacSystemFont, Inter, ui-sans-serif,
                   system-ui, "Segoe UI", Roboto, sans-serif;
      font-size: .875rem;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }}
    main {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      min-height: 100vh;
      min-height: 100dvh;
      padding: 2.5rem 1.5rem;
    }}
    .verification {{
      width: 100%;
      max-width: 28rem;
      margin: auto;
      padding-bottom: 2.5rem;
      text-align: center;
      overflow-wrap: anywhere;
    }}
    h1 {{ margin: 0 0 1rem; font-size: 1.5rem; line-height: 2rem; font-weight: 400; }}
    p {{ margin: .75rem 0 0; color: var(--muted); }}
    form {{ margin-top: 1.25rem; }}
    button {{
      display: block;
      width: 100%;
      min-height: 2.75rem;
      padding: .625rem 1rem;
      border: 1px solid transparent;
      border-radius: 9999px;
      background: var(--button);
      color: var(--button-text);
      font: inherit;
      cursor: pointer;
    }}
    button:hover {{ background: var(--button-hover); color: var(--foreground); }}
    a {{ color: inherit; text-underline-offset: .2em; }}
    a:hover {{ color: var(--foreground); }}
    button:focus-visible, a:focus-visible {{
      outline: 2px solid currentColor;
      outline-offset: 4px;
    }}
    .back-link {{ margin-top: 1.5rem; }}
  </style>
</head>
<body>
  <main>
    <section class="verification" aria-labelledby="verification-title">
      <h1 id="verification-title">{html.escape(title)}</h1>
      {content}
      <p class="back-link"><a href="/">Back to Open WebUI</a></p>
    </section>
  </main>
</body>
</html>"""
    return HTMLResponse(
        document,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


class Event:
    class Valves(BaseModel):
        model_config = ConfigDict(validate_default=True)

        token_ttl: str = Field(
            default=os.getenv("EMAIL_VERIFICATION_TOKEN_TTL", "24h"),
            description=(
                "Verification link lifetime: positive seconds or s/m/h/d, e.g. 24h. "
                "Changes also affect already-issued links."
            ),
        )
        sender_name: str = Field(
            default=os.getenv("EMAIL_VERIFICATION_SENDER_NAME", "SciLifeLab OpenLLM"),
            description="Display name in the email From header; does not change the sending mailbox.",
        )
        email_subject: str = Field(
            default=os.getenv(
                "EMAIL_VERIFICATION_SUBJECT",
                "SciLifeLab OpenLLM: Verify your email address",
            ),
            description="Subject of signup verification emails.",
        )
        send_on_signup: bool = Field(
            default=TypeAdapter(bool).validate_python(
                os.getenv("EMAIL_VERIFICATION_SEND_ON_SIGNUP", "true").strip()
            ),
            description=(
                "Send verification emails on signup and resend requests. Turn off to "
                "pause new emails while existing links and email-change revocation "
                "continue working."
            ),
        )
        resend_cooldown: str = Field(
            default=os.getenv("EMAIL_VERIFICATION_RESEND_COOLDOWN", "5m"),
            description=(
                "Minimum time between emails to the same user, enforced per backend "
                "process: positive seconds or s/m/h/d, e.g. 5m."
            ),
        )

        @field_validator("token_ttl", "resend_cooldown")
        @classmethod
        def validate_duration(cls, value: str) -> str:
            duration_seconds(value)
            return value.strip().lower()

        @field_validator("sender_name", "email_subject")
        @classmethod
        def validate_email_header(cls, value: str) -> str:
            if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError(
                    "Email header text must be nonempty and contain no control characters"
                )
            return value.strip()

    def __init__(self):
        self.valves = self.Valves()
        self._function_id = None
        self._setup_lock = asyncio.Lock()
        self._registered_app = None
        self._warned_missing = False
        self._last_sent: dict[str, float] = {}

    @property
    def missing_settings(self) -> list[str]:
        return [name for name in REQUIRED_SETTINGS if not os.getenv(name, "").strip()]

    @property
    def ttl(self) -> int:
        return duration_seconds(self.valves.token_ttl)

    async def refresh_valves(self):
        # Custom HTTP routes bypass the Event dispatcher, which normally loads
        # saved valves. Read them here so every replica uses the current TTL.
        if self._function_id is not None:
            values = await Functions.get_function_valves_by_id(self._function_id)
            self.valves = self.Valves(**(values or {}))

    @property
    def verification_url(self) -> str:
        url = os.environ["EMAIL_VERIFICATION_URL"].strip()
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.netloc
            or not parts.path.startswith("/")
            or parts.path == "/"
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "EMAIL_VERIFICATION_URL must be an HTTPS URL with a path and no query/fragment"
            )
        return url

    @property
    def verification_path(self) -> str:
        return urlsplit(self.verification_url).path

    @property
    def resend_path(self) -> str:
        return self.verification_path.rstrip("/") + "/resend"

    @property
    def serializer(self) -> URLSafeTimedSerializer:
        # WEBUI_SECRET_KEY is already distributed to all of your K8s replicas.
        # A purpose-specific salt prevents cross-use with other signed tokens.
        signing_key = (
            os.getenv("EMAIL_VERIFICATION_SECRET") or os.environ["WEBUI_SECRET_KEY"]
        )
        if not signing_key.strip():
            raise RuntimeError("The email-verification signing secret is empty")
        return URLSafeTimedSerializer(signing_key, salt="openwebui-email-verification")

    async def event(
        self,
        event: dict,
        __event_name__: str | None = None,
        __id__: str | None = None,
        __app__=None,
        __request__=None,
    ):
        if __id__ is not None:
            self._function_id = __id__
        # Deployments without these settings, such as environments restored
        # from another database dump, must not register routes or send email.
        missing = self.missing_settings
        if missing:
            if not self._warned_missing:
                log.warning(
                    "Email verification inactive; missing environment variables: %s",
                    ", ".join(missing),
                )
                self._warned_missing = True
            return

        # Event Functions run separately in each backend process. Install
        # our routes on every process during its startup event, and also
        # when any later event first reaches a newly enabled/edited Function.
        if __app__ is not None:
            await self.setup(__app__)

        if __event_name__ == "auth.signup":
            await self.on_signup(event)
        elif __event_name__ == "user.updated":
            await self.on_user_updated(event)

    async def setup(self, app):
        if app is None or app is self._registered_app:
            return

        async with self._setup_lock:
            if app is self._registered_app:
                return

            # Fail at verification time if the group is missing, but still
            # register the route now so an invalid token returns 400, not a SPA 404.
            path = self.verification_path

            async def show_confirmation(token: str = Query("")):
                try:
                    if not token or len(token) > 4096:
                        raise HTTPException(400, "Verification token is missing or invalid")
                    await self.refresh_valves()
                    self.decode_token(token)  # Validate before displaying the form.
                except HTTPException as error:
                    return self.failure_page(error)
                safe_token = html.escape(token, quote=True)
                return page(
                    "Confirm your email address",
                    "<p>Confirm that you own this email address.</p>"
                    f'<form method="post" action="{html.escape(path, quote=True)}">'
                    f'<input type="hidden" name="token" value="{safe_token}">'
                    '<button type="submit">Verify email</button></form>',
                )

            async def complete_confirmation(request: Request):
                try:
                    if int(request.headers.get("content-length", "0")) > 8192:
                        raise HTTPException(413, "Form too large")
                    raw = await request.body()
                    if len(raw) > 8192:
                        raise HTTPException(413, "Form too large")
                    params = parse_qs(raw.decode("utf-8", errors="strict"))
                    token = params.get("token", [""])[0]
                    if not token or len(token) > 4096:
                        raise HTTPException(400, "Verification token is missing or invalid")
                    await self.refresh_valves()
                    return await self.verify(token)
                except HTTPException as error:
                    return self.failure_page(error)

            resend_path = self.resend_path

            async def show_resend(request: Request):
                await self.refresh_valves()
                user = await self.signed_in_user(request)
                refusal = await self.resend_refusal(user)
                if refusal:
                    return refusal
                return page(
                    "Resend verification email",
                    f"<p>A new verification link will be sent to "
                    f"{html.escape(user.email)}.</p>"
                    f'<form method="post" action="{html.escape(resend_path, quote=True)}">'
                    '<button type="submit">Resend verification email</button></form>',
                )

            async def complete_resend(request: Request):
                # The auth cookie is SameSite=Lax by default; also reject
                # cross-site form posts explicitly.
                origin = request.headers.get("origin")
                expected = "{0.scheme}://{0.netloc}".format(urlsplit(self.verification_url))
                if (origin and origin != expected) or request.headers.get(
                    "sec-fetch-site", "same-origin"
                ) not in {"same-origin", "none"}:
                    raise HTTPException(403, "Cross-site request rejected")
                await self.refresh_valves()
                return await self.resend(await self.signed_in_user(request))

            # Open WebUI mounts its SPA as Mount('/') in main.py. Appending a
            # new route via add_api_route() *after* Mount('/') means the SPA
            # responds first (HTTP 200 + frontend 404). Move new routes BEFORE
            # that mount. This also removes old handlers on hot-reload.
            routes = app.router.routes

            def is_ours(route):
                name = getattr(route, "name", None)
                route_path = getattr(route, "path", None)
                return name in OWN_ROUTE_NAMES or (
                    name == LEGACY_ROUTE_NAME
                    and route_path
                    in {"/api/v1/email-verification/verify", "/auth/verify-email"}
                )

            for route in list(routes):
                if is_ours(route):
                    routes.remove(route)

            for route in routes:
                if getattr(route, "path", None) not in {path, resend_path}:
                    continue
                if isinstance(route, Mount):
                    continue
                if getattr(route, "methods", set()) & {"GET", "POST"}:
                    raise RuntimeError(
                        f"Email verification URL conflicts with existing route: {route.path}"
                    )

            app.add_api_route(
                path,
                show_confirmation,
                methods=["GET"],
                name=GET_ROUTE_NAME,
                include_in_schema=False,
            )
            app.add_api_route(
                path,
                complete_confirmation,
                methods=["POST"],
                name=POST_ROUTE_NAME,
                include_in_schema=False,
            )
            app.add_api_route(
                resend_path,
                show_resend,
                methods=["GET"],
                name=RESEND_GET_ROUTE_NAME,
                include_in_schema=False,
            )
            app.add_api_route(
                resend_path,
                complete_resend,
                methods=["POST"],
                name=RESEND_POST_ROUTE_NAME,
                include_in_schema=False,
            )

            added = [
                route
                for route in routes
                if getattr(route, "name", None) in OWN_ROUTE_NAMES
            ]
            for route in added:
                routes.remove(route)
            spa_index = next(
                (
                    i
                    for i, route in enumerate(routes)
                    if isinstance(route, Mount) and route.path in ("", "/")
                ),
                len(routes),
            )
            for offset, route in enumerate(added):
                routes.insert(spa_index + offset, route)

            self._registered_app = app
            log.info(
                "Email verification routes registered at %s and %s before SPA mount (routes=%d)",
                path,
                resend_path,
                len(added),
            )

    async def on_signup(self, event: dict):
        if not self.valves.send_on_signup:
            return

        subject = event.get("subject") or {}
        actor = event.get("actor") or {}
        user_id = subject.get("id") or actor.get("id")
        if not user_id:
            log.error("auth.signup event has no user ID")
            return

        group = await Groups.get_group_by_name(GROUP_NAME)
        if group is None:
            raise RuntimeError(f"Create group '{GROUP_NAME}' in Open WebUI Admin first")

        user = await Users.get_user_by_id(str(user_id))
        if user is None or not user.email:
            log.error(
                "Cannot send verification email; signup account missing email or user ID"
            )
            return
        if user.role != "pending":
            log.warning(
                "Skipping verification email: signup user %s is not pending", user.id
            )
            return

        await self.send_link(user)

    async def send_link(self, user):
        token = self.serializer.dumps(
            {"user_id": user.id, "email": user.email.strip().lower()}
        )
        link = self.verification_url + "?" + urlencode({"token": token})
        self._last_sent[user.id] = time.monotonic()
        await asyncio.to_thread(self.send_verification_email, user.email, link)
        log.info("Gmail accepted verification email for user id %s", user.id)

    async def signed_in_user(self, request: Request):
        # Reuse Open WebUI's session checks (signature, revocation, session
        # stamp). Pending users receive the token cookie at signup.
        try:
            return await get_current_user(request, Response(), BackgroundTasks(), None)
        except HTTPException:
            return None

    async def resend_refusal(self, user) -> HTMLResponse | None:
        """Explain why this visitor cannot request a new email, or return None."""
        if user is None:
            return page(
                "Sign in required",
                "<p>Sign in with the account you registered, then request a new "
                "verification email from the account pending screen.</p>",
                status_code=401,
            )
        if user.role != "pending" or not user.email:
            return page(
                "No verification needed",
                "<p>Verification emails are only sent to accounts awaiting activation.</p>",
            )
        group = await Groups.get_group_by_name(GROUP_NAME)
        if group is None:
            log.error("Missing group '%s'", GROUP_NAME)
            return page(
                "Resend unavailable",
                "<p>Email verification is temporarily unavailable.</p>",
                status_code=503,
            )
        memberships = await Groups.get_groups_by_member_id(user.id)
        if any(item.id == group.id for item in memberships):
            return page(
                "Email already verified",
                "<p>Your email address has already been verified.</p>"
                "<p>Your account is still awaiting administrator approval.</p>",
            )
        if not self.valves.send_on_signup:
            return page(
                "Resend unavailable",
                "<p>Verification emails are paused. Please try again later.</p>",
                status_code=503,
            )
        return None

    async def resend(self, user) -> HTMLResponse:
        refusal = await self.resend_refusal(user)
        if refusal:
            return refusal

        # Per-process throttle; replicas each allow one email per cooldown.
        now = time.monotonic()
        cooldown = duration_seconds(self.valves.resend_cooldown)
        self._last_sent = {
            key: sent for key, sent in self._last_sent.items() if now - sent < cooldown
        }
        if user.id in self._last_sent:
            wait = int(cooldown - (now - self._last_sent[user.id])) + 1
            return page(
                "Please wait",
                "<p>A verification email was sent recently. Check your inbox and "
                f"spam folder, or try again in {wait} seconds.</p>",
                status_code=429,
            )

        try:
            await self.send_link(user)
        except Exception:
            self._last_sent.pop(user.id, None)
            log.exception("Failed to resend verification email for user id %s", user.id)
            return page(
                "Email not sent",
                "<p>The verification email could not be sent. Please try again later.</p>",
                status_code=502,
            )
        return page(
            "Verification email sent",
            f"<p>A new verification link was sent to {html.escape(user.email)}.</p>"
            "<p>Links from earlier emails remain valid until they expire.</p>",
        )

    async def on_user_updated(self, event: dict):
        # A verified marker must not carry over to a different email address.
        fields = (event.get("data") or {}).get("updated_fields") or []
        if "email" not in fields:
            return
        user_id = (event.get("subject") or {}).get("id")
        if not user_id:
            return
        group = await Groups.get_group_by_name(GROUP_NAME)
        if group:
            await Groups.remove_users_from_group(group.id, [str(user_id)])
            log.info(
                "Revoked email verification after email change, user id %s", user_id
            )

    def failure_page(self, error: HTTPException) -> HTMLResponse:
        """Render a callback error; offer a new email when the link itself is bad."""
        if error.status_code == 400:
            # Expired, tampered, or outdated links. The resend page checks the
            # session and eligibility before showing its own POST button.
            content = (
                "<p>Request a new verification email while signed in with the "
                "account you registered.</p>"
                f'<form method="get" action="{html.escape(self.resend_path, quote=True)}">'
                '<button type="submit">Request a new verification email</button></form>'
            )
        elif error.status_code >= 500:
            content = "<p>Please try again later.</p>"
        else:
            content = ""
        return page(str(error.detail), content, status_code=error.status_code)

    def decode_token(self, token: str) -> dict:
        try:
            payload = self.serializer.loads(token, max_age=self.ttl)
        except SignatureExpired as exc:
            raise HTTPException(400, "Verification link expired") from exc
        except BadSignature as exc:
            raise HTTPException(400, "Invalid verification link") from exc
        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(key), str) and payload[key]
            for key in ("user_id", "email")
        ):
            raise HTTPException(400, "Invalid verification token payload")
        return payload

    async def verify(self, token: str) -> HTMLResponse:
        payload = self.decode_token(token)
        user = await Users.get_user_by_id(payload["user_id"])
        if user is None:
            raise HTTPException(404, "Account not found")
        if not user.email or user.email.strip().lower() != payload["email"]:
            raise HTTPException(400, "Account email has changed")

        group = await Groups.get_group_by_name(GROUP_NAME)
        if group is None:
            log.error("Missing group '%s'", GROUP_NAME)
            raise HTTPException(503, "Email verification is temporarily unavailable")

        memberships = await Groups.get_groups_by_member_id(user.id)
        if not any(item.id == group.id for item in memberships):
            # Idempotent in current Open WebUI: already-existing user IDs are
            # skipped by add_users_to_group(), and no role is changed here.
            result = await Groups.add_users_to_group(group.id, [user.id])
            if not result:
                raise HTTPException(500, "Unable to save email verification")
            log.info("Verified email ownership for user id %s", user.id)

        return page(
            "Email verified",
            "<p>Your email address has been verified.</p>"
            "<p>Your account is still awaiting administrator approval.</p>",
        )

    def send_verification_email(self, recipient: str, link: str):
        credentials_info = json.loads(os.environ["GMAIL_CREDENTIALS_JSON"])
        delegated_user = os.environ["GMAIL_DELEGATED_USER"].strip()
        sender_email = os.environ["GMAIL_SENDER_EMAIL"].strip()

        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=[GMAIL_SEND_SCOPE],
            subject=delegated_user,
        )

        message = EmailMessage()
        message["From"] = Address(display_name=self.valves.sender_name, addr_spec=sender_email)
        message["To"] = Address(addr_spec=recipient.strip())
        message["Subject"] = self.valves.email_subject
        message["Date"] = formatdate(usegmt=True)
        message.set_content(
            "Welcome!\n\n"
            "Please confirm ownership of your email address by following this link:\n\n"
            f"{link}\n\n"
            "If you did not register for an account, ignore this message.\n"
        )
        raw = base64.urlsafe_b64encode(message.as_bytes(policy=SMTP)).decode("ascii")

        with AuthorizedSession(
            credentials, refresh_timeout=15, max_refresh_attempts=0
        ) as session:
            response = session.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                json={"raw": raw},
                timeout=(10, 30),
                allow_redirects=False,
            )
            response.raise_for_status()
