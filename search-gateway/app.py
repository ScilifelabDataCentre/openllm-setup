"""Search gateway v1: no web search leaves until the user approves it.

How it works
------------
Open WebUI never searches by itself. A small Open WebUI tool ("Web search with
approval") sends every search to POST /search here, together with the
logged-in user's id and the chat the search comes from. For each search:

  1. Is this chat (or, for searches without a chat, this user) inside an
     approved session? Then the search runs on DuckDuckGo right away.
  2. Has the user already answered for exactly this search?
     Approved once: it runs, once. Declined: we say so, once.
  3. Otherwise it is new. Nothing is sent: we store it as "waiting" and
     answer "approval needed", with an id and a link into Open WebUI.

The user always answers inside Open WebUI, never on a page of ours:
  - In a chat, the tool shows the question right away (approve once, approve
    for this session, decline) and sends the answer to POST /approvals/<id>.
  - From a terminal or an agent there is nobody to show a question to, so
    the model shows the link instead. It opens the "Search approvals"
    assistant in Open WebUI, which asks the same question the same way.

This service is internal only: no route exposes it, and every endpoint needs
the API key. Open WebUI's login decides who the user is, and this service
makes sure only that user can answer for their own searches.

Life of one approval
--------------------
  status   | event                            | result
  waiting  | user: approve once               | status becomes "allowed"
  waiting  | user: approve for this session   | session starts, entry removed
  waiting  | user: decline                    | status becomes "denied"
  allowed  | the same search again            | search runs once, entry removed
  denied   | the same search again            | "denied" reply, entry removed
  any      | older than the expiry time       | entry removed

Things to know before changing this file
----------------------------------------
- State lives in memory (the dicts below). Run exactly one process and one
  pod. A restart clears open approvals and sessions; users are asked again.
- All handlers are async and run on one event loop, so they never change the
  state at the same time. The only slow call (DuckDuckGo) runs in a worker
  thread, after its approval has been removed, so an approval is used once.
- Where to extend later: an automatic content check belongs at the start of
  search(), before step 1, so it also covers searches inside an approved
  session (runbook, section 18).
"""

import asyncio
import os
import secrets
import time
from urllib.parse import quote

from ddgs import DDGS
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Settings (environment variables, set in deployment.yaml)
# ---------------------------------------------------------------------------

# Shared secret. The Open WebUI tool and assistant send it as "Authorization: Bearer <key>".
API_KEY = os.environ["API_KEY"]

# Open WebUI's address as users' browsers reach it, e.g. https://openllm.scilifelab-2-dev.sys.kth.se
# Approval links for terminals and agents point here.
OPENWEBUI_URL = os.environ["OPENWEBUI_URL"].rstrip("/")

# Id of the "Search approvals" assistant in Open WebUI (the id of its function).
APPROVALS_MODEL = os.environ.get("APPROVALS_MODEL", "search_approvals")

# How long an unanswered approval is kept. Default: 15 minutes.
EXPIRY = int(os.environ.get("APPROVAL_EXPIRY_SECONDS", "900"))

# How long "approve for this session" lasts. Default: 8 hours, about a working day.
SESSION_HOURS = float(os.environ.get("SESSION_HOURS", "8"))

# How many approvals one user may have waiting at the same time.
MAX_WAITING = int(os.environ.get("MAX_WAITING_PER_USER", "5"))

# How many approvals one user may request per hour. Stops runaway agents.
MAX_PER_HOUR = int(os.environ.get("MAX_APPROVALS_PER_HOUR", "30"))

app = FastAPI()

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# One entry per approval, keyed by its random id:
#   user:    Open WebUI user id; only this user can answer, and it only works for them
#   query:   the exact search text; an approval only works for this exact text
#   chat:    the chat the search came from ("" for terminals and agents)
#   status:  "waiting", "allowed" or "denied"
#   created: time.time() when the entry was made; used for expiry
approvals = {}

# Approved sessions: (user, chat) -> time.time() when the session ends.
# chat is "" for searches without a chat, so such a session covers all of
# that user's searches from terminals and agents.
sessions = {}

# For the hourly limit: user id -> times this user got an approval request.
asked = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def authorized(authorization):
    """Only Open WebUI (the tool and the assistant) holds the key.

    compare_digest compares in constant time, so the key cannot be guessed by
    measuring response times.
    """
    return secrets.compare_digest(authorization, f"Bearer {API_KEY}")


def denied_access():
    return JSONResponse({"detail": "Unauthorized"}, status_code=401)


def result(title, text, **extra):
    """Wrap a message for the model as one search result.

    The search format is a list of {"link", "title", "snippet"}, and the model
    reads the snippet. When we have no real results (approval needed,
    declined, limits, errors), our message goes there. "message": True tells
    the tool that this is a message from us, not a web page.
    """
    return [{"link": OPENWEBUI_URL, "title": title, "snippet": text, "message": True, **extra}]


def waiting(rid, query):
    """Reply for a search that still needs the user's answer.

    In a chat, the tool reads "approval_id" and asks the user directly, so the
    model never sees this text. From a terminal or an agent, the model shows
    the text, and the link opens the "Search approvals" assistant with the id
    already filled in (?model=...&q=...).
    """
    link = f"{OPENWEBUI_URL}/?model={quote(APPROVALS_MODEL)}&q={rid}"
    return result(
        "Approval needed: nothing was sent",
        f"Show the user this link to review the search in Open WebUI: {link} "
        f'When they have approved it, run exactly the same search again: "{query}". '
        f"The link expires in {EXPIRY // 60} minutes.",
        link=link,
        approval_id=rid,
        session_hours=SESSION_HOURS,
    )


def drop_expired():
    """Remove old approvals and ended sessions.

    Called at the start of every request instead of running a background
    timer: simpler, and nothing can use an old entry in between.
    """
    now = time.time()
    for rid in [r for r, a in approvals.items() if now - a["created"] > EXPIRY]:
        del approvals[rid]
    for key in [k for k, end in sessions.items() if end <= now]:
        del sessions[key]


def run_search(query, count):
    """Send one search to DuckDuckGo and return results in the search format.

    The backend is pinned to "duckduckgo" on purpose: the DDGS default ("auto")
    picks a random engine for every search (Google, Yandex and others), which
    would send queries to companies we did not choose.

    DDGS returns {"href", "title", "body"}; we return {"link", "title",
    "snippet"}. This call blocks, so it runs in a worker thread (see searched()).
    """
    items = DDGS().text(query, max_results=count, backend="duckduckgo")
    return [{"link": i["href"], "title": i.get("title", ""), "snippet": i.get("body", "")} for i in items]


async def searched(query, count):
    """Run the search in a worker thread. This is where a search leaves the cluster."""
    try:
        return await asyncio.to_thread(run_search, query, count)
    except Exception:
        # DuckDuckGo refused, timed out, or the pod has no internet access.
        return result("Search failed", "The search engine did not answer. Try again later.")


# ---------------------------------------------------------------------------
# Endpoint for searches
# ---------------------------------------------------------------------------


@app.post("/search")
async def search(
    body: dict,  # {"query": "...", "count": 5}
    # FastAPI reads these from the request headers ("x_openwebui_user_id" means
    # the header "X-OpenWebUI-User-Id"). The tool fills them in from Open
    # WebUI's login. If they are missing, everyone shares the user "unknown".
    authorization: str = Header(""),
    x_openwebui_user_id: str = Header("unknown"),
    x_openwebui_chat_id: str = Header(""),  # empty for terminals and agents
):
    if not authorized(authorization):
        return denied_access()

    query = str(body.get("query", "")).strip()
    count = int(body.get("count", 5))
    user, chat = x_openwebui_user_id, x_openwebui_chat_id
    drop_expired()

    # (A future content check goes here, so it covers every search, sessions included.)

    # 1. Inside an approved session? Then no question: search right away.
    if (user, chat) in sessions:
        return await searched(query, count)

    # 2. Has this user already answered for exactly this search?
    rid = next((r for r, a in approvals.items() if a["user"] == user and a["query"] == query), None)
    if rid:
        status = approvals[rid]["status"]
        if status == "waiting":
            # Not answered yet: give the same approval again, never a second one.
            return waiting(rid, query)

        # Answered. Remove the entry first, so the answer is used exactly once.
        del approvals[rid]
        if status == "denied":
            return result("Search declined", f'The user declined the search "{query}". Answer without web results.')
        return await searched(query, count)

    # 3. A new search. Check the limits before creating an approval.
    now = time.time()
    recent = asked[user] = [t for t in asked.get(user, []) if now - t < 3600]  # keep only the last hour
    if sum(a["user"] == user and a["status"] == "waiting" for a in approvals.values()) >= MAX_WAITING:
        return result("Too many searches waiting", "Ask the user to answer the open approvals first.")
    if len(recent) >= MAX_PER_HOUR:
        return result("Hourly limit reached", "Too many searches this hour. Try again later.")

    # 4. Create the approval. The id is 16 random bytes, so it cannot be guessed,
    #    but the id alone is not enough: only the same user can answer it.
    rid = secrets.token_urlsafe(16)
    approvals[rid] = {"user": user, "query": query, "chat": chat, "status": "waiting", "created": now}
    recent.append(now)
    return waiting(rid, query)


# ---------------------------------------------------------------------------
# Endpoints for answers (used by the Open WebUI tool and the assistant)
# ---------------------------------------------------------------------------


@app.get("/approvals/{rid}")
async def approval(rid: str, user: str = "", authorization: str = Header("")):
    """Show one approval to its own user, so the assistant can ask about it.

    Anyone else, and any expired or used id, gets the same "not found", so
    the answer reveals nothing about other users' searches.
    """
    if not authorized(authorization):
        return denied_access()
    drop_expired()
    a = approvals.get(rid)
    if not a or a["user"] != user:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return {"query": a["query"], "chat": a["chat"], "status": a["status"], "session_hours": SESSION_HOURS}


@app.post("/approvals/{rid}")
async def answer(rid: str, body: dict, authorization: str = Header("")):
    """Record the user's answer: {"user": "<id>", "decision": "once" | "session" | "decline"}.

    Only the user who made the search can answer, and only the first answer
    counts. The search itself does not run here: it runs when the tool (or,
    after "continue", the model) sends the same search again.
    """
    if not authorized(authorization):
        return denied_access()
    drop_expired()
    a = approvals.get(rid)
    user, decision = str(body.get("user", "")), body.get("decision")
    if not a or a["user"] != user:
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if a["status"] != "waiting":
        return {"status": a["status"]}  # already answered: keep the first answer

    if decision == "once":
        a["status"] = status = "allowed"
    elif decision == "session":
        # From now on, this chat (or, without a chat, this user's searches)
        # skips the question until the session ends. The session covers this
        # search too, so the approval itself is no longer needed.
        sessions[(user, a["chat"])] = time.time() + SESSION_HOURS * 3600
        del approvals[rid]
        status = "session"
    elif decision == "decline":
        a["status"] = status = "denied"
    else:
        return JSONResponse({"detail": "decision must be once, session or decline"}, status_code=400)

    # Audit line: who answered what, without the search text.
    print(f"search {status} for user {user}", flush=True)
    return {"status": status}


# ---------------------------------------------------------------------------
# Health check (used by the readinessProbe in deployment.yaml)
# ---------------------------------------------------------------------------


@app.get("/healthz")
async def healthz():
    return {"ok": True}
