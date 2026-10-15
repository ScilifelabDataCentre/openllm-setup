"""Checks for the search approval flow: the gateway, the Open WebUI tool and the Search approvals assistant.

Run from the repo root, with plain Python 3.10 or newer and nothing installed:

    python3 search-gateway/tests/test_search_approval.py

FastAPI, DDGS and pydantic are replaced by small stand-ins, and the tool and the
assistant talk to the gateway's functions directly instead of over HTTP. Nothing
leaves the machine.
"""

import asyncio, json, os, sys, time, types, urllib.error, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "search-gateway"), str(ROOT / "openwebui-functionality" / "search-approval")]

# ---- stand-ins for packages this sandbox does not have (the real ones run in the images) ----
fa = types.ModuleType("fastapi"); fr = types.ModuleType("fastapi.responses")
class FastAPI:
    def get(self, path): return lambda f: f
    def post(self, path): return lambda f: f
class Resp:
    def __init__(self, content, status_code=200): self.content, self.status_code = content, status_code
fa.FastAPI, fa.Header = FastAPI, (lambda default=None: default); fr.JSONResponse = Resp
dd = types.ModuleType("ddgs"); calls = []
class DDGS:
    def text(self, q, max_results, backend):
        calls.append((q, backend)); return [{"title": "T", "href": "https://example.org", "body": "B"}]
dd.DDGS = DDGS
pd = types.ModuleType("pydantic"); pd.BaseModel = object; pd.Field = lambda default=None, **kw: default
sys.modules.update({"fastapi": fa, "fastapi.responses": fr, "ddgs": dd, "pydantic": pd})
os.environ.update(API_KEY="test", OPENWEBUI_URL="https://owu.example/", MAX_WAITING_PER_USER="3", MAX_APPROVALS_PER_HOUR="50")

import app as gw
import web_search_tool as tool_mod
import search_approvals_pipe as pipe_mod
run = asyncio.run
K = "Bearer test"

def search(q, user="u1", chat="c1", auth=K):
    return run(gw.search({"query": q, "count": 3}, authorization=auth, x_openwebui_user_id=user, x_openwebui_chat_id=chat))
def answer(rid, user, decision):
    return run(gw.answer(rid, {"user": user, "decision": decision}, authorization=K))

# ================= gateway =================
assert search("x", auth="Bearer bad").status_code == 401
r = search("sepsis guidelines"); rid = r[0]["approval_id"]
assert r[0]["link"] == f"https://owu.example/?model=search_approvals&q={rid}" and r[0]["message"] and not calls
assert run(gw.approval(rid, user="u1", authorization=K))["query"] == "sepsis guidelines"
assert run(gw.approval(rid, user="u2", authorization=K)).status_code == 404          # not your approval
assert answer(rid, "u2", "once").status_code == 404                                  # cannot answer others'
assert answer(rid, "u1", "maybe").status_code == 400
assert answer(rid, "u1", "once") == {"status": "allowed"}
assert answer(rid, "u1", "decline") == {"status": "allowed"}                         # first answer counts
assert search("sepsis guidelines")[0]["link"] == "https://example.org" and calls[-1] == ("sepsis guidelines", "duckduckgo")
assert "approval_id" in search("sepsis guidelines")[0]                                # once means once
# decline
d = search("declined q", chat="c2")[0]["approval_id"]; answer(d, "u1", "decline")
assert search("declined q", chat="c2")[0]["title"] == "Search declined"
# session: covers the same chat only, until it ends
s = search("first", chat="c3")[0]["approval_id"]; n = len(calls)
assert answer(s, "u1", "session") == {"status": "session"} and s not in gw.approvals
assert search("first", chat="c3")[0]["link"] == "https://example.org"
assert search("something else", chat="c3")[0]["link"] == "https://example.org" and len(calls) == n + 2
assert "approval_id" in search("something else", chat="c4")[0]                      # other chat: ask again
assert "approval_id" in search("something else", user="u2", chat="c3")[0]           # other user: ask again
gw.sessions[("u1", "c3")] = time.time() - 1
assert "approval_id" in search("after the end", chat="c3")[0]                       # session ended
# limits and expiry
for q in ("w1", "w2"): search(q, user="u5")
assert "approval_id" in search("w3", user="u5")[0]
assert search("w4", user="u5")[0]["title"] == "Too many searches waiting"
old = search("old", user="u6")[0]["approval_id"]; gw.approvals[old]["created"] -= 10_000
assert run(gw.approval(old, user="u6", authorization=K)).status_code == 404
print("gateway: ok")

# ================= tool (in a chat, and from the API) =================
async def fake_gateway(self, path, body, user_id="", chat_id=""):
    if path == "/search":
        return await gw.search(body, authorization=K, x_openwebui_user_id=user_id, x_openwebui_chat_id=chat_id)
    res = await gw.answer(path.rsplit("/", 1)[1], body, authorization=K)
    if isinstance(res, Resp): raise urllib.error.HTTPError(path, res.status_code, "error", {}, None)
    return res
tool_mod.Tools._gateway = fake_gateway
t = tool_mod.Tools()
asked = []
def picker(choice):
    async def f(event): asked.append(event["data"]["title"]); return choice
    return f
events = []
async def emit(e): events.append(e)
def use(q, chat="t1", user="tu", call=None):
    return run(t.web_search(q, __user__={"id": user}, __metadata__={"chat_id": chat}, __event_call__=call, __event_emitter__=emit))

n = len(calls)
out = use("q once", call=picker(tool_mod.ONCE))
assert json.loads(out)[0]["link"] == "https://example.org" and len(calls) == n + 1 and asked == ["Allow this web search?"]
assert any(e["type"] == "source" for e in events) and any(e["type"] == "status" for e in events)
asked.clear(); use("q once", call=picker(tool_mod.ONCE)); assert len(asked) == 1        # asked again: it was once
asked.clear(); use("q session", call=picker(tool_mod.SESSION)); use("another", call=picker(tool_mod.ONCE))
assert len(asked) == 1                                                                   # session: no second question
n = len(calls)
assert "declined" in use("q no", chat="t2", call=picker(tool_mod.DECLINE)) and len(calls) == n
assert "declined" in use("q cancel", chat="t3", call=picker(False)) and len(calls) == n  # Cancel = Decline
async def no_browser(e): return {"error": "Client session disconnected."}
out = use("from terminal", chat="", call=no_browser)
assert "?model=search_approvals&q=" in out and len(calls) == n                          # API: link instead
async def expires(e):
    gw.approvals.clear(); return tool_mod.ONCE
assert "expired" in use("slow answer", chat="t4", call=expires)
# typed answers (older Open WebUI shows a text box): clear words work, anything else is Decline
asked.clear(); use("typed session", chat="t5", call=picker("session")); use("typed next", chat="t5", call=picker("once"))
assert len(asked) == 1
assert "declined" in use("typed unclear", chat="t6", call=picker("maybe"))
print("tool: ok")

# ================= pipe ("Search approvals" assistant) =================
async def pipe_gateway(self, method, path, body=None):
    p = urllib.parse.urlparse(path); rid = urllib.parse.unquote(p.path.rsplit("/", 1)[1])
    if method == "GET":
        res = await gw.approval(rid, user=urllib.parse.parse_qs(p.query).get("user", [""])[0], authorization=K)
    else:
        res = await gw.answer(rid, body, authorization=K)
    if isinstance(res, Resp): raise urllib.error.HTTPError(path, res.status_code, "error", {}, None)
    return res
pipe_mod.Pipe._gateway = pipe_gateway
pp = pipe_mod.Pipe()
def open_link(rid, user, call):
    return run(pp.pipe({"messages": [{"role": "user", "content": rid}]}, __user__={"id": user}, __event_call__=call))

api = search("from an agent", user="pu", chat="")[0]["approval_id"]
assert open_link(api, "someone else", picker(pipe_mod.ONCE)) == pipe_mod.GONE         # not your link
assert open_link(api, "pu", picker(pipe_mod.ONCE)).startswith("Approved once")
assert search("from an agent", user="pu", chat="")[0]["link"] == "https://example.org"  # "continue" now works
assert "already used" in open_link(api, "pu", picker(pipe_mod.ONCE))
ses = search("agent 2", user="pu", chat="")[0]["approval_id"]
assert "Approved for this session" in open_link(ses, "pu", picker(pipe_mod.SESSION)) and ("pu", "") in gw.sessions
ch = search("from a chat", user="pu", chat="chat-9")[0]["approval_id"]
assert "[Back to the chat](/c/chat-9)" in open_link(ch, "pu", picker(pipe_mod.DECLINE))
assert open_link("hello there", "pu", picker(pipe_mod.ONCE)) == pipe_mod.HELP
typed = search("typed in pipe", user="pv", chat="")[0]["approval_id"]  # pv: pu already has a session
assert open_link(typed, "pv", picker("1")).startswith("Approved once")
print("pipe: ok")
print("all checks passed")
