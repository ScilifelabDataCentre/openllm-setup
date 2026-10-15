"""
title: Search approvals
description: Approve or decline web searches that came from a terminal or an agent. Opened from the approval links the assistant shows.
version: 1.0.0
"""

# How it fits together
# --------------------
# When a search comes from a terminal or an agent, nobody can be asked in a
# chat. The search gateway then hands out a link into Open WebUI:
#
#     https://<open webui>/?model=search_approvals&q=<approval id>
#
# The link opens a new chat with this "assistant", behind Open WebUI's normal
# login, and sends the approval id as the first message. This is not an AI
# model: it is this small program. It looks the approval up, asks the same
# question as the chat tool (approve once, approve for this session, decline)
# and sends the answer to the gateway.
#
# Install it as a Function with the id "search_approvals"; that id is the
# model id in the link (APPROVALS_MODEL in the gateway's deployment.yaml).
#
# Security: the user's identity comes from Open WebUI's login (__user__). The
# gateway only shows and accepts approvals that belong to that user, so a
# link opened by someone else does nothing.

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field

# The three answers, as the user sees them. Keep them the same in web_search_tool.py.
ONCE = "Approve once"
SESSION = "Approve for this session"
DECLINE = "Decline"
DECISIONS = {ONCE: "once", SESSION: "session", DECLINE: "decline"}  # what the gateway expects

# Typed answers, in case an older Open WebUI shows a text box instead of the list.
TYPED = {"approve once": ONCE, "once": ONCE, "1": ONCE, "approve for this session": SESSION, "session": SESSION, "2": SESSION}


def to_choice(answer):
    """Turn the dialog's answer into ONCE, SESSION or DECLINE. Anything unclear counts as DECLINE."""
    return TYPED.get(str(answer or "").strip().lower(), DECLINE)

HELP = "I handle web search approvals. Open an approval link from the assistant to review a search."
GONE = "This approval has expired, was already used, or belongs to someone else."


async def ask(event_call, query, session_hours, in_chat):
    """Ask the user in their browser. Returns ONCE, SESSION or DECLINE, or None if nobody answered.

    Same dialog as in web_search_tool.py, so users see one question everywhere.
    """
    if not event_call:
        return None
    where = "that chat" if in_chat else "your terminals and agents"
    try:
        answer = await event_call(
            {
                "type": "input",
                "data": {
                    "title": "Allow this web search?",
                    "message": f'The assistant wants to search the web for:\n\n"{query}"\n\n'
                    f'Nothing has been sent yet. "{SESSION}" lets all searches from {where} '
                    f"run without asking for the next {session_hours or 8:g} hours.",
                    "placeholder": "once, session or decline",
                    "input": {"type": "select", "options": [ONCE, SESSION, DECLINE]},
                },
            }
        )
    except Exception:
        return None
    if isinstance(answer, dict):  # {"error": ...}: the tab closed, or no answer in time
        return None
    return to_choice(answer)  # Cancel returns False, which counts as DECLINE


def last_user_text(body):
    """The text of the user's latest message: here, the approval id from the link."""
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
        approval_id = last_user_text(body)
        if not approval_id or " " in approval_id:
            return HELP
        path = f"/approvals/{urllib.parse.quote(approval_id, safe='')}"

        try:
            # Only the logged-in user's own approvals are found.
            approval = await self._gateway("GET", f"{path}?user={urllib.parse.quote(user_id, safe='')}")
            if approval["status"] != "waiting":
                return f"This search was already answered ({approval['status']})."

            choice = await ask(__event_call__, approval["query"], approval.get("session_hours"), bool(approval["chat"]))
            if choice is None:
                return "Nothing was decided. Open the approval link again to answer."
            await self._gateway("POST", path, {"user": user_id, "decision": DECISIONS[choice]})
        except urllib.error.HTTPError as error:
            return GONE if error.code == 404 else "The search gateway is not available right now."
        except Exception:
            return "The search gateway is not available right now."

        hours = f"{approval.get('session_hours') or 8:g}"
        reply = {
            ONCE: "Approved once. Go back and write *continue*: the same search now runs once.",
            SESSION: f"Approved for this session. Searches from the same place run without asking "
            f"for the next {hours} hours. Go back and write *continue*.",
            DECLINE: "Declined. Nothing was sent.",
        }[choice]
        if approval["chat"]:  # the search came from a chat: offer the way back
            reply += f"\n\n[Back to the chat](/c/{urllib.parse.quote(approval['chat'], safe='')})"
        return reply
