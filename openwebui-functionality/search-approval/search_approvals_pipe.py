"""
title: Search approvals
description: Approve or decline web searches that came from a terminal or an agent. Opened from the approval links the assistant shows.
version: 1.1.0
"""

# How it fits together
# --------------------
# When a search comes from a terminal or an agent, nobody can be asked in a
# chat. The search gateway then hands out a link into Open WebUI:
#
#     https://<open webui>/?model=search_approvals&q=<message with the approval id>
#
# The link opens a new chat with this "assistant", behind Open WebUI's normal
# login, with a short message holding the approval id in the message box. Once
# sent, this assistant reads the id from that message. The search text itself
# is never in the link (links end up in browser history and server logs).
#
# This is not an AI model: it is this small program. It looks the approval up,
# shows the same box as the chat tool (approve once, approve for this session,
# decline) and sends the answer to the gateway. The box and the code that
# shows it are copied from web_search_tool.py: keep the two the same.
#
# Install it as a Function with the id "search_approvals"; that id is the
# model id in the link (APPROVALS_MODEL in the gateway's deployment.yaml).
#
# Security: the user's identity comes from Open WebUI's login (__user__). The
# gateway only shows and accepts approvals that belong to that user, so a
# link opened by someone else does nothing.

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field

HELP = "I handle web search approvals. Open an approval link from the assistant to review a search."
GONE = "This approval has expired, was already used, or belongs to someone else."

# The three answers, as the user sees them. Keep them the same in web_search_tool.py.
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


def approval_id_in(text):
    """The approval id in the message the approval link fills in, or None.

    Ids are 22 characters of letters, digits, "-" and "_" (from the gateway's
    secrets.token_urlsafe(16)); the words around them are shorter.
    """
    text = urllib.parse.unquote(text)  # also works when someone pastes the whole link
    ids = re.findall(r"[A-Za-z0-9_-]{20,}", text)
    return ids[-1] if ids else None


def last_user_text(body):
    """The text of the user's latest message."""
    for message in reversed(body.get("messages", [])):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, list):  # text plus images: keep the text parts
                content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
            return content.strip()
    return ""


class Pipe:
    class Valves(BaseModel):
        GATEWAY_URL: str = Field("http://search-gateway:8000", description="The search gateway inside the cluster")
        API_KEY: str = Field("", description="The gateway's API key (api-key in the search-gateway secret)")
        ASK_TIMEOUT_SECONDS: int = Field(300, description="How long the approval box waits for an answer")

    def __init__(self):
        self.valves = self.Valves()

    async def _gateway(self, method, path, body=None):
        """Call the gateway and return its JSON answer. urllib blocks, so it runs in a thread."""

        def call():
            request = urllib.request.Request(
                self.valves.GATEWAY_URL.rstrip("/") + path,
                data=json.dumps(body).encode() if body is not None else None,
                method=method,
                headers={"Authorization": f"Bearer {self.valves.API_KEY}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)

        return await asyncio.to_thread(call)

    async def pipe(self, body: dict, __user__: dict = None, __event_call__=None) -> str:
        user_id = (__user__ or {}).get("id", "")
        approval_id = approval_id_in(last_user_text(body))
        if not approval_id:
            return HELP
        path = f"/approvals/{urllib.parse.quote(approval_id, safe='')}"

        try:
            # Only the logged-in user's own approvals are found.
            approval = await self._gateway("GET", f"{path}?user={urllib.parse.quote(user_id, safe='')}")
            if approval["status"] != "waiting":
                return f"This search was already answered ({approval['status']})."

            search = f'Web search: "{approval["query"]}"\n\n'  # so this chat shows what was decided
            where = "that chat" if approval["chat"] else "your terminals and agents"
            choice = await ask(
                __event_call__, approval["query"], approval.get("session_hours"), where, self.valves.ASK_TIMEOUT_SECONDS
            )
            # No answer: nothing is decided, and the approval stays open for its 15 minutes.
            if choice is None:
                return search + "The question could not be shown here. Open the approval link again to answer."
            if choice == CLOSED:
                return search + "Closed without an answer, so nothing was sent. Send the message again to answer."
            if choice == TIMEOUT:
                return search + "No answer in time, so nothing was sent. Send the message again to answer."
            await self._gateway("POST", path, {"user": user_id, "decision": DECISIONS[choice]})
        except urllib.error.HTTPError as error:
            return GONE if error.code == 404 else "The search gateway is not available right now."
        except Exception:
            return "The search gateway is not available right now."

        hours = f"{approval.get('session_hours') or 8:g}"
        reply = search + {
            ONCE: "Approved once. Go back and write *continue*: the same search now runs once.",
            SESSION: f"Approved for this session. Searches from the same place run without asking "
            f"for the next {hours} hours. Go back and write *continue*.",
            DECLINE: "Declined. Nothing was sent.",
        }[choice]
        if approval["chat"]:  # the search came from a chat: offer the way back
            reply += f"\n\n[Back to the chat](/c/{urllib.parse.quote(approval['chat'], safe='')})"
        return reply
