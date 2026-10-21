"""
title: SciLifeLab Account Approval Notification
oi_type: event
function_id: account_approval_notification
description: Email users via the Gmail API when their role changes from pending to user.
author: SciLifeLab
version: 0.1.0
requirements: google-auth,requests

Required Kubernetes environment variables (the Function stays inactive
until all are set; they are shared with the email verification Function):
  GMAIL_CREDENTIALS_JSON=<Google service-account JSON from your Kubernetes Secret>
  GMAIL_DELEGATED_USER=<real Google Workspace mailbox>
  GMAIL_SENDER_EMAIL=<authorized From/Send As address>

Admin Valves (saved values override these optional environment defaults):
  send_on_approval: ACCOUNT_APPROVAL_SEND=true
  sender_name: ACCOUNT_APPROVAL_SENDER_NAME=SciLifeLab OpenLLM
  email_subject: ACCOUNT_APPROVAL_SUBJECT=SciLifeLab OpenLLM: Your account has been approved
  login_url: ACCOUNT_APPROVAL_LOGIN_URL= (HTTPS; empty uses Open WebUI's WEBUI_URL)

Open WebUI's role events carry only the new role. The Function therefore
records a marker in the user's info while the user is pending (on account
creation, on a change to pending, and for all pending users when each
backend process first handles an event) and emails only marked users whose role
becomes "user". Demotions such as admin -> user send nothing.
"""

import asyncio
import base64
import json
import logging
import os
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
from urllib.parse import urlsplit

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from open_webui.models.config import Config
from open_webui.models.users import Users

log = logging.getLogger(__name__)

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
REQUIRED_SETTINGS = (
    "GMAIL_CREDENTIALS_JSON",
    "GMAIL_DELEGATED_USER",
    "GMAIL_SENDER_EMAIL",
)
# Key in the user's info JSON marking an account that awaits approval.
PENDING_MARKER = "scilifelab_awaiting_approval"


class Event:
    class Valves(BaseModel):
        model_config = ConfigDict(validate_default=True)

        send_on_approval: bool = Field(
            default=TypeAdapter(bool).validate_python(
                os.getenv("ACCOUNT_APPROVAL_SEND", "true").strip()
            ),
            description=(
                "Send an email when a pending user is approved. Turn off to pause "
                "emails; pending users are still tracked."
            ),
        )
        sender_name: str = Field(
            default=os.getenv("ACCOUNT_APPROVAL_SENDER_NAME", "SciLifeLab OpenLLM"),
            description="Display name in the email From header; does not change the sending mailbox.",
        )
        email_subject: str = Field(
            default=os.getenv(
                "ACCOUNT_APPROVAL_SUBJECT",
                "SciLifeLab OpenLLM: Your account has been approved",
            ),
            description="Subject of account approval emails.",
        )
        login_url: str = Field(
            default=os.getenv("ACCOUNT_APPROVAL_LOGIN_URL", ""),
            description=(
                "HTTPS link to Open WebUI included in the email. Empty uses the "
                "WEBUI_URL setting, or omits the link when that is also unset."
            ),
        )

        @field_validator("sender_name", "email_subject")
        @classmethod
        def validate_email_header(cls, value: str) -> str:
            if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError(
                    "Email header text must be nonempty and contain no control characters"
                )
            return value.strip()

        @field_validator("login_url")
        @classmethod
        def validate_login_url(cls, value: str) -> str:
            value = value.strip()
            if value and not is_https_url(value):
                raise ValueError("login_url must be empty or an HTTPS URL")
            return value

    def __init__(self):
        self.valves = self.Valves()
        self._warned_missing = False
        self._backfill_lock = asyncio.Lock()
        self._backfilled_app = None

    @property
    def missing_settings(self) -> list[str]:
        return [name for name in REQUIRED_SETTINGS if not os.getenv(name, "").strip()]

    async def event(
        self,
        event: dict,
        __event_name__: str | None = None,
        __app__=None,
    ):
        missing = self.missing_settings
        if missing:
            if not self._warned_missing:
                log.warning(
                    "Account approval notification inactive; missing environment variables: %s",
                    ", ".join(missing),
                )
                self._warned_missing = True
            return

        # Users who signed up before this process loaded the Function have no
        # marker yet. Mark them once per process; repeated runs are harmless.
        if __app__ is not None:
            await self.backfill(__app__)

        user_id = (event.get("subject") or {}).get("id")
        if __event_name__ in {"user.created", "auth.signup"}:
            await self.on_role_observed(user_id)
        elif __event_name__ == "user.role_updated":
            await self.on_role_observed(user_id)
        elif __event_name__ == "user.updated":
            # SCIM changes roles through user.updated rather than role_updated.
            fields = (event.get("data") or {}).get("updated_fields") or []
            if "role" in fields:
                await self.on_role_observed(user_id)

    async def backfill(self, app):
        if app is self._backfilled_app:
            return
        async with self._backfill_lock:
            if app is self._backfilled_app:
                return
            result = await Users.get_users(filter={"roles": ["pending"]})
            marked = 0
            for user in (result or {}).get("users", []):
                if not (user.info or {}).get(PENDING_MARKER):
                    await self.set_marker(user, True)
                    marked += 1
            self._backfilled_app = app
            if marked:
                log.info("Marked %d existing pending users for approval emails", marked)

    async def on_role_observed(self, user_id):
        if not user_id:
            return
        # Read the stored role: event data is not trusted to be current.
        user = await Users.get_user_by_id(str(user_id))
        if user is None:
            return
        marked = bool((user.info or {}).get(PENDING_MARKER))

        if user.role == "pending":
            if not marked:
                await self.set_marker(user, True)
            return
        if not marked:
            return
        if user.role != "user":
            # Approved straight to admin, or another role: no email.
            await self.set_marker(user, False)
            return
        if not self.valves.send_on_approval:
            log.info("Approval email paused; not notifying user id %s", user.id)
            await self.set_marker(user, False)
            return
        if not user.email:
            log.error("Cannot send approval email; user id %s has no email", user.id)
            await self.set_marker(user, False)
            return

        # Clear first so a repeated event cannot send twice. A failed send is
        # logged and not retried.
        await self.set_marker(user, False)
        link = await self.login_link()
        await asyncio.to_thread(self.send_approval_email, user.email, user.name, link)
        log.info("Gmail accepted approval email for user id %s", user.id)

    async def set_marker(self, user, value: bool):
        info = dict(user.info or {})
        if value:
            info[PENDING_MARKER] = True
        else:
            info.pop(PENDING_MARKER, None)
        await Users.update_user_by_id(user.id, {"info": info})
        user.info = info

    async def login_link(self) -> str:
        if self.valves.login_url:
            return self.valves.login_url
        try:
            url = str(await Config.get("webui.url") or "").strip()
        except Exception:
            log.exception("Could not read WEBUI_URL for the approval email")
            return ""
        return url if is_https_url(url) else ""

    def send_approval_email(self, recipient: str, name: str | None, link: str):
        credentials_info = json.loads(os.environ["GMAIL_CREDENTIALS_JSON"])
        delegated_user = os.environ["GMAIL_DELEGATED_USER"].strip()
        sender_email = os.environ["GMAIL_SENDER_EMAIL"].strip()

        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=[GMAIL_SEND_SCOPE],
            subject=delegated_user,
        )

        greeting = f"Hello {name.strip()},\n\n" if name and name.strip() else "Hello,\n\n"
        sign_in = f"You can now sign in at:\n\n{link}\n\n" if link else "You can now sign in.\n\n"
        message = EmailMessage()
        message["From"] = Address(display_name=self.valves.sender_name, addr_spec=sender_email)
        message["To"] = Address(addr_spec=recipient.strip())
        message["Subject"] = self.valves.email_subject
        message["Date"] = formatdate(usegmt=True)
        message.set_content(
            greeting
            + f"Your {self.valves.sender_name} account has been approved by an administrator.\n\n"
            + sign_in
            + "If you did not register for an account, ignore this message.\n"
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


def is_https_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.netloc)
