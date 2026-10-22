import base64
import importlib.util
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError


@pytest.fixture
def approval(monkeypatch, request):
    """Load the standalone Function with only Open WebUI's database APIs mocked."""
    for name in (
        "ACCOUNT_APPROVAL_SEND",
        "ACCOUNT_APPROVAL_SENDER_NAME",
        "ACCOUNT_APPROVAL_SUBJECT",
        "ACCOUNT_APPROVAL_LOGIN_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GMAIL_CREDENTIALS_JSON", "{}")
    monkeypatch.setenv("GMAIL_DELEGATED_USER", "delegate@example.org")
    monkeypatch.setenv("GMAIL_SENDER_EMAIL", "sender@example.org")
    for name, value in getattr(request, "param", {}).items():
        monkeypatch.setenv(name, value)

    for name in ("open_webui", "open_webui.models"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    for name, symbol in (("config", "Config"), ("users", "Users")):
        module_name = f"open_webui.models.{name}"
        dependency = ModuleType(module_name)
        setattr(dependency, symbol, MagicMock())
        monkeypatch.setitem(sys.modules, module_name, dependency)

    path = Path(__file__).parents[2] / "functions" / "oi_account_approval_notification.py"
    spec = importlib.util.spec_from_file_location("approval_under_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.Config.get = AsyncMock(return_value="https://example.org")
    module.Users.update_user_by_id = AsyncMock()
    module.Users.get_users = AsyncMock(return_value={"users": [], "total": 0})
    return module


def make_user(role, marked=False, email="user@example.org", info=None):
    info = dict(info or {})
    if marked:
        info["scilifelab_awaiting_approval"] = True
    return SimpleNamespace(id="user-1", email=email, name="Ada", role=role, info=info)


def make_event(approval, user):
    event = approval.Event()
    event.send_approval_email = MagicMock()
    approval.Users.get_user_by_id = AsyncMock(return_value=user)
    return event


def saved_info(approval):
    user_id, update = approval.Users.update_user_by_id.call_args.args
    assert user_id == "user-1"
    return update["info"]


ROLE_UPDATED = ("user.role_updated", {"subject": {"id": "user-1"}, "data": {"role": "user"}})
SCIM_UPDATED = ("user.updated", {"subject": {"id": "user-1"}, "data": {"updated_fields": ["role"]}})


def test_defaults(approval):
    valves = approval.Event().valves
    assert valves.send_on_approval is True
    assert valves.sender_name == "SciLifeLab OpenLLM"
    assert valves.email_subject == "SciLifeLab OpenLLM: Your account has been approved"
    assert valves.login_url == ""


@pytest.mark.parametrize("approval", [{
    "ACCOUNT_APPROVAL_SEND": "false",
    "ACCOUNT_APPROVAL_SENDER_NAME": "Deployment name",
    "ACCOUNT_APPROVAL_SUBJECT": "Deployment subject",
    "ACCOUNT_APPROVAL_LOGIN_URL": " https://llm.example.org/ ",
}], indirect=True)
def test_environment_defaults_and_saved_overrides(approval):
    event = approval.Event()
    assert event.valves.send_on_approval is False
    assert event.valves.sender_name == "Deployment name"
    assert event.valves.email_subject == "Deployment subject"
    assert event.valves.login_url == "https://llm.example.org/"
    event.valves = event.Valves(send_on_approval=True, login_url="")
    assert event.valves.send_on_approval is True
    assert event.valves.login_url == ""


@pytest.mark.parametrize("field,value", [
    ("sender_name", " "),
    ("email_subject", "Hello\r\nBcc: other@example.org"),
    ("login_url", "http://example.org"),
    ("login_url", "example.org"),
])
def test_invalid_valves_rejected(approval, field, value):
    with pytest.raises(ValidationError):
        approval.Event.Valves(**{field: value})


@pytest.mark.parametrize("approval", [{"ACCOUNT_APPROVAL_LOGIN_URL": "ftp://x"}], indirect=True)
def test_environment_defaults_are_validated(approval):
    with pytest.raises(ValidationError):
        approval.Event()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,payload", [ROLE_UPDATED, SCIM_UPDATED])
async def test_marked_pending_user_approved_receives_email(approval, name, payload):
    event = make_event(approval, make_user("user", marked=True, info={"other": 1}))
    await event.event(payload, __event_name__=name)
    event.send_approval_email.assert_called_once_with(
        "user@example.org", "Ada", "https://example.org"
    )
    # The marker is cleared, preserving unrelated info.
    assert saved_info(approval) == {"other": 1}


@pytest.mark.asyncio
async def test_repeated_event_sends_once(approval):
    user = make_user("user", marked=True)
    event = make_event(approval, user)
    await event.event(ROLE_UPDATED[1], __event_name__=ROLE_UPDATED[0])
    await event.event(ROLE_UPDATED[1], __event_name__=ROLE_UPDATED[0])
    assert event.send_approval_email.call_count == 1


@pytest.mark.asyncio
async def test_unmarked_user_role_change_sends_nothing(approval):
    # E.g. an admin demoted to user: never pending as far as we know.
    event = make_event(approval, make_user("user"))
    await event.event(ROLE_UPDATED[1], __event_name__=ROLE_UPDATED[0])
    event.send_approval_email.assert_not_called()
    approval.Users.update_user_by_id.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["user.created", "auth.signup", "user.role_updated"])
async def test_pending_user_is_marked(approval, name):
    event = make_event(approval, make_user("pending"))
    await event.event({"subject": {"id": "user-1"}}, __event_name__=name)
    assert saved_info(approval) == {"scilifelab_awaiting_approval": True}
    event.send_approval_email.assert_not_called()


@pytest.mark.asyncio
async def test_signup_then_approval_flow(approval):
    user = make_user("pending")
    event = make_event(approval, user)
    await event.event({"subject": {"id": "user-1"}}, __event_name__="user.created")
    user.role = "user"
    await event.event(ROLE_UPDATED[1], __event_name__=ROLE_UPDATED[0])
    event.send_approval_email.assert_called_once()
    assert user.info == {}


@pytest.mark.asyncio
async def test_pending_to_admin_clears_marker_without_email(approval):
    event = make_event(approval, make_user("admin", marked=True))
    await event.event(ROLE_UPDATED[1], __event_name__=ROLE_UPDATED[0])
    event.send_approval_email.assert_not_called()
    assert saved_info(approval) == {}


@pytest.mark.asyncio
async def test_paused_sending_clears_marker_without_email(approval):
    event = make_event(approval, make_user("user", marked=True))
    event.valves = event.Valves(send_on_approval=False)
    await event.event(ROLE_UPDATED[1], __event_name__=ROLE_UPDATED[0])
    event.send_approval_email.assert_not_called()
    assert saved_info(approval) == {}


@pytest.mark.asyncio
async def test_unrelated_user_update_ignored(approval):
    event = make_event(approval, make_user("user", marked=True))
    await event.event(
        {"subject": {"id": "user-1"}, "data": {"updated_fields": ["timezone"]}},
        __event_name__="user.updated",
    )
    approval.Users.get_user_by_id.assert_not_called()
    event.send_approval_email.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [
    "GMAIL_CREDENTIALS_JSON", "GMAIL_DELEGATED_USER", "GMAIL_SENDER_EMAIL",
])
async def test_missing_settings_keep_function_inactive(approval, monkeypatch, caplog, setting):
    monkeypatch.setenv(setting, " ")
    event = make_event(approval, make_user("user", marked=True))
    await event.event(ROLE_UPDATED[1], __event_name__=ROLE_UPDATED[0], __app__=object())
    await event.event(ROLE_UPDATED[1], __event_name__=ROLE_UPDATED[0])
    event.send_approval_email.assert_not_called()
    approval.Users.get_user_by_id.assert_not_called()
    approval.Users.get_users.assert_not_called()
    warnings = [r for r in caplog.records if "inactive" in r.getMessage()]
    assert len(warnings) == 1 and setting in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_backfill_marks_existing_pending_users_once_per_app(approval):
    unmarked = make_user("pending")
    marked = SimpleNamespace(**{**vars(make_user("pending", marked=True)), "id": "user-2"})
    approval.Users.get_users = AsyncMock(return_value={"users": [unmarked, marked], "total": 2})
    event = make_event(approval, None)
    app = object()
    await event.event({}, __event_name__="system.startup.completed", __app__=app)
    await event.event({}, __event_name__="system.startup.completed", __app__=app)
    approval.Users.get_users.assert_awaited_once_with(filter={"roles": ["pending"]})
    approval.Users.update_user_by_id.assert_awaited_once_with(
        "user-1", {"info": {"scilifelab_awaiting_approval": True}}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("valve,config,expected", [
    ("https://llm.example.org", "https://ignored.example.org", "https://llm.example.org"),
    ("", "https://example.org", "https://example.org"),
    ("", "http://localhost:8080", ""),
    ("", None, ""),
])
async def test_login_link(approval, valve, config, expected):
    approval.Config.get = AsyncMock(return_value=config)
    event = approval.Event()
    event.valves = event.Valves(login_url=valve)
    assert await event.login_link() == expected


def sent_message(approval, monkeypatch, link):
    session = MagicMock()
    session.__enter__.return_value = session
    monkeypatch.setattr(approval, "AuthorizedSession", MagicMock(return_value=session))
    monkeypatch.setattr(
        approval.service_account.Credentials, "from_service_account_info", MagicMock()
    )
    approval.Event().send_approval_email("user@example.org", "Ada", link)
    raw = session.post.call_args.kwargs["json"]["raw"]
    return BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))


def test_email_content(approval, monkeypatch):
    message = sent_message(approval, monkeypatch, "https://example.org")
    assert message["From"] == "SciLifeLab OpenLLM <sender@example.org>"
    assert message["To"] == "user@example.org"
    assert message["Subject"] == "SciLifeLab OpenLLM: Your account has been approved"
    body = message.get_content()
    assert "Hello Ada" in body
    assert "approved" in body
    assert "https://example.org" in body


def test_email_without_link(approval, monkeypatch):
    body = sent_message(approval, monkeypatch, "").get_content()
    assert "You can now sign in." in body
    assert "https://" not in body
