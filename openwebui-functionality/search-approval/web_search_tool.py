"""
title: Web search with approval
description: Searches the web through the search gateway. Nothing is sent until the user approves the search, right here in the chat.
version: 1.1.0
"""

# How it fits together
# --------------------
# Open WebUI's built-in web search is switched off, and this tool is the
# model's only way to search. Every search goes to the search gateway, which
# decides. When the gateway answers "approval needed", this tool shows the user
# a small box in the same chat with three buttons (approve once, approve for
# this session, decline), sends the answer to the gateway, and, if approved,
# gets the results straight away: no extra step for the user.
#
# Closing the box (tapping outside it, or pressing Esc) means "not now":
# nothing is sent, and the model is told why. If nobody answers, the box closes
# itself after ASK_TIMEOUT_SECONDS and the model gets the approval link instead.
#
# From a terminal or an agent (an API call) there is no browser to ask. Then
# the tool gives the model the gateway's text with a link instead. The link
# opens the "Search approvals" assistant in Open WebUI (search_approvals_pipe.py).
#
# Security: the user's identity comes from Open WebUI's login (__user__), not
# from the model. The gateway only lets that user answer for their own searches.

import asyncio
import json
import urllib.error
import urllib.request

from pydantic import BaseModel, Field

# The three answers, as the user sees them. Keep them the same in search_approvals_pipe.py.
ONCE = "Approve once"
SESSION = "Approve for this session"
DECLINE = "Decline"
DECISIONS = {ONCE: "once", SESSION: "session", DECLINE: "decline"}  # what the gateway expects

# Two outcomes without an answer.
CLOSED = "closed"  # the user closed the box
TIMEOUT = "timeout"  # nobody answered in time

# What the box in the browser can return.
ANSWERS = {"once": ONCE, "session": SESSION, "decline": DECLINE, "closed": CLOSED, "timeout": TIMEOUT}

# Typed or picked answers for the fallback list (an older Open WebUI may show a text box).
TYPED = {"approve once": ONCE, "once": ONCE, "1": ONCE, "approve for this session": SESSION, "session": SESSION, "2": SESSION}

# The approval box. Open WebUI runs this JavaScript in the user's browser (an
# "execute" event) and sends back what it returns. __DATA__ is replaced with
# the texts below, as JSON. All texts go in with textContent, never as HTML,
# because the search text comes from the model.
DIALOG_JS = r"""
const data = __DATA__;
try {
  return await new Promise((resolve) => {
    const dark = document.documentElement.classList.contains('dark');
    const c = dark
      ? { card: '#171717', text: '#ececec', muted: '#a3a3a3', line: '#404040', box: '#262626', main: '#ececec', mainText: '#171717' }
      : { card: '#ffffff', text: '#171717', muted: '#525252', line: '#d4d4d4', box: '#f5f5f5', main: '#171717', mainText: '#ffffff' };
    const el = (tag, css, text) => {
      const node = document.createElement(tag);
      node.style.cssText = css;
      if (text) node.textContent = text;
      return node;
    };
    const overlay = el('div', 'position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(0,0,0,.6)');
    const card = el('div', `width:100%;max-width:26rem;border-radius:16px;padding:20px;background:${c.card};color:${c.text};font-family:inherit;box-shadow:0 12px 32px rgba(0,0,0,.4)`);
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.setAttribute('aria-label', data.title);
    card.append(
      el('div', 'font-size:1.05rem;font-weight:600;margin-bottom:8px', data.title),
      el('div', `font-size:.9rem;color:${c.muted}`, data.intro),
      el('div', `margin:8px 0 12px;padding:10px 12px;border-radius:10px;background:${c.box};font-size:.95rem;overflow-wrap:anywhere`, data.query),
      el('div', `font-size:.85rem;color:${c.muted};margin-bottom:8px`, data.note),
    );
    let done = false;
    let timer = null;
    const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); finish('closed'); } };
    const finish = (value) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      document.removeEventListener('keydown', onKey, true);
      overlay.remove();
      resolve(value);
    };
    const button = (label, note, value, main) => {
      const b = el('button', `display:block;width:100%;margin-top:8px;padding:10px 14px;border-radius:12px;font:inherit;font-size:.95rem;cursor:pointer;border:1px solid ${main ? c.main : c.line};background:${main ? c.main : 'transparent'};color:${main ? c.mainText : c.text}`, label);
      b.type = 'button';
      if (note) b.append(el('div', 'font-size:.78rem;opacity:.75;margin-top:2px', note));
      b.addEventListener('click', () => finish(value));
      card.append(b);
      return b;
    };
    button(data.once, '', 'once', true);
    button(data.session, data.sessionNote, 'session', false);
    const decline = button(data.decline, '', 'decline', false);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) finish('closed'); });
    document.addEventListener('keydown', onKey, true);
    timer = setTimeout(() => finish('timeout'), data.timeoutMs);
    overlay.append(card);
    document.body.append(overlay);
    decline.focus();  // the default: pressing Enter declines
  });
} catch (e) {
  return { dialog_error: String(e) };
}
"""


def dialog_event(query, session_hours, where, timeout):
    """The "execute" event that shows the approval box in the browser.

    where: what a session covers, for example "this chat".
    """
    data = {
        "title": "Allow this web search?",
        "intro": "The assistant wants to search the web for:",
        "query": query,
        "note": "Nothing has been sent yet. Tapping outside this box also declines.",
        "once": ONCE,
        "session": SESSION,
        "decline": DECLINE,
        "sessionNote": f"No more questions from {where} for {session_hours or 8:g} hours",
        "timeoutMs": timeout * 1000,
    }
    # json.dumps escapes every non-ASCII character, so the result is a safe JavaScript value.
    return {"type": "execute", "data": {"code": DIALOG_JS.replace("__DATA__", json.dumps(data))}}


def from_error(answer):
    """Open WebUI's own errors: {"error": ...}. A timeout means nobody answered; anything else, no browser."""
    return TIMEOUT if "timed out" in str(answer.get("error", "")) else None


async def ask(event_call, query, session_hours, where, timeout):
    """Ask the user in their browser.

    Returns ONCE, SESSION or DECLINE when they answer, CLOSED when they close
    the box, TIMEOUT when nobody answers in time, or None when nobody can be
    asked here (an API call).
    """
    if not event_call:
        return None
    try:
        # A little longer than the box's own timer, which closes the box first.
        answer = await asyncio.wait_for(event_call(dialog_event(query, session_hours, where, timeout)), timeout + 15)
    except asyncio.TimeoutError:
        return TIMEOUT
    except Exception:
        return None
    if isinstance(answer, dict) and "dialog_error" in answer:
        # The box could not be drawn: fall back to Open WebUI's own dialog with a list.
        return await ask_with_list(event_call, query, session_hours, where, timeout)
    if isinstance(answer, dict):
        return from_error(answer)
    return ANSWERS.get(answer, DECLINE)


async def ask_with_list(event_call, query, session_hours, where, timeout):
    """Fallback: Open WebUI's own dialog with a list of the three answers, Decline preselected."""
    try:
        answer = await asyncio.wait_for(
            event_call(
                {
                    "type": "input",
                    "data": {
                        "title": "Allow this web search?",
                        "message": f'The assistant wants to search the web for:\n\n"{query}"\n\n'
                        f'Nothing has been sent yet. Pick an answer, then Confirm. "{SESSION}" means '
                        f"no more questions from {where} for {session_hours or 8:g} hours.",
                        "value": DECLINE,
                        "input": {"type": "select", "options": [DECLINE, ONCE, SESSION]},
                    },
                }
            ),
            timeout,
        )
    except asyncio.TimeoutError:
        return TIMEOUT
    except Exception:
        return None
    if isinstance(answer, dict):
        return from_error(answer)
    return TYPED.get(str(answer or "").strip().lower(), DECLINE)  # Cancel returns False: Decline


def as_text(results):
    """What the model reads: real results as JSON, or the gateway's message as plain text."""
    real = [{k: r.get(k, "") for k in ("link", "title", "snippet")} for r in results if not r.get("message")]
    if real:
        return json.dumps(real, ensure_ascii=False)
    return results[0]["snippet"] if results else "No results."


class Tools:
    class Valves(BaseModel):
        GATEWAY_URL: str = Field("http://search-gateway:8000", description="The search gateway inside the cluster")
        API_KEY: str = Field("", description="The gateway's API key (api-key in the search-gateway secret)")
        RESULT_COUNT: int = Field(5, description="How many search results to ask for")
        ASK_TIMEOUT_SECONDS: int = Field(300, description="How long the approval box waits for an answer")

    def __init__(self):
        self.valves = self.Valves()

    async def _gateway(self, path, body, user_id="", chat_id=""):
        """POST to the gateway and return its JSON answer. urllib blocks, so it runs in a thread."""

        def call():
            request = urllib.request.Request(
                self.valves.GATEWAY_URL.rstrip("/") + path,
                data=json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {self.valves.API_KEY}",
                    "Content-Type": "application/json",
                    "X-OpenWebUI-User-Id": user_id,
                    "X-OpenWebUI-Chat-Id": chat_id,
                },
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)

        return await asyncio.to_thread(call)

    async def web_search(
        self,
        query: str,
        __user__: dict = None,
        __metadata__: dict = None,
        __event_call__=None,
        __event_emitter__=None,
    ) -> str:
        """
        Search the web for current information. The user approves every search before anything is sent, and this waits for their answer.
        :param query: The search text. Keep it short and leave out personal or confidential details.
        """
        user_id = (__user__ or {}).get("id", "")
        chat_id = (__metadata__ or {}).get("chat_id") or ""  # empty for API calls
        search = {"query": query, "count": self.valves.RESULT_COUNT}

        async def status(text, done=True):  # a short note in the chat, kept with the message
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": text, "done": done}})

        try:
            results = await self._gateway("/search", search, user_id, chat_id)
            approval = results[0] if results and results[0].get("approval_id") else None

            if approval:
                # Approval needed: ask right here in the chat.
                await status("Web search: waiting for your approval", done=False)
                where = "this chat" if chat_id else "your terminals and agents"
                choice = await ask(
                    __event_call__, query, approval.get("session_hours"), where, self.valves.ASK_TIMEOUT_SECONDS
                )
                if choice is None:
                    # Nobody can be asked here (an API call): the gateway's text carries the approval link.
                    await status("Web search: approval needed (link)")
                    return approval["snippet"]
                if choice == CLOSED:
                    # "Not now". The approval stays open, so asking again shows the same question.
                    await status("Web search: not sent (approval box closed)")
                    return (
                        f'The user closed the approval box without choosing, so the search "{query}" was not sent. '
                        "Tell them nothing was searched, and that they can ask again if they still want it."
                    )
                if choice == TIMEOUT:
                    await status("Web search: not sent (no answer in time)")
                    return "No answer to the approval question in time, so nothing was sent. " + approval["snippet"]

                await self._gateway(f"/approvals/{approval['approval_id']}", {"user": user_id, "decision": DECISIONS[choice]})
                await status(f"Web search: {choice}")
                if choice == DECLINE:
                    return f'The user declined the web search "{query}". Answer without web results.'
                # Approved: the gateway now runs the same search.
                results = await self._gateway("/search", search, user_id, chat_id)
        except urllib.error.HTTPError as error:
            if error.code == 404:  # the approval expired while the question was open
                await status("Web search: the approval expired")
                return "The approval has expired. Run the search again to get a new one."
            await status("Web search: not available")
            return "Web search is not available right now."
        except Exception:
            await status("Web search: not available")
            return "Web search is not available right now."

        # Show the real results as sources under the answer.
        if __event_emitter__:
            for r in results:
                if not r.get("message"):
                    await __event_emitter__(
                        {
                            "type": "source",
                            "data": {
                                "source": {"name": r.get("title") or r["link"]},
                                "document": [r.get("snippet", "")],
                                "metadata": [{"source": r["link"], "url": r["link"], "name": r.get("title", "")}],
                            },
                        }
                    )
        return as_text(results)
