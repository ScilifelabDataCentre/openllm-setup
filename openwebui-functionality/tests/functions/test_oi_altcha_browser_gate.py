import asyncio
import base64
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import ValidationError
from redis.exceptions import ConnectionError
from starlette.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


class Store:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def set(self, key, value, ex):
        self.values[key] = value
        self.ttls[key] = ex

    async def get(self, key):
        return self.values.get(key)

    async def getdel(self, key):
        return self.values.pop(key, None)


@pytest.fixture
async def gate(monkeypatch, tmp_path):
    functions = SimpleNamespace(get_function_by_id=AsyncMock(return_value=SimpleNamespace(is_active=True)),
                                get_function_valves_by_id=AsyncMock(return_value={}))
    for name in ('open_webui', 'open_webui.models', 'open_webui.models.functions'):
        module = ModuleType(name)
        module.Functions = functions
        monkeypatch.setitem(sys.modules, name, module)
    for key, value in {'WHALE_AUTH_URL': 'http://whale.test', 'WHALE_AUTH_API_TOKEN': 'test-only',
                       'ALTCHA_REDIS_URL': 'redis://localhost', 'ALTCHA_PUBLIC_ORIGINS': 'https://webui.test'}.items():
        monkeypatch.setenv(key, value)
    path = Path(__file__).parents[2] / 'functions/oi_altcha_browser_gate.py'
    spec = importlib.util.spec_from_file_location('test_altcha_gate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store = Store()
    monkeypatch.setattr(module.Redis, 'from_url', lambda *a, **kw: store)
    fn = module.Event()
    app = FastAPI()

    class SPA(StaticFiles):
        async def get_response(self, path, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as error:
                if error.status_code == 404:
                    return await super().get_response('index.html', scope)
                raise
    (tmp_path / 'index.html').write_text('SPA')
    app.mount('/', SPA(directory=tmp_path, html=True))
    await fn.event({}, __id__='altcha_browser_gate', __app__=app)
    challenge = {'parameters': {'nonce': 'unique', 'algorithm': 'PBKDF2/SHA-256'}, 'signature': 'signed'}
    fn.upstream = AsyncMock(side_effect=lambda endpoint, payload=None: challenge if endpoint == 'challenge' else {'verified': True})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://webui.test',
                                 headers={'Origin': 'https://webui.test'}) as client:
        yield SimpleNamespace(fn=fn, app=app, client=client, module=module, store=store,
                              functions=functions, challenge=challenge)


def payload(challenge):
    return {'altchaPayload': base64.b64encode(json.dumps({'challenge': challenge, 'solution': {}}).encode()).decode()}


async def verified(gate):
    assert (await gate.client.post(gate.module.PREFIX + '/challenge')).status_code == 200
    return await gate.client.post(gate.module.PREFIX + '/verify', json=payload(gate.challenge))


async def test_clearance_cookie_expiry_and_cross_replica(gate):
    assert (await gate.client.post(gate.module.PREFIX + '/check')).status_code == 401
    response = await verified(gate)
    assert response.json() == {'verified': True, 'expiresIn': 3600}
    cookie = response.headers['set-cookie']
    assert all(flag in cookie for flag in ('Secure', 'HttpOnly', 'SameSite=strict', 'Path=/'))
    replica = gate.module.Event()
    replica._function_id = 'altcha_browser_gate'
    replica.origins = gate.fn.origins
    replica._redis = gate.store
    from starlette.requests import Request
    request = Request({'type': 'http', 'headers': [(b'cookie', ('%s=%s' % (gate.module.COOKIE, gate.client.cookies.get(gate.module.COOKIE))).encode())]})
    assert (await replica.check(request)).status_code == 204
    responses = await asyncio.gather(*(gate.client.post(gate.module.PREFIX + '/check') for _ in range(2)))
    assert [r.status_code for r in responses] == [204, 204]
    gate.store.values.clear()  # Redis expiration invalidates clearance everywhere.
    assert (await replica.check(request)).status_code == 401


async def test_replay_and_different_browser_rejected(gate):
    await gate.client.post(gate.module.PREFIX + '/challenge')
    original = gate.client.cookies.get(gate.module.COOKIE)
    gate.client.cookies.clear()
    assert (await gate.client.post(gate.module.PREFIX + '/verify', json=payload(gate.challenge))).status_code == 403
    gate.client.cookies.set(gate.module.COOKIE, original)
    assert (await gate.client.post(gate.module.PREFIX + '/verify', json=payload(gate.challenge))).status_code == 200
    gate.client.cookies.clear()
    gate.client.cookies.set(gate.module.COOKIE, original)
    assert (await gate.client.post(gate.module.PREFIX + '/verify', json=payload(gate.challenge))).status_code == 403


@pytest.mark.parametrize('body', [{}, [], {'altchaPayload': 42}, {'altchaPayload': '!'}, {'altchaPayload': 'bnVsbA=='}])
async def test_bad_payload(gate, body):
    assert (await gate.client.post(gate.module.PREFIX + '/verify', json=body)).status_code == 400


async def test_payload_limit(gate):
    assert (await gate.client.post(gate.module.PREFIX + '/verify', content='x' * 16385)).status_code == 413


async def test_wrong_challenge_and_expired_session(gate):
    await gate.client.post(gate.module.PREFIX + '/challenge')
    assert (await gate.client.post(gate.module.PREFIX + '/verify', json=payload({'signature': 'different'}))).status_code == 403
    await gate.client.post(gate.module.PREFIX + '/challenge')
    gate.store.values.clear()  # Redis expiration.
    assert (await gate.client.post(gate.module.PREFIX + '/verify', json=payload(gate.challenge))).status_code == 403


@pytest.mark.parametrize('result', [{'verified': False}, {'verified': 'true'}, {'verified': True, 'expired': True}, {'verified': True, 'invalidSignature': True}])
async def test_upstream_rejection(gate, result):
    await gate.client.post(gate.module.PREFIX + '/challenge')
    gate.fn.upstream.side_effect = None
    gate.fn.upstream.return_value = result
    assert (await gate.client.post(gate.module.PREFIX + '/verify', json=payload(gate.challenge))).status_code == 403
    assert (await gate.client.post(gate.module.PREFIX + '/check')).status_code == 401


async def test_origin_disabled_and_redis_failure(gate):
    assert (await gate.client.post(gate.module.PREFIX + '/challenge', headers={'Origin': 'https://other.test'})).status_code == 403
    gate.functions.get_function_by_id.return_value.is_active = False
    assert (await gate.client.post(gate.module.PREFIX + '/challenge')).status_code == 503
    gate.functions.get_function_by_id.return_value.is_active = True
    gate.store.set = AsyncMock(side_effect=ConnectionError())
    assert (await gate.client.post(gate.module.PREFIX + '/challenge')).status_code == 503


async def test_saved_valves_and_missing_settings(gate, monkeypatch):
    gate.functions.get_function_valves_by_id.return_value = {'clearance_ttl': 60}
    assert (await verified(gate)).json()['expiresIn'] == 60
    assert 60 in gate.store.ttls.values()
    with pytest.raises(ValidationError):
        gate.module.Event.Valves(clearance_ttl=0)
    monkeypatch.delenv('WHALE_AUTH_API_TOKEN')
    app = FastAPI()
    await gate.module.Event().event({}, __app__=app)
    assert not hasattr(app.state, 'altcha_browser_guard')
    assert (await gate.client.post(gate.module.PREFIX + '/check')).status_code == 503


async def test_route_reload_and_upstream_auth(gate, monkeypatch):
    other = gate.module.Event()
    await other.event({}, __id__='altcha_browser_gate', __app__=gate.app)
    assert len([r for r in gate.app.routes if (r.name or '').startswith('altcha_browser_')]) == 4
    real_client = httpx.AsyncClient
    def transport(request):
        assert request.headers['authorization'] == 'Bearer test-only'
        assert str(request.url) == 'http://whale.test/api/v1/altcha/verify'
        return httpx.Response(200, json={'verified': True})
    monkeypatch.setattr(gate.module.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(transport), **kw))
    assert await other.upstream('verify', {'altchaPayload': 'test'}) == {'verified': True}


@pytest.mark.parametrize('failure', ['timeout', 'http', 'json'])
async def test_upstream_failure_is_closed(gate, monkeypatch, failure):
    real_client = httpx.AsyncClient
    def transport(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('test', request=request)
        if failure == 'http':
            return httpx.Response(401)
        return httpx.Response(200, text='not JSON')
    monkeypatch.setattr(gate.module.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(transport), **kw))
    with pytest.raises(gate.module.HTTPException) as error:
        await gate.module.Event.upstream(gate.fn, 'challenge')
    assert error.value.status_code == 503


async def test_unsolved_challenge_is_not_a_signup_pass(gate):
    await gate.client.post(gate.module.PREFIX + '/challenge')
    assert (await gate.client.post(gate.module.PREFIX + '/check')).status_code == 401


async def test_interstitial_no_cache_and_script_policy(gate):
    response = await gate.client.get('/auth/verify-human')
    assert response.status_code == 200
    assert 'Checking your browser' in response.text
    assert '/auth/verify-human/altcha-3.5.0.min.js' in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    assert '__NONCE__' not in response.text
    assert 'test-only' not in response.text


async def test_check_is_post_and_not_spa_get_fallback(gate):
    assert (await gate.client.post(gate.module.PREFIX + '/check')).status_code == 401
    # The real SPA returns 405 for POST when a Function isn't registered.
    gate.app.router.routes[:] = [r for r in gate.app.routes if (r.name or '') != 'altcha_browser_check']
    assert (await gate.client.get(gate.module.PREFIX + '/check')).text == 'SPA'
    assert (await gate.client.post(gate.module.PREFIX + '/check')).status_code == 405
