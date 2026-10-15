"""
title: Web search with approval
description: Searches the web through the search gateway. Nothing is sent until the user approves the search, right here in the chat.
version: 1.0.0
"""

# Open WebUI's built-in web search is switched off, and this tool is the
# model's only way to search. Every search goes to the search gateway, which
# decides. When the gateway answers "approval needed", this tool asks the user
# in the same chat (approve once, approve for this session, decline), sends
# the answer to the gateway, and, if approved, gets the results straight
# away: no extra step for the user.
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

# Typed answers, in case an older Open WebUI shows a text box instead of the list.
TYPED = {"approve once": ONCE, "once": ONCE, "1": ONCE, "approve for this session": SESSION, "session": SESSION, "2": SESSION}


def to_choice(answer):
    """Turn the dialog's answer into ONCE, SESSION or DECLINE. Anything unclear counts as DECLINE."""
    return TYPED.get(str(answer or "").strip().lower(), DECLINE)


async def ask(event_call, query, session_hours, in_chat):
    """Ask the user in their browser. Returns ONCE, SESSION or DECLINE, or None if nobody can answer here.

    Uses Open WebUI's input dialog with a fixed list of choices. None means:
    no browser (an API call), or no answer before WEBSOCKET_EVENT_CALLER_TIMEOUT.
    """
    if not event_call:
        return None
    where = "this chat" if in_chat else "your terminals and agents"
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
    if isinstance(answer, dict):  # {"error": ...}: no browser here, or no answer in time
        return None
    return to_choice(answer)  # Cancel returns False, which counts as DECLINE


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

        try:
            results = await self._gateway("/search", search, user_id, chat_id)
            approval_id = results[0].get("approval_id") if results else None

            if approval_id:
                # Approval needed: ask right here in the chat.
                choice = await ask(__event_call__, query, results[0].get("session_hours"), in_chat=bool(chat_id))
                if choice is None:
                    # Nobody could answer here: the gateway's text carries the approval link.
                    return results[0]["snippet"]
                await self._gateway(f"/approvals/{approval_id}", {"user": user_id, "decision": DECISIONS[choice]})
                if __event_emitter__:  # a short note in the chat about what was decided
                    await __event_emitter__({"type": "status", "data": {"description": f"Web search: {choice}", "done": True}})
                if choice == DECLINE:
                    return f'The user declined the web search "{query}". Answer without web results.'
                # Approved: the gateway now runs the same search.
                results = await self._gateway("/search", search, user_id, chat_id)
        except urllib.error.HTTPError as error:
            if error.code == 404:  # the approval expired while the question was open
                return "The approval has expired. Run the search again to get a new one."
            return "Web search is not available right now."
        except Exception:
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
