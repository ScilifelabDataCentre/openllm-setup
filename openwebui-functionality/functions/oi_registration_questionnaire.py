"""
title: SciLifeLab Registration Questionnaire
oi_type: event
function_id: registration_questionnaire
description: Ask pending users about their affiliation and planned use, and store the answers in whale-auth-tools.
author: SciLifeLab
version: 0.2.1
requirements: httpx==0.28.1

Deployment: WHALE_AUTH_URL, WHALE_AUTH_API_TOKEN (the same settings as the
ALTCHA browser gate). The Function stays inactive until both are set. The token
never reaches the browser.

Serves GET/POST /auth/registration-questionnaire to signed-in pending users.
POST /api/v1/registration-questionnaire/status returns {"required": bool}; the
SciLifeLab Open WebUI fork calls it from the pending screen and redirects to the
form. Linking from the Pending User Overlay Content also works. Answers are stored only in
whale-auth-tools, with the account email as contactEmail so administrators can
match them to the account; the user's info records only the submission ID.

When require_altcha is on (needed when whale-auth-tools runs with
ALTCHA_REQUIRED=true), the form embeds the ALTCHA widget served at
altcha_widget_url, by default the copy openllm-charts mounts into /static/altcha/
when registrationQuestionnaire.enabled=true.
Restart all replicas after installation so each process registers the routes.
"""

import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from open_webui.models.functions import Functions
from open_webui.models.users import Users
from open_webui.utils.auth import get_current_user
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.routing import Mount

log = logging.getLogger(__name__)
PAGE = "/auth/registration-questionnaire"
# POST, so a missing Function is not mistaken for the SPA's HTTP-200 GET fallback.
STATUS = "/api/v1/registration-questionnaire/status"
ROUTE_PREFIX = "registration_questionnaire_"
REQUIRED = ("WHALE_AUTH_URL", "WHALE_AUTH_API_TOKEN")
# Key in the user's info JSON holding the submission ID; answers stay in whale-auth-tools.
INFO_KEY = "scilifelab_questionnaire"
MAX_BODY = 32768
OTHER = "Other"


class Unavailable(Exception):
    pass


class Event:
    class Valves(BaseModel):
        model_config = ConfigDict(validate_default=True)

        require_altcha: bool = Field(
            default=True,
            description="Embed the ALTCHA widget and send its payload. Required when whale-auth-tools has ALTCHA_REQUIRED=true.",
        )
        altcha_widget_url: str = Field(
            default="/static/altcha/altcha-3.5.0.min.js",
            description="Same-origin path of the ALTCHA v3 widget module.",
        )

        @field_validator("altcha_widget_url")
        @classmethod
        def validate_widget_url(cls, value: str) -> str:
            if not re.fullmatch(r"/(?!/)[A-Za-z0-9._~/-]*", value):
                raise ValueError("altcha_widget_url must be a same-origin path such as /static/altcha.min.js")
            return value

    def __init__(self):
        self.valves = self.Valves()
        self._function_id = None
        self._app = None
        self._warned = False
        self._submitting = set()

    async def event(self, event: dict, __id__: str | None = None, __app__=None):
        self._function_id = __id__ or self._function_id
        missing = [key for key in REQUIRED if not os.getenv(key, "").strip()]
        if missing:
            if not self._warned:
                log.warning("Registration questionnaire inactive; missing: %s", ", ".join(missing))
                self._warned = True
            return
        if __app__ is not None:
            self.setup(__app__)

    def setup(self, app):
        if app is self._app:
            return
        parsed = urlsplit(self.service)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username \
                or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid WHALE_AUTH_URL")
        routes = app.router.routes
        routes[:] = [r for r in routes if not (getattr(r, "name", "") or "").startswith(ROUTE_PREFIX)]
        if any(getattr(r, "path", None) in (PAGE, STATUS) and not isinstance(r, Mount) for r in routes):
            raise RuntimeError("Registration questionnaire route already exists")
        app.add_api_route(PAGE, self.show, methods=["GET"], name=ROUTE_PREFIX + "show", include_in_schema=False)
        app.add_api_route(PAGE, self.submit, methods=["POST"], name=ROUTE_PREFIX + "submit", include_in_schema=False)
        app.add_api_route(STATUS, self.status, methods=["POST"], name=ROUTE_PREFIX + "status", include_in_schema=False)
        # Open WebUI's SPA is Mount('/'); routes appended after it are never reached.
        added = [r for r in routes if (getattr(r, "name", "") or "").startswith(ROUTE_PREFIX)]
        for route in added:
            routes.remove(route)
        index = next((i for i, r in enumerate(routes) if isinstance(r, Mount) and r.path in ("", "/")), len(routes))
        routes[index:index] = added
        self._app = app
        log.info("Registration questionnaire routes registered at %s and %s", PAGE, STATUS)

    @property
    def service(self) -> str:
        return os.environ["WHALE_AUTH_URL"].strip().rstrip("/")

    async def ready(self):
        # Registered routes survive disabling the Function; refuse explicitly.
        function = await Functions.get_function_by_id(self._function_id) if self._function_id else None
        if not function or not function.is_active or not all(os.getenv(key, "").strip() for key in REQUIRED):
            raise Unavailable()
        # Custom routes bypass the Event dispatcher, which normally loads saved Valves.
        self.valves = self.Valves(**(await Functions.get_function_valves_by_id(self._function_id) or {}))

    async def upstream(self, method: str, path: str, payload: dict | None = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                return await client.request(method, f"{self.service}{path}", json=payload, headers={
                    "Authorization": f"Bearer {os.environ['WHALE_AUTH_API_TOKEN'].strip()}"})
        except httpx.HTTPError:
            raise Unavailable() from None

    async def fetch_json(self, method: str, path: str) -> dict:
        response = await self.upstream(method, path)
        try:
            data = response.json() if response.status_code == 200 else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            log.warning("whale-auth-tools %s %s returned HTTP %s", method, path, response.status_code)
            raise Unavailable()
        return data

    async def schema(self) -> dict:
        data = await self.fetch_json("GET", "/api/v1/questionnaire/schema")
        lists = ("positions", "scilifelabAffiliations", "plannedUses")
        if not all(isinstance(data.get(key), list) and data[key]
                   and all(isinstance(item, str) for item in data[key]) for key in lists) \
                or not isinstance(data.get("questions"), dict):
            raise Unavailable()
        return data

    async def signed_in_user(self, request: Request):
        # Reuse Open WebUI's session checks. Pending users receive the token cookie at signup.
        try:
            return await get_current_user(request, Response(), BackgroundTasks(), None)
        except HTTPException:
            return None

    def refusal(self, user) -> HTMLResponse | None:
        if user is None:
            return page("Sign in required", "<p>Sign in with the account you registered, "
                        "then open the questionnaire from the account pending screen.</p>", status_code=401)
        if user.role != "pending":
            return page("No questionnaire needed",
                        "<p>The questionnaire is only for accounts awaiting approval.</p>")
        if (user.info or {}).get(INFO_KEY):
            return submitted_page()
        if not user.email:
            return unavailable_page()
        return None

    async def status(self, request: Request):
        """Tell the pending screen whether to send this user to the form."""
        try:
            await self.ready()
        except Unavailable:
            return JSONResponse({"required": False}, status_code=503, headers={"Cache-Control": "no-store"})
        user = await self.signed_in_user(request)
        if user is None:
            raise HTTPException(401, "Not authenticated")
        required = user.role == "pending" and bool(user.email) and not (user.info or {}).get(INFO_KEY)
        return JSONResponse({"required": required}, headers={"Cache-Control": "no-store"})

    async def show(self, request: Request):
        try:
            await self.ready()
            user = await self.signed_in_user(request)
            return self.refusal(user) or await self.form(user, {})
        except Unavailable:
            return unavailable_page()

    async def submit(self, request: Request):
        # The auth cookie is SameSite=Lax by default; also reject cross-site form posts.
        origin = request.headers.get("origin")
        if (origin and urlsplit(origin).netloc != request.headers.get("host")) or \
                request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "none"}:
            raise HTTPException(403, "Cross-site request rejected")
        try:
            await self.ready()
            user = await self.signed_in_user(request)
            refusal = self.refusal(user)
            if refusal:
                return refusal
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > MAX_BODY:
                    raise HTTPException(413, "Form too large")
            try:
                form = parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=64)
            except ValueError:
                raise HTTPException(400, "Invalid form") from None
            if user.id in self._submitting:
                return page("Submission in progress", "<p>Your answers are already being submitted.</p>",
                            status_code=409)
            self._submitting.add(user.id)
            try:
                return await self.store(user, form)
            finally:
                self._submitting.discard(user.id)
        except Unavailable:
            return unavailable_page()

    async def store(self, user, form: dict) -> HTMLResponse:
        schema = await self.schema()
        values, errors = answers(form, schema)
        altcha = form.get("altcha", [""])[0]
        if self.valves.require_altcha and not altcha and not errors:
            errors.append("Please complete the verification before submitting.")
        if errors:
            return await self.form(user, values, errors, schema, status_code=422)
        payload = {**values, "contactEmail": user.email}
        if self.valves.require_altcha:
            payload["altchaPayload"] = altcha
        response = await self.upstream("POST", "/api/v1/questionnaire/submissions", payload)
        if response.status_code == 422:
            # Answers were validated above, so the usual cause is an expired challenge.
            log.info("whale-auth-tools rejected questionnaire for user id %s", user.id)
            return await self.form(user, values, ["Verification expired or failed, or an answer was not "
                                                  "accepted. Please check your answers and submit again."],
                                   schema, status_code=422)
        try:
            data = response.json() if response.status_code == 201 else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict) or not data.get("id"):
            log.warning("whale-auth-tools questionnaire submission returned HTTP %s", response.status_code)
            raise Unavailable()
        info = dict(user.info or {})
        info[INFO_KEY] = {"id": str(data["id"]),
                          "submittedAt": str(data.get("submittedAt") or datetime.now(timezone.utc).isoformat())}
        await Users.update_user_by_id(user.id, {"info": info})
        log.info("Stored questionnaire %s for user id %s", info[INFO_KEY]["id"], user.id)
        return submitted_page(thanks=True)

    async def form(self, user, values: dict, errors: list[str] | None = None, schema: dict | None = None,
                   status_code: int = 200) -> HTMLResponse:
        schema = schema or await self.schema()
        challenge = None
        if self.valves.require_altcha:
            challenge = await self.fetch_json("POST", "/api/v1/altcha/challenge")
        return page("Tell us about yourself", render_form(schema, values, errors or [], user.email, challenge,
                                                          self.valves.altcha_widget_url),
                    status_code=status_code, script=self.valves.altcha_widget_url if challenge else None)


def answers(form: dict, schema: dict) -> tuple[dict, list[str]]:
    """Collect submitted values and check them, so users see readable errors."""
    def text(name):
        return " ".join(form.get(name, [""])[0].split())

    def chosen(name, options):
        return [option for option in options if option in form.get(name, [])]

    values = {
        "organisation": text("organisation"),
        "positions": chosen("positions", schema["positions"]),
        "positionOther": text("positionOther"),
        "scilifelabAffiliation": next(iter(chosen("scilifelabAffiliation", schema["scilifelabAffiliations"])), ""),
        "scilifelabAffiliationOther": text("scilifelabAffiliationOther"),
        "plannedUses": chosen("plannedUses", schema["plannedUses"]),
        "plannedUseOther": text("plannedUseOther"),
        "addToSlack": {"yes": True, "no": False}.get(text("addToSlack")),
    }
    errors = []
    if not values["organisation"]:
        errors.append("Enter your university or organisation.")
    if not values["positions"]:
        errors.append("Select at least one position.")
    if not values["scilifelabAffiliation"]:
        errors.append("Select how you are affiliated to SciLifeLab.")
    if not values["plannedUses"]:
        errors.append("Select at least one planned use.")
    if values["addToSlack"] is None:
        errors.append("Answer whether you would like to join the Slack channel.")
    for choice, other, label in (("positions", "positionOther", "position"),
                                 ("scilifelabAffiliation", "scilifelabAffiliationOther", "SciLifeLab affiliation"),
                                 ("plannedUses", "plannedUseOther", "planned use")):
        selected = OTHER in values[choice] if isinstance(values[choice], list) else values[choice] == OTHER
        if not selected:
            values[other] = ""
        elif not values[other]:
            errors.append(f"Describe your other {label}.")
    if len(values["organisation"]) > 200:
        errors.append("The organisation name must be at most 200 characters.")
    if any(len(values[key]) > 2000 for key in ("positionOther", "scilifelabAffiliationOther", "plannedUseOther")):
        errors.append("Free-text answers must be at most 2000 characters.")
    return {key: value for key, value in values.items() if value not in ("", None)}, errors


def render_form(schema: dict, values: dict, errors: list[str], email: str, challenge: dict | None,
                widget_url: str) -> str:
    e = html.escape
    questions = schema["questions"]

    def question(key, default):
        return e(str(questions.get(key) or default))

    def options(name, items, kind):
        chosen = values.get(name)
        chosen = chosen if isinstance(chosen, list) else [chosen]
        return "".join(
            f'<label class="option"><input type="{kind}" name="{name}" value="{e(item, quote=True)}"'
            f'{" checked" if item in chosen else ""}{" required" if kind == "radio" else ""}> {e(item)}</label>'
            for item in items)

    def other(name, label):
        return (f'<label class="other">{e(label)} <input type="text" name="{name}" maxlength="2000" '
                f'value="{e(values.get(name, ""), quote=True)}"></label>')

    slack = values.get("addToSlack")
    error_list = "".join(f"<li>{e(error)}</li>" for error in errors)
    altcha = ""
    if challenge:
        altcha = (f'<altcha-widget auto="onsubmit" name="altcha" '
                  f'challenge="{e(json.dumps(challenge, separators=(",", ":")), quote=True)}"></altcha-widget>')
    return f"""<p>Your account is waiting for administrator approval. Your answers help us review it.</p>
{f'<div class="errors" role="alert"><ul>{error_list}</ul></div>' if errors else ""}
<form method="post" action="{PAGE}">
<label class="question" for="organisation">{question("organisation", "Which university or organisation are you affiliated to?")}</label>
<input id="organisation" type="text" name="organisation" maxlength="200" required autocomplete="organization"
 value="{e(values.get("organisation", ""), quote=True)}">
<fieldset><legend class="question">{question("positions", "Which position(s) do you hold?")}</legend>
<p class="hint">Select all that apply.</p>
{options("positions", schema["positions"], "checkbox")}{other("positionOther", "If Other, which position?")}</fieldset>
<fieldset><legend class="question">{question("scilifelabAffiliation", "How are you affiliated to SciLifeLab?")}</legend>
{options("scilifelabAffiliation", schema["scilifelabAffiliations"], "radio")}{other("scilifelabAffiliationOther", "If Other, please describe:")}</fieldset>
<fieldset><legend class="question">{question("plannedUses", "What do you plan to use the OpenLLM service for?")}</legend>
<p class="hint">Select all that apply.</p>
{options("plannedUses", schema["plannedUses"], "checkbox")}{other("plannedUseOther", "If Other, what for?")}</fieldset>
<fieldset><legend class="question">{question("addToSlack", "Would you like to be added to the SciLifeLab Slack channel for users of the service?")}</legend>
<label class="option"><input type="radio" name="addToSlack" value="yes" required{" checked" if slack is True else ""}> Yes</label>
<label class="option"><input type="radio" name="addToSlack" value="no" required{" checked" if slack is False else ""}> No</label>
</fieldset>
<p class="hint">Your answers are linked to your account email address, {e(email)}, which is also used for a Slack invitation.</p>
{altcha}
<button type="submit">Submit</button>
</form>"""


def submitted_page(thanks: bool = False) -> HTMLResponse:
    return page("Thank you" if thanks else "Questionnaire submitted",
                "<p>Your answers have been sent to the administrators, who will use them to review your account.</p>"
                "<p>Account review may take several working days.</p>")


def unavailable_page() -> HTMLResponse:
    return page("Questionnaire unavailable", "<p>The questionnaire is temporarily unavailable. "
                "Please try again later.</p>", status_code=503)


def page(title: str, content: str, *, status_code: int = 200, script: str | None = None) -> HTMLResponse:
    """Render an Open WebUI-style page without external resources or referrers."""
    widget = f'<script type="module" src="{html.escape(script, quote=True)}"></script>' if script else ""
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>{html.escape(title)}</title>{widget}
<style>
:root{{color-scheme:light;--background:#fff;--foreground:#000;--muted:oklch(.51 0 0);--border:oklch(.42 0 0 / 20%);
--button:oklch(.42 0 0 / 5%);--button-hover:oklch(.42 0 0 / 10%);--error:#b91c1c}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--background:#000;--foreground:oklch(.94 0 0);
--muted:oklch(.77 0 0);--border:oklch(.94 0 0 / 20%);--button:oklch(.94 0 0 / 5%);--button-hover:oklch(.94 0 0 / 10%);--error:#f87171}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--background);color:var(--foreground);font-size:.875rem;line-height:1.5;
font-family:-apple-system,BlinkMacSystemFont,Inter,ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif}}
main{{display:flex;flex-direction:column;justify-content:center;min-height:100dvh;padding:2.5rem 1.5rem}}
section{{width:100%;max-width:36rem;margin:auto;overflow-wrap:anywhere}}
h1{{margin:0 0 1rem;font-size:1.5rem;line-height:2rem;font-weight:400;text-align:center}}
p{{margin:.75rem 0 0;color:var(--muted)}}
fieldset{{margin:1.5rem 0 0;padding:0;border:0}}
.question{{display:block;margin-top:1.5rem;padding:0;font-weight:600;color:var(--foreground)}}
.hint{{margin:.25rem 0 .5rem}}
.option{{display:flex;gap:.5rem;align-items:center;padding:.25rem 0}}
.other{{display:block;margin-top:.5rem;color:var(--muted)}}
input[type=text]{{display:block;width:100%;margin-top:.5rem;padding:.5rem .75rem;border:1px solid var(--border);
border-radius:.75rem;background:transparent;color:inherit;font:inherit}}
altcha-widget{{display:block;margin-top:1.5rem}}
button{{display:block;width:100%;min-height:2.75rem;margin-top:1.5rem;padding:.625rem 1rem;border:1px solid transparent;
border-radius:9999px;background:var(--button);color:inherit;font:inherit;cursor:pointer}}
button:hover{{background:var(--button-hover)}}
.errors{{margin-top:1rem;padding:.5rem 1rem;border:1px solid var(--error);border-radius:.75rem;color:var(--error)}}
a{{color:inherit;text-underline-offset:.2em}}
:focus-visible{{outline:2px solid currentColor;outline-offset:3px}}
.back-link{{margin-top:1.5rem;text-align:center}}
</style></head><body><main><section aria-labelledby="title">
<h1 id="title">{html.escape(title)}</h1>
{content}
<p class="back-link"><a href="/">Back to SciLifeLab OpenLLM</a></p>
</section></main></body></html>"""
    return HTMLResponse(document, status_code=status_code, headers={
        "Cache-Control": "no-store",
        # Not no-referrer: browsers then send "Origin: null" on the form POST,
        # which the cross-site check rejects. same-origin still sends no
        # referrer to other sites.
        "Referrer-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": (
            "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "worker-src 'self' blob:; img-src 'self' data:; connect-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        ),
    })
