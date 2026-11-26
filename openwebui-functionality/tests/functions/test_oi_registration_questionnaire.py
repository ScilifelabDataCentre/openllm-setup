import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from starlette.staticfiles import StaticFiles

SCHEMA = {
    'positions': ['PhD student', 'Postdoc', 'PI', 'Other'],
    'scilifelabAffiliations': ['Infrastructure unit staff', 'Affiliated research group member', 'Other'],
    'plannedUses': ['Software development', 'Research and data analysis', 'Other'],
    'questions': {'organisation': 'Which university or organisation are you affiliated to?'},
    'notes': {},
}
CHALLENGE = {'parameters': {'nonce': 'n'}, 'signature': 'signed'}
ANSWERS = [('organisation', ' Example  University '), ('positions', 'PhD student'), ('positions', 'Other'),
           ('positionOther', 'Lab manager'), ('scilifelabAffiliation', 'Affiliated research group member'),
           ('plannedUses', 'Software development'), ('addToSlack', 'yes'), ('altcha', 'solved-payload')]


def pending(**info):
    return SimpleNamespace(id='user-1', email='pending@example.org', role='pending', info=info)


@pytest.fixture
async def q(monkeypatch, tmp_path):
    functions = SimpleNamespace(get_function_by_id=AsyncMock(return_value=SimpleNamespace(is_active=True)),
                                get_function_valves_by_id=AsyncMock(return_value={}))
    users = SimpleNamespace(update_user_by_id=AsyncMock())
    auth = ModuleType('open_webui.utils.auth')
    auth.get_current_user = AsyncMock(return_value=pending())
    for name in ('open_webui', 'open_webui.models', 'open_webui.utils'):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    for name, symbol, value in (('functions', 'Functions', functions), ('users', 'Users', users)):
        module = ModuleType(f'open_webui.models.{name}')
        setattr(module, symbol, value)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(sys.modules, auth.__name__, auth)
    monkeypatch.setenv('WHALE_AUTH_URL', 'http://whale.test/')
    monkeypatch.setenv('WHALE_AUTH_API_TOKEN', 'test-only')
    path = Path(__file__).parents[2] / 'functions/oi_registration_questionnaire.py'
    spec = importlib.util.spec_from_file_location('test_registration_questionnaire', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    service = SimpleNamespace(requests=[], submit=lambda body: httpx.Response(
        201, json={'id': 'a5f3c3a4-0000-4000-8000-000000000001', 'submittedAt': '2026-11-13T12:00:00Z'}))

    def handler(request):
        assert request.headers['authorization'] == 'Bearer test-only'
        body = json.loads(request.content) if request.content else None
        service.requests.append((request.method, request.url.path, body))
        if request.url.path == '/api/v1/questionnaire/schema':
            return httpx.Response(200, json=SCHEMA)
        if request.url.path == '/api/v1/altcha/challenge':
            return httpx.Response(200, json=CHALLENGE)
        return service.submit(body)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient',
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    app = FastAPI()
    (tmp_path / 'index.html').write_text('SPA')
    app.mount('/', StaticFiles(directory=tmp_path, html=True))
    fn = module.Event()
    await fn.event({}, __id__='registration_questionnaire', __app__=app)
    async with real_client(transport=httpx.ASGITransport(app=app), base_url='https://webui.test') as client:
        yield SimpleNamespace(fn=fn, app=app, client=client, module=module, service=service,
                              functions=functions, users=users, auth=auth, real_client=real_client)


async def post(q, data, **headers):
    return await q.client.post(q.module.PAGE, content=urlencode(data),
                               headers={'Content-Type': 'application/x-www-form-urlencoded', **headers})


def submitted(q):
    return [body for method, path, body in q.service.requests if path.endswith('/submissions')]


async def test_form_renders_schema_and_altcha(q):
    response = await q.client.get(q.module.PAGE)
    assert response.status_code == 200
    assert 'Which university or organisation are you affiliated to?' in response.text
    assert all(option in response.text for option in SCHEMA['positions'] + SCHEMA['plannedUses'])
    assert '<altcha-widget auto="onsubmit" name="altcha"' in response.text
    assert '&quot;signature&quot;:&quot;signed&quot;' in response.text
    assert '<script type="module" src="/static/altcha/altcha-3.5.0.min.js">' in response.text
    assert 'pending@example.org' in response.text
    assert 'test-only' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']


async def test_routes_precede_spa_and_reload_once(q):
    names = [r.name for r in q.app.routes]
    assert names.index('registration_questionnaire_show') < len(names) - 1
    await q.module.Event().event({}, __id__='registration_questionnaire', __app__=q.app)
    assert sum((r.name or '').startswith('registration_questionnaire_') for r in q.app.routes) == 3


async def test_submit_stores_answers_and_marks_user(q):
    response = await post(q, ANSWERS)
    assert response.status_code == 200 and 'Thank you' in response.text
    assert submitted(q) == [{
        'organisation': 'Example University', 'positions': ['PhD student', 'Other'], 'positionOther': 'Lab manager',
        'scilifelabAffiliation': 'Affiliated research group member', 'plannedUses': ['Software development'],
        'addToSlack': True, 'contactEmail': 'pending@example.org', 'altchaPayload': 'solved-payload'}]
    q.users.update_user_by_id.assert_awaited_once_with('user-1', {'info': {'scilifelab_questionnaire': {
        'id': 'a5f3c3a4-0000-4000-8000-000000000001', 'submittedAt': '2026-11-13T12:00:00Z'}}})


async def test_unchecked_other_text_and_unknown_options_are_dropped(q):
    data = [item for item in ANSWERS if item != ('positions', 'Other')] + [('plannedUses', 'Not an option')]
    assert (await post(q, data + [('addToSlack', 'no')])).status_code == 200
    body = submitted(q)[0]
    assert 'positionOther' not in body and body['plannedUses'] == ['Software development']


@pytest.mark.parametrize('remove, message', [
    ('organisation', 'Enter your university or organisation.'),
    ('positionOther', 'Describe your other position.'),
    ('scilifelabAffiliation', 'Select how you are affiliated to SciLifeLab.'),
    ('plannedUses', 'Select at least one planned use.'),
    ('addToSlack', 'Answer whether you would like to join the Slack channel.'),
    ('altcha', 'Please complete the verification before submitting.'),
])
async def test_validation_rerenders_form_with_answers(q, remove, message):
    response = await post(q, [item for item in ANSWERS if item[0] != remove])
    assert response.status_code == 422
    assert message in response.text
    assert submitted(q) == []
    q.users.update_user_by_id.assert_not_called()
    if remove != 'organisation':
        assert 'value="Example University"' in response.text


async def test_service_rejection_keeps_answers(q):
    q.service.submit = lambda body: httpx.Response(422, json={'detail': 'ALTCHA verification failed.'})
    response = await post(q, ANSWERS)
    assert response.status_code == 422 and 'Verification expired or failed' in response.text
    assert 'value="Lab manager"' in response.text
    q.users.update_user_by_id.assert_not_called()


@pytest.mark.parametrize('result', [httpx.Response(500), httpx.Response(201, text='not JSON'),
                                    httpx.Response(201, json={})])
async def test_service_failure_is_unavailable(q, result):
    q.service.submit = lambda body: result
    assert (await post(q, ANSWERS)).status_code == 503
    q.users.update_user_by_id.assert_not_called()


async def test_service_unreachable(q, monkeypatch):
    def fail(request):
        raise httpx.ConnectError('down', request=request)
    monkeypatch.setattr(q.module.httpx, 'AsyncClient',
                        lambda **kw: q.real_client(transport=httpx.MockTransport(fail), **kw))
    assert (await q.client.get(q.module.PAGE)).status_code == 503


async def test_altcha_can_be_disabled(q):
    q.functions.get_function_valves_by_id.return_value = {'require_altcha': False}
    response = await q.client.get(q.module.PAGE)
    assert '<altcha-widget' not in response.text and '<script' not in response.text
    assert (await post(q, [item for item in ANSWERS if item[0] != 'altcha'])).status_code == 200
    assert 'altchaPayload' not in submitted(q)[0]
    assert all(path != '/api/v1/altcha/challenge' for _, path, _ in q.service.requests)


@pytest.mark.parametrize('user, status, text', [
    (None, 401, 'Sign in required'),
    (SimpleNamespace(id='u', email='a@example.org', role='user', info={}), 200, 'No questionnaire needed'),
    (pending(scilifelab_questionnaire={'id': 'x'}), 200, 'Questionnaire submitted'),
])
async def test_refusals(q, user, status, text):
    if user is None:
        q.auth.get_current_user.side_effect = HTTPException(401)
    else:
        q.auth.get_current_user.return_value = user
    for response in (await q.client.get(q.module.PAGE), await post(q, ANSWERS)):
        assert response.status_code == status and text in response.text
    assert submitted(q) == []


async def test_form_page_lets_browsers_send_origin(q):
    # With no-referrer, browsers send "Origin: null" on the form POST.
    assert (await q.client.get(q.module.PAGE)).headers['referrer-policy'] == 'same-origin'


async def test_cross_site_post_rejected(q):
    assert (await post(q, ANSWERS, Origin='null')).status_code == 403
    assert (await post(q, ANSWERS, Origin='https://evil.test')).status_code == 403
    assert (await post(q, ANSWERS, **{'Sec-Fetch-Site': 'cross-site'})).status_code == 403
    assert (await post(q, ANSWERS, Origin='https://webui.test', **{'Sec-Fetch-Site': 'same-origin'})).status_code == 200


async def test_oversized_form(q):
    assert (await post(q, [('organisation', 'x' * 40000)])).status_code == 413


async def test_disabled_function_and_missing_settings(q, monkeypatch, caplog):
    q.functions.get_function_by_id.return_value = SimpleNamespace(is_active=False)
    assert (await q.client.get(q.module.PAGE)).status_code == 503
    monkeypatch.delenv('WHALE_AUTH_API_TOKEN')
    app = FastAPI()
    fn = q.module.Event()
    await fn.event({}, __app__=app)
    await fn.event({}, __app__=app)
    assert not any((r.name or '').startswith('registration_questionnaire_') for r in app.routes)
    assert caplog.text.count('Registration questionnaire inactive') == 1


def test_valves(q):
    assert q.module.Event.Valves().require_altcha is True
    assert q.module.Event.Valves(altcha_widget_url='/static/altcha.js').altcha_widget_url == '/static/altcha.js'
    for url in ('https://cdn.example.org/altcha.js', '//cdn.example.org/altcha.js', '/a"onload="x'):
        with pytest.raises(ValidationError):
            q.module.Event.Valves(altcha_widget_url=url)


async def test_invalid_service_url(q, monkeypatch):
    monkeypatch.setenv('WHALE_AUTH_URL', 'ftp://whale.test')
    with pytest.raises(ValueError):
        await q.module.Event().event({}, __app__=FastAPI())


@pytest.mark.parametrize('user, required', [
    (pending(), True),
    (pending(scilifelab_questionnaire={'id': 'x'}), False),
    (SimpleNamespace(id='u', email='a@example.org', role='user', info={}), False),
    (SimpleNamespace(id='u', email='', role='pending', info={}), False),
])
async def test_status(q, user, required):
    q.auth.get_current_user.return_value = user
    response = await q.client.post(q.module.STATUS)
    assert response.status_code == 200 and response.json() == {'required': required}
    assert response.headers['cache-control'] == 'no-store'
    assert q.service.requests == []  # No service call is needed for the status.


async def test_status_signed_out_and_unavailable(q):
    q.auth.get_current_user.side_effect = HTTPException(401)
    assert (await q.client.post(q.module.STATUS)).status_code == 401
    q.functions.get_function_by_id.return_value = SimpleNamespace(is_active=False)
    response = await q.client.post(q.module.STATUS)
    assert response.status_code == 503 and response.json() == {'required': False}

