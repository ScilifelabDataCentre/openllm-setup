"""Checks for the Open WebUI tool and the Search approvals assistant, together with the gateway.

The gateway's code lives in its own repository, openllm-search-gateway. Clone it
next to openllm-setup (or point SEARCH_GATEWAY_DIR at a clone), then run from the
openllm-setup root, with plain Python 3.10 or newer and nothing installed:

    python3 openwebui-functionality/search-approval/tests/test_tool_and_assistant.py

FastAPI, DDGS and pydantic are replaced by small stand-ins, and the tool and the
assistant talk to the gateway's functions directly instead of over HTTP. Nothing
leaves the machine. The gateway's own checks are in openllm-search-gateway.
"""

# cspell:ignore ddgs DDGS fastapi openwebui OPENWEBUI pydantic

import asyncio, json, os, sys, time, types, urllib.error, urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]  # openwebui-functionality/search-approval
SETUP_ROOT = Path(__file__).resolve().parents[3]  # openllm-setup
GATEWAY = Path(os.environ.get("SEARCH_GATEWAY_DIR", SETUP_ROOT.parent / "openllm-search-gateway"))
if not (GATEWAY / "app.py").exists():
    sys.exit(f"Gateway code not found at {GATEWAY}. Clone openllm-search-gateway next to openllm-setup, "
             "or set SEARCH_GATEWAY_DIR to your clone.")
sys.path[:0] = [str(GATEWAY), str(HERE)]

# ---- stand-ins for packages that only exist in the real images ----
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
def picker(answer):
    """A browser that answers the approval box with this value: "once", "session", "decline", "closed" ..."""
    async def f(event): asked.append(event["type"]); return answer
    return f
events = []
async def emit(e): events.append(e)
def use(q, chat="t1", user="tu", call=None):
    return run(t.web_search(q, __user__={"id": user}, __metadata__={"chat_id": chat}, __event_call__=call, __event_emitter__=emit))

n = len(calls)
out = use("q once", call=picker("once"))
assert json.loads(out)[0]["link"] == "https://example.org" and len(calls) == n + 1 and asked == ["execute"]
statuses = [e["data"]["description"] for e in events if e["type"] == "status"]
assert statuses[:2] == ["Web search: waiting for your approval", "Web search: Approve once"]
assert any(e["type"] == "source" for e in events)
asked.clear(); use("q once", call=picker("once")); assert len(asked) == 1                # once means once
asked.clear(); use("q session", call=picker("session")); use("another", call=picker("once"))
assert len(asked) == 1                                                                   # session: no second question
n = len(calls)
assert "declined" in use("q no", chat="t2", call=picker("decline")) and len(calls) == n
assert "declined" in use("q odd", chat="t3", call=picker("something else")) and len(calls) == n  # unknown = Decline
# a closed box: nothing sent, the approval stays open, and asking again shows the same question
asked.clear()
assert "closed the approval box" in use("q closed", chat="t7", call=picker("closed")) and len(calls) == n
waiting = [a for a in gw.approvals.values() if a["query"] == "q closed"]
assert len(waiting) == 1 and waiting[0]["status"] == "waiting"
assert json.loads(use("q closed", chat="t7", call=picker("once")))[0]["link"] == "https://example.org" and len(asked) == 2
n = len(calls)
# no answer in time: the box's own timer, Open WebUI's timeout, and the tool's own limit
assert "No answer" in use("q late", chat="t8", user="lu", call=picker("timeout"))
async def owui_timeout(e): return {"error": "Event call timed out. The browser tab may be inactive or closed."}
assert "No answer" in use("q late 2", chat="t8", user="lu", call=owui_timeout)
t.valves.ASK_TIMEOUT_SECONDS = -14  # the tool then waits only 1 second
async def never(e): await asyncio.sleep(3); return "once"
out = use("q late 3", chat="t8", user="lu", call=never)
t.valves.ASK_TIMEOUT_SECONDS = 300
assert "No answer" in out and "?model=search_approvals&q=" in out and len(calls) == n
# no browser (an API call): the approval link instead
async def no_browser(e): return {"error": "Client session disconnected."}
out = use("from terminal", chat="", call=no_browser)
assert "?model=search_approvals&q=" in out and len(calls) == n
# the box could not be drawn: Open WebUI's own list is used instead, with Decline preselected
async def old_browser(e):
    asked.append(e["type"])
    if e["type"] == "execute": return {"dialog_error": "TypeError"}
    assert e["data"]["value"] == tool_mod.DECLINE and e["data"]["input"]["options"][0] == tool_mod.DECLINE
    return tool_mod.ONCE
asked.clear()
assert json.loads(use("q fallback", chat="t9", call=old_browser))[0]["link"] == "https://example.org"
assert asked == ["execute", "input"]
# the approval expires while the question is open
async def expires(e):
    gw.approvals.clear(); return "once"
assert "expired" in use("slow answer", chat="t4", call=expires)
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
assert open_link(api, "someone else", picker("once")) == pipe_mod.GONE                 # not your link
assert "Approved once" in open_link(api, "pu", picker("once"))
assert search("from an agent", user="pu", chat="")[0]["link"] == "https://example.org"  # "continue" now works
assert "already used" in open_link(api, "pu", picker("once"))
# closing the box decides nothing: the same link still works afterwards
cl = search("closed in pipe", user="pc", chat="")[0]["approval_id"]
assert "Closed without an answer" in open_link(cl, "pc", picker("closed")) and gw.approvals[cl]["status"] == "waiting"
assert "Approved once" in open_link(cl, "pc", picker("once"))
ses = search("agent 2", user="pu", chat="")[0]["approval_id"]
assert "Approved for this session" in open_link(ses, "pu", picker("session")) and ("pu", "") in gw.sessions
ch = search("from a chat", user="pu", chat="chat-9")[0]["approval_id"]
assert "[Back to the chat](/c/chat-9)" in open_link(ch, "pu", picker("decline"))
assert open_link("hello there", "pu", picker("once")) == pipe_mod.HELP
# the message from the link, a pasted link, and a bare id all work
msg = search("from the link", user="pw", chat="")[0]
assert open_link(f"Review web search request {msg['approval_id']}", "pw", picker("once")).startswith('Web search: "from the link"')
pasted = search("pasted link", user="pw", chat="")[0]
assert "Approved once" in open_link(pasted["link"], "pw", picker("once"))
# an older browser: the list fallback, where a typed "1" means Approve once
typed = search("typed in pipe", user="pv", chat="")[0]["approval_id"]
async def old_browser_pipe(e): return {"dialog_error": "x"} if e["type"] == "execute" else "1"
assert "Approved once" in open_link(typed, "pv", old_browser_pipe)
print("pipe: ok")
print("all checks passed")
