import base64
import importlib.util
import sys
import time
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError


@pytest.fixture
def callback(monkeypatch, request):
    """Load the standalone Function with only Open WebUI's database APIs mocked."""
    for name in (
        "EMAIL_VERIFICATION_TOKEN_TTL",
        "EMAIL_VERIFICATION_SENDER_NAME",
        "EMAIL_VERIFICATION_SUBJECT",
        "EMAIL_VERIFICATION_SEND_ON_SIGNUP",
        "EMAIL_VERIFICATION_RESEND_COOLDOWN",
        "EMAIL_VERIFICATION_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMAIL_VERIFICATION_URL", "https://example.org/auth/verify-email")
    monkeypatch.setenv("GMAIL_CREDENTIALS_JSON", "{}")
    monkeypatch.setenv("GMAIL_DELEGATED_USER", "delegate@example.org")
    monkeypatch.setenv("GMAIL_SENDER_EMAIL", "sender@example.org")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "test-only-signing-key")
    for name, value in getattr(request, "param", {}).items():
        monkeypatch.setenv(name, value)

    for name in ("open_webui", "open_webui.models", "open_webui.utils"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    auth = ModuleType("open_webui.utils.auth")
    auth.get_current_user = AsyncMock(side_effect=HTTPException(401, "Not authenticated"))
    monkeypatch.setitem(sys.modules, auth.__name__, auth)
    for name, symbol in (("functions", "Functions"), ("groups", "Groups"), ("users", "Users")):
        module_name = f"open_webui.models.{name}"
        dependency = ModuleType(module_name)
        setattr(dependency, symbol, MagicMock())
        monkeypatch.setitem(sys.modules, module_name, dependency)

    path = Path(__file__).parents[2] / "functions" / "oi_email_verification_callback.py"
    spec = importlib.util.spec_from_file_location("verification_under_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_defaults(callback):
    event = callback.Event()
    assert event.ttl == 86400
    assert event.valves.sender_name == "SciLifeLab OpenLLM"
    assert event.valves.email_subject == "SciLifeLab OpenLLM: Verify your email address"
    assert event.valves.send_on_signup is True
    assert event.valves.resend_cooldown == "5m"


@pytest.mark.parametrize("callback", [{
    "EMAIL_VERIFICATION_TOKEN_TTL": " 2H ",
    "EMAIL_VERIFICATION_SENDER_NAME": "Deployment name",
    "EMAIL_VERIFICATION_SUBJECT": "Deployment subject",
    "EMAIL_VERIFICATION_SEND_ON_SIGNUP": "false",
}], indirect=True)
def test_environment_defaults_and_saved_overrides(callback):
    event = callback.Event()
    assert event.ttl == 7200
    assert event.valves.sender_name == "Deployment name"
    assert event.valves.email_subject == "Deployment subject"
    assert event.valves.send_on_signup is False
    event.valves = event.Valves(
        token_ttl="30m", sender_name="Saved name", email_subject="Saved subject",
        send_on_signup=True,
    )
    assert event.ttl == 1800
    assert event.valves.sender_name == "Saved name"
    assert event.valves.email_subject == "Saved subject"
    assert event.valves.send_on_signup is True


@pytest.mark.parametrize("duration,seconds", [
    ("3600", 3600), ("30s", 30), ("15m", 900), (" 2H ", 7200), ("7d", 604800),
])
def test_duration_formats(callback, duration, seconds):
    event = callback.Event()
    event.valves = event.Valves(token_ttl=duration)
    assert event.ttl == seconds


@pytest.mark.parametrize("field", ["token_ttl", "resend_cooldown"])
@pytest.mark.parametrize("duration", ["0", "0h", "-1", "1.5h", "1w", "", "forever"])
def test_invalid_durations_rejected(callback, field, duration):
    with pytest.raises(ValidationError):
        callback.Event.Valves(**{field: duration})


@pytest.mark.parametrize("callback", [{"EMAIL_VERIFICATION_TOKEN_TTL": "0h"}], indirect=True)
def test_environment_defaults_are_validated(callback):
    with pytest.raises(ValidationError):
        callback.Event()


@pytest.mark.parametrize("field", ["sender_name", "email_subject"])
@pytest.mark.parametrize("value", [" ", "Hello\r\nBcc: other@example.org", "Hello\x00"])
def test_invalid_email_headers_rejected(callback, field, value):
    with pytest.raises(ValidationError):
        callback.Event.Valves(**{field: value})


@pytest.mark.asyncio
async def test_enabled_signup_sends_verification_link(callback):
    event = callback.Event()
    event.send_verification_email = MagicMock()
    callback.Groups.get_group_by_name = AsyncMock(return_value=SimpleNamespace(id="verified"))
    callback.Users.get_user_by_id = AsyncMock(return_value=SimpleNamespace(
        id="user-1", email="user@example.org", role="pending",
    ))
    await event.event({"subject": {"id": "user-1"}}, __event_name__="auth.signup")
    recipient, link = event.send_verification_email.call_args.args
    assert recipient == "user@example.org"
    assert link.startswith("https://example.org/auth/verify-email?")
    token = parse_qs(urlsplit(link).query)["token"][0]
    assert event.decode_token(token) == {"user_id": "user-1", "email": recipient}


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [
    "EMAIL_VERIFICATION_URL", "GMAIL_CREDENTIALS_JSON",
    "GMAIL_DELEGATED_USER", "GMAIL_SENDER_EMAIL",
])
async def test_missing_settings_keep_function_inactive(callback, monkeypatch, caplog, setting):
    monkeypatch.setenv(setting, " ")
    event = callback.Event()
    event.send_verification_email = MagicMock()
    callback.Groups.remove_users_from_group = AsyncMock()
    app = FastAPI()
    await event.event(
        {"subject": {"id": "user-1"}}, __event_name__="auth.signup",
        __id__="verification", __app__=app,
    )
    await event.event(
        {"subject": {"id": "user-1"}, "data": {"updated_fields": ["email"]}},
        __event_name__="user.updated",
    )
    assert not callback.OWN_ROUTE_NAMES & {route.name for route in app.routes}
    event.send_verification_email.assert_not_called()
    callback.Groups.remove_users_from_group.assert_not_awaited()
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1 and setting in warnings[0]

    # Supplying the setting activates the already-loaded Function.
    monkeypatch.setenv(setting, "https://example.org/auth/verify-email")
    await event.event({}, __app__=app)
    assert callback.OWN_ROUTE_NAMES <= {route.name for route in app.routes}


@pytest.mark.asyncio
async def test_paused_signup_keeps_routes_and_revocation(callback):
    event = callback.Event()
    event.valves = event.Valves(send_on_signup=False)
    event.send_verification_email = MagicMock()
    app = FastAPI()
    await event.event(
        {"subject": {"id": "user-1"}}, __event_name__="auth.signup",
        __id__="verification", __app__=app,
    )
    event.send_verification_email.assert_not_called()
    callback.Users.get_user_by_id.assert_not_called()
    assert callback.OWN_ROUTE_NAMES <= {route.name for route in app.routes}

    callback.Groups.get_group_by_name = AsyncMock(return_value=SimpleNamespace(id="verified"))
    callback.Groups.remove_users_from_group = AsyncMock()
    await event.event(
        {"subject": {"id": "user-1"}, "data": {"updated_fields": ["email"]}},
        __event_name__="user.updated",
    )
    callback.Groups.remove_users_from_group.assert_awaited_once_with("verified", ["user-1"])


@pytest.mark.asyncio
async def test_existing_routes_reload_ttl_and_verify_while_sending_paused(callback, monkeypatch):
    event = callback.Event()
    app = FastAPI()
    await event.event({}, __id__="verification", __app__=app)
    get = next(route.endpoint for route in app.routes if route.name == callback.GET_ROUTE_NAME)
    post = next(route.endpoint for route in app.routes if route.name == callback.POST_ROUTE_NAME)
    token = event.serializer.dumps({"user_id": "user-1", "email": "user@example.org"})
    now = time.time()
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: now + 7200)
    saved = {"token_ttl": "1h", "send_on_signup": False}
    callback.Functions.get_function_valves_by_id = AsyncMock(return_value=saved)
    request = SimpleNamespace(
        headers={}, body=AsyncMock(return_value=urlencode({"token": token}).encode()),
    )
    for endpoint, argument in ((get, token), (post, request)):
        response = await endpoint(argument)
        assert response.status_code == 400
        assert b"Verification link expired" in response.body
        assert b'action="/auth/verify-email/resend"' in response.body

    saved["token_ttl"] = "3h"
    callback.Users.get_user_by_id = AsyncMock(return_value=SimpleNamespace(
        id="user-1", email="user@example.org", role="pending",
    ))
    callback.Groups.get_group_by_name = AsyncMock(return_value=SimpleNamespace(id="verified"))
    callback.Groups.get_groups_by_member_id = AsyncMock(return_value=[])
    callback.Groups.add_users_to_group = AsyncMock(return_value=True)
    assert (await get(token)).status_code == 200
    callback.Groups.add_users_to_group.assert_not_awaited()
    assert (await post(request)).status_code == 200
    callback.Groups.add_users_to_group.assert_awaited_once_with("verified", ["user-1"])
    assert event.valves.send_on_signup is False
    assert callback.Functions.get_function_valves_by_id.await_count == 4


async def confirmation_endpoints(callback, event):
    app = FastAPI()
    await event.event({}, __id__="verification", __app__=app)
    routes = {route.name: route for route in app.routes}
    callback.Functions.get_function_valves_by_id = AsyncMock(return_value={})
    return routes[callback.GET_ROUTE_NAME].endpoint, routes[callback.POST_ROUTE_NAME].endpoint


def form_post(token):
    return SimpleNamespace(
        headers={}, body=AsyncMock(return_value=urlencode({"token": token}).encode()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("token,message", [
    ("", b"Verification token is missing or invalid"),
    ("tampered.token", b"Invalid verification link"),
])
async def test_bad_links_render_page_offering_resend(callback, token, message):
    event = callback.Event()
    get, post = await confirmation_endpoints(callback, event)
    callback.Groups.add_users_to_group = AsyncMock()
    for response in (await get(token), await post(form_post(token))):
        assert response.status_code == 400
        assert response.headers["content-type"].startswith("text/html")
        assert message in response.body
        assert b'method="get" action="/auth/verify-email/resend"' in response.body
    callback.Groups.add_users_to_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_email_offers_resend_but_outages_do_not(callback):
    event = callback.Event()
    _, post = await confirmation_endpoints(callback, event)
    token = event.serializer.dumps({"user_id": "user-1", "email": "old@example.org"})
    callback.Users.get_user_by_id = AsyncMock(return_value=PENDING)
    response = await post(form_post(token))
    assert response.status_code == 400
    assert b"Account email has changed" in response.body
    assert b"/auth/verify-email/resend" in response.body

    token = event.serializer.dumps({"user_id": "user-1", "email": "user@example.org"})
    callback.Groups.get_group_by_name = AsyncMock(return_value=None)
    response = await post(form_post(token))
    assert response.status_code == 503
    assert b"temporarily unavailable" in response.body
    assert b"<form" not in response.body


def test_email_uses_valve_headers_and_deployment_mailbox(callback, monkeypatch):
    monkeypatch.setenv("GMAIL_CREDENTIALS_JSON", "{}")
    monkeypatch.setenv("GMAIL_DELEGATED_USER", "delegate@example.org")
    monkeypatch.setenv("GMAIL_SENDER_EMAIL", "sender@example.org")
    monkeypatch.setattr(callback.service_account, "Credentials", MagicMock())
    session_factory = MagicMock()
    monkeypatch.setattr(callback, "AuthorizedSession", session_factory)
    event = callback.Event()
    event.valves = event.Valves(sender_name="Research team", email_subject="Confirm membership")
    event.send_verification_email("recipient@example.org", "https://example.org/verify")
    session = session_factory.return_value.__enter__.return_value
    raw = session.post.call_args.kwargs["json"]["raw"]
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
    assert message["From"].addresses[0].display_name == "Research team"
    assert message["From"].addresses[0].addr_spec == "sender@example.org"
    assert message["Subject"] == "Confirm membership"


PENDING = SimpleNamespace(id="user-1", email="user@example.org", role="pending")


async def resend_endpoints(callback, event):
    app = FastAPI()
    await event.event({}, __id__="verification", __app__=app)
    routes = {route.name: route for route in app.routes}
    get = routes[callback.RESEND_GET_ROUTE_NAME]
    post = routes[callback.RESEND_POST_ROUTE_NAME]
    assert get.path == post.path == "/auth/verify-email/resend"
    callback.Functions.get_function_valves_by_id = AsyncMock(return_value={})
    callback.Groups.get_group_by_name = AsyncMock(return_value=SimpleNamespace(id="verified"))
    callback.Groups.get_groups_by_member_id = AsyncMock(return_value=[])
    return get.endpoint, post.endpoint


def browser_request(**headers):
    return SimpleNamespace(headers={"origin": "https://example.org", **headers})


@pytest.mark.asyncio
async def test_resend_sends_new_link_and_throttles(callback, monkeypatch):
    event = callback.Event()
    event.send_verification_email = MagicMock()
    get, post = await resend_endpoints(callback, event)
    callback.get_current_user.side_effect = None
    callback.get_current_user.return_value = PENDING
    now = [1000.0]
    monkeypatch.setattr(callback.time, "monotonic", lambda: now[0])

    form = await get(browser_request())
    assert form.status_code == 200 and b'action="/auth/verify-email/resend"' in form.body
    event.send_verification_email.assert_not_called()

    response = await post(browser_request())
    assert response.status_code == 200
    recipient, link = event.send_verification_email.call_args.args
    assert recipient == "user@example.org"
    token = parse_qs(urlsplit(link).query)["token"][0]
    assert event.decode_token(token) == {"user_id": "user-1", "email": recipient}

    now[0] += 299
    assert (await post(browser_request())).status_code == 429
    now[0] += 2
    assert (await post(browser_request())).status_code == 200
    assert event.send_verification_email.call_count == 2


@pytest.mark.asyncio
async def test_signup_email_starts_resend_cooldown(callback):
    event = callback.Event()
    event.send_verification_email = MagicMock()
    _, post = await resend_endpoints(callback, event)
    callback.Users.get_user_by_id = AsyncMock(return_value=PENDING)
    await event.event({"subject": {"id": "user-1"}}, __event_name__="auth.signup")
    callback.get_current_user.side_effect = None
    callback.get_current_user.return_value = PENDING
    assert (await post(browser_request())).status_code == 429
    assert event.send_verification_email.call_count == 1


@pytest.mark.asyncio
async def test_failed_resend_does_not_start_cooldown(callback):
    event = callback.Event()
    event.send_verification_email = MagicMock(side_effect=RuntimeError("Gmail down"))
    _, post = await resend_endpoints(callback, event)
    callback.get_current_user.side_effect = None
    callback.get_current_user.return_value = PENDING
    assert (await post(browser_request())).status_code == 502
    event.send_verification_email.side_effect = None
    assert (await post(browser_request())).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("case,status", [
    ("signed_out", 401), ("active", 200), ("verified", 200),
    ("paused", 503), ("missing_group", 503),
])
async def test_resend_refused_without_sending(callback, case, status):
    event = callback.Event()
    event.send_verification_email = MagicMock()
    get, post = await resend_endpoints(callback, event)
    if case != "signed_out":
        callback.get_current_user.side_effect = None
        callback.get_current_user.return_value = PENDING
    if case == "active":
        callback.get_current_user.return_value = SimpleNamespace(
            id="user-1", email="user@example.org", role="user",
        )
    elif case == "verified":
        callback.Groups.get_groups_by_member_id = AsyncMock(
            return_value=[SimpleNamespace(id="verified")]
        )
    elif case == "paused":
        callback.Functions.get_function_valves_by_id = AsyncMock(
            return_value={"send_on_signup": False}
        )
    elif case == "missing_group":
        callback.Groups.get_group_by_name = AsyncMock(return_value=None)
    for endpoint in (get, post):
        response = await endpoint(browser_request())
        assert response.status_code == status
        assert b"<form" not in response.body
    event.send_verification_email.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    {"origin": "https://attacker.example"},
    {"sec-fetch-site": "cross-site"},
])
async def test_resend_rejects_cross_site_posts(callback, headers):
    event = callback.Event()
    event.send_verification_email = MagicMock()
    _, post = await resend_endpoints(callback, event)
    callback.get_current_user.side_effect = None
    callback.get_current_user.return_value = PENDING
    with pytest.raises(HTTPException) as error:
        await post(browser_request(**headers))
    assert error.value.status_code == 403
    event.send_verification_email.assert_not_called()
