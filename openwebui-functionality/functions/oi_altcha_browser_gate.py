"""
title: SciLifeLab ALTCHA Browser Gate
oi_type: event
function_id: altcha_browser_gate
description: Serve a browser interstitial and validate clearance for an NGINX proxy.
author: SciLifeLab
version: 0.3.0
requirements: httpx==0.28.1, redis==8.0.1

Requires the optional openllm-charts ALTCHA proxy and HTTPS; no app source changes.
Deployment: WHALE_AUTH_URL, WHALE_AUTH_API_TOKEN, ALTCHA_REDIS_URL,
ALTCHA_PUBLIC_ORIGINS (comma-separated exact HTTPS origins).
Restart all replicas after installation. No HMAC secrets enter Open WebUI.
"""

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import secrets
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from open_webui.models.functions import Functions
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.routing import Mount

log = logging.getLogger(__name__)
PREFIX = "/api/v1/altcha-gate"
PAGE = "/auth/verify-human"
COOKIE = "__Host-altcha-clearance"
# Pending challenges use a separate cookie so a new challenge never replaces clearance.
PENDING = "__Host-altcha-pending"
KEY = "openwebui:altcha-gate:"
REQUIRED = ("WHALE_AUTH_URL", "WHALE_AUTH_API_TOKEN", "ALTCHA_REDIS_URL", "ALTCHA_PUBLIC_ORIGINS")


class Event:
    class Valves(BaseModel):
        clearance_ttl: int = Field(default=3600, ge=60, le=86400, description="Seconds the verified browser may access the website.")

    def __init__(self):
        self.valves = self.Valves()
        self._function_id = None
        self._app = None
        self._redis = None
        self._warned = False

    async def event(self, event: dict, __id__: str | None = None, __app__=None):
        self._function_id = __id__ or self._function_id
        missing = [key for key in REQUIRED if not os.getenv(key, "").strip()]
        if missing:
            if not self._warned:
                log.warning("ALTCHA browser gate inactive; missing: %s", ", ".join(missing))
                self._warned = True
            return
        if __app__ is not None:
            self.setup(__app__)

    def setup(self, app):
        if app is self._app:
            return
        self.origins = {origin.strip().rstrip("/") for origin in os.environ["ALTCHA_PUBLIC_ORIGINS"].split(",")}
        self.service = os.environ["WHALE_AUTH_URL"].rstrip("/")
        for url, public in [(origin, True) for origin in self.origins] + [(self.service, False)]:
            parsed = urlsplit(url)
            if (parsed.scheme not in ({"https"} if public else {"http", "https"})
                    or not parsed.netloc or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or (public and parsed.path)):
                raise ValueError("Invalid ALTCHA origin or service URL")
        self._redis = Redis.from_url(os.environ["ALTCHA_REDIS_URL"], decode_responses=True,
                                     socket_connect_timeout=5, socket_timeout=5)
        routes = app.router.routes
        routes[:] = [r for r in routes if not (getattr(r, "name", "") or "").startswith("altcha_browser_")]
        for path, handler, method in ((f"{PREFIX}/challenge", self.challenge, "POST"),
                                      (f"{PREFIX}/verify", self.verify, "POST"),
                                      (f"{PREFIX}/check", self.check, "POST"),
                                      (PAGE, self.page, "GET")):
            suffix = path.rsplit("/", 1)[-1]
            if any(getattr(r, "path", None) == path for r in routes):
                raise RuntimeError("ALTCHA route already exists")
            app.add_api_route(path, handler, methods=[method], name=f"altcha_browser_{suffix}", include_in_schema=False)
        added = [r for r in routes if (getattr(r, "name", "") or "").startswith("altcha_browser_")]
        for route in added:
            routes.remove(route)
        index = next((i for i, r in enumerate(routes) if isinstance(r, Mount) and r.path in ("", "/")), len(routes))
        routes[index:index] = added
        self._app = app

    async def ready(self, request: Request, check_origin=True):
        # Registered routes survive disabling a Function; explicitly fail closed.
        function = await Functions.get_function_by_id(self._function_id) if self._function_id else None
        if not function or not function.is_active or not all(os.getenv(key, "").strip() for key in REQUIRED):
            raise HTTPException(503, "Browser verification is unavailable")
        self.valves = self.Valves(**(await Functions.get_function_valves_by_id(self._function_id) or {}))
        if check_origin and (request.headers.get("origin") not in self.origins or request.headers.get("sec-fetch-site") == "cross-site"):
            raise HTTPException(403, "Same-origin browser verification is required")

    async def storage(self, method, *args, **kwargs):
        try:
            return await getattr(self._redis, method)(*args, **kwargs)
        except RedisError:
            raise HTTPException(503, "Browser verification is unavailable") from None

    async def upstream(self, endpoint, payload=None):
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                response = await client.post(f"{self.service}/api/v1/altcha/{endpoint}", json=payload,
                                             headers={"Authorization": f"Bearer {os.environ['WHALE_AUTH_API_TOKEN']}"})
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError()
                return data
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Browser verification is unavailable") from None

    @staticmethod
    def token(request, cookie):
        token = request.cookies.get(cookie, "")
        return token if re.fullmatch(r"[0-9a-f]{64}", token) else None

    @staticmethod
    def pending_key(browser, signature):
        # One key per issued challenge lets several tabs verify independently.
        return f"{KEY}pending:{browser}:{hashlib.sha256(signature.encode()).hexdigest()}"

    async def cleared(self, request):
        token = self.token(request, COOKIE)
        return bool(token) and await self.storage("get", KEY + token) == "verified"

    def response(self, data, cookie, token, ttl):
        response = JSONResponse(data, headers={"Cache-Control": "no-store"})
        # Lax keeps clearance on top-level navigations from other sites (email, Slack,
        # OAuth returns); cross-site POSTs to protected endpoints still omit it.
        samesite = "lax" if cookie == COOKIE else "strict"
        response.set_cookie(cookie, token, max_age=ttl, secure=True, httponly=True, samesite=samesite, path="/")
        return response

    async def challenge(self, request: Request):
        await self.ready(request)
        if await self.cleared(request):
            # Another tab already verified this browser; keep its clearance cookie.
            return JSONResponse({"verified": True}, headers={"Cache-Control": "no-store"})
        challenge = await self.upstream("challenge")
        if not isinstance(challenge.get("parameters"), dict) or not isinstance(challenge.get("signature"), str) \
                or not challenge["signature"]:
            raise HTTPException(503, "Unsupported ALTCHA challenge")
        browser = self.token(request, PENDING) or secrets.token_hex(32)
        # Match the exact challenge issued to this browser. Redis TTL caps its lifetime;
        # whale-auth-tools independently checks its signed expiresAt timestamp.
        await self.storage("set", self.pending_key(browser, challenge["signature"]), json.dumps(challenge), ex=600)
        return self.response(challenge, PENDING, browser, 600)

    async def verify(self, request: Request):
        await self.ready(request)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16384:
                raise HTTPException(413, "Verification payload is too large")
        try:
            data = json.loads(raw)
            payload = data["altchaPayload"]
            if not isinstance(payload, str):
                raise ValueError()
            decoded = json.loads(base64.b64decode(payload, validate=True))
            challenge = decoded["challenge"]
            signature = challenge["signature"]
            if not isinstance(signature, str):
                raise ValueError()
        except (ValueError, KeyError, TypeError, binascii.Error):
            raise HTTPException(400, "Invalid ALTCHA payload") from None
        browser = self.token(request, PENDING)
        if not browser:
            raise HTTPException(403, "Complete browser verification first")
        issued = await self.storage("getdel", self.pending_key(browser, signature))
        if not issued or json.loads(issued) != challenge:
            raise HTTPException(403, "Challenge expired or already used; try again")
        result = await self.upstream("verify", {"altchaPayload": payload})
        if result.get("verified") is not True or any(result.get(key) for key in ("expired", "invalidSignature", "invalidSolution", "error")):
            raise HTTPException(403, "Verification failed; try again")
        token = secrets.token_hex(32)
        await self.storage("set", KEY + token, "verified", ex=self.valves.clearance_ttl)
        return self.response({"verified": True, "expiresIn": self.valves.clearance_ttl}, COOKIE, token, self.valves.clearance_ttl)

    async def check(self, request: Request):
        await self.ready(request, check_origin=False)
        valid = await self.cleared(request)
        return Response(status_code=204 if valid else 401, headers={"Cache-Control": "no-store"})

    async def page(self, request: Request):
        await self.ready(request, check_origin=False)
        nonce = secrets.token_urlsafe(24)
        # The proxy serves this HTML at the original navigation URL. Reloading
        # preserves the path, query and fragment without accepting redirect URLs.
        html = GATE_HTML.replace("__NONCE__", nonce)
        return HTMLResponse(html, headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'self' 'nonce-{nonce}'; "
                f"style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "worker-src 'self' blob:; img-src 'self' data:; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
        })


GATE_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Checking your browser</title>
<style>
:root{color-scheme:light dark;font:16px system-ui,sans-serif;background:light-dark(#fafafa,#111);color:light-dark(#222,#eee)}
body{margin:0;min-height:100vh;display:grid;place-items:center}main{max-width:30rem;padding:2rem;text-align:center}
h1{font-size:1.6rem}p{line-height:1.6}button{font:inherit;padding:.6rem 1rem;cursor:pointer}#widget{margin:1.5rem auto}
</style></head><body><main>
<h1>Checking your browser</h1><p>This brief security check helps keep automated abuse away. You will continue automatically.</p>
<p id="status" role="status" aria-live="polite">Preparing verification…</p>
<div id="widget"></div><button id="retry" hidden>Try again</button>
<noscript>JavaScript is required to complete this check.</noscript>
</main><script type="module" nonce="__NONCE__">
const status = document.getElementById('status');
const retry = document.getElementById('retry');
let busy = false;
async function post(path, body = {}) {
  const response = await fetch('/api/v1/altcha-gate/' + path, {
    method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  if (response.status === 429) throw new Error('Too many attempts. Please wait a minute and try again.');
  if (!response.ok) throw new Error('Verification is unavailable or expired. Please try again.');
  return response.json();
}
function proceed() {
  status.textContent = 'Verified. Continuing…';
  if (location.pathname === '/auth/verify-human') location.replace('/');
  else location.reload();
}
function failed(error) {
  status.textContent = error.message || 'Verification failed. Please try again.';
  retry.hidden = false;
  busy = false;
}
async function start() {
  if (busy) return;
  busy = true;
  retry.hidden = true;
  status.textContent = 'Checking your browser…';
  document.getElementById('widget').replaceChildren();
  try {
    await import('/auth/verify-human/altcha-3.5.0.min.js');
    const challenge = await post('challenge');
    if (challenge.verified === true) return proceed();
    const widget = document.createElement('altcha-widget');
    widget.setAttribute('challenge', JSON.stringify(challenge));
    widget.addEventListener('verified', async event => {
      try {
        const result = await post('verify', {altchaPayload: event.detail.payload});
        if (result.verified !== true) throw new Error('Verification failed. Please try again.');
        proceed();
      } catch (error) { failed(error); }
    }, {once: true});
    widget.addEventListener('statechange', event => {
      if (['error', 'expired'].includes(event.detail.state)) failed(new Error('Verification failed or expired. Please try again.'));
    });
    // Register the load listener before insertion; auto-start only once ready.
    widget.addEventListener('load', () => { widget.verify().catch(failed); }, {once: true});
    document.getElementById('widget').append(widget);
  } catch (error) { failed(error); }
}
retry.addEventListener('click', start);
start();
</script></body></html>'''
