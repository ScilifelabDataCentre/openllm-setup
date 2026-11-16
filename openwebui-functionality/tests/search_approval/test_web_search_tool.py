"""Tests for the "Web search with approval" tool (search-approval/web_search_tool.py)."""

import asyncio
import json
import re

USER = {"id": "user-1"}


def search(tool, query, chat_id="chat-1", browser=None, chat=None, user=USER):
    return asyncio.run(
        tool.web_search(query, __user__=user, __metadata__={"chat_id": chat_id}, __event_call__=browser, __event_emitter__=chat)
    )


def test_valves_fall_back_to_the_settings_from_the_chart(tool_module, monkeypatch):
    valves = tool_module.Tools().valves
    assert valves.GATEWAY_URL == "" and valves.API_KEY == "" and valves.RESULT_COUNT == 5 and valves.ASK_TIMEOUT_SECONDS == 300
    monkeypatch.delenv("SEARCH_GATEWAY_URL", raising=False)
    monkeypatch.delenv("SEARCH_GATEWAY_API_KEY", raising=False)
    assert tool_module.gateway_settings(valves) == ("http://search-gateway:8000", "")
    monkeypatch.setenv("SEARCH_GATEWAY_URL", "http://search-gateway.openllm.svc.cluster.local:8000/")
    monkeypatch.setenv("SEARCH_GATEWAY_API_KEY", "from-chart")
    assert tool_module.gateway_settings(valves) == ("http://search-gateway.openllm.svc.cluster.local:8000", "from-chart")
    valves.GATEWAY_URL, valves.API_KEY = "http://other:8000", "from-valve"  # a Valve that is set wins
    assert tool_module.gateway_settings(valves) == ("http://other:8000", "from-valve")


def test_api_key_valve_is_masked(tool_module):
    schema = tool_module.Tools.Valves.model_json_schema()["properties"]["API_KEY"]
    assert schema["input"] == {"type": "password"}


def test_approve_once_runs_the_search_once(tool_module, gateway, browser, chat):
    tool = tool_module.Tools()
    box = browser("once")
    results = json.loads(search(tool, "sepsis guidelines 2026", browser=box, chat=chat))
    assert [r["link"] for r in results] == [f"https://example.org/{n}" for n in range(5)]
    assert gateway.searches == ["sepsis guidelines 2026"]
    assert [e["type"] for e in box.events] == ["execute"]  # the three-button box
    assert chat.statuses == ["Web search: waiting for your approval", "Web search: Approve once"]
    assert len(chat.sources) == 5
    # Once means once: the same search asks again.
    box = browser("decline")
    search(tool, "sepsis guidelines 2026", browser=box)
    assert len(box.events) == 1 and gateway.searches == ["sepsis guidelines 2026"]


def test_approve_for_session_covers_that_chat_only(tool_module, gateway, browser):
    tool = tool_module.Tools()
    search(tool, "first question", browser=browser("session"))
    quiet = browser("decline")
    assert json.loads(search(tool, "second question", browser=quiet))  # same chat: no question
    assert quiet.events == [] and gateway.searches == ["first question", "second question"]
    other = browser("decline")
    search(tool, "second question", chat_id="chat-2", browser=other)  # another chat: asked again
    assert len(other.events) == 1


def test_decline_sends_nothing(tool_module, gateway, browser, chat):
    out = search(tool_module.Tools(), "patient 19800101-1234", browser=browser("decline"), chat=chat)
    assert "declined" in out and gateway.searches == []
    assert chat.statuses[-1] == "Web search: Decline"


def test_closing_the_box_sends_nothing_and_keeps_the_question(tool_module, gateway, browser, chat):
    tool = tool_module.Tools()
    out = search(tool, "closed box", browser=browser("closed"), chat=chat)
    assert "closed the approval box" in out and gateway.searches == []
    assert chat.statuses[-1] == "Web search: not sent (approval box closed)"
    waiting = gateway.waiting_ids()
    assert len(waiting) == 1  # still open, nothing decided
    assert gateway.answers == []
    # Asking again shows the same question, not a second one.
    search(tool, "closed box", browser=browser("once"))
    assert gateway.answers[0][0] == waiting[0] and gateway.searches == ["closed box"]


def test_no_answer_in_time_returns_the_link(tool_module, gateway, browser, chat):
    out = search(tool_module.Tools(), "nobody answers", browser=browser("timeout"), chat=chat)
    assert out.startswith("No answer to the approval question in time, so nothing was sent.")
    assert "/?model=search_approvals&q=Send%20this%20message" in out and gateway.searches == []
    assert chat.statuses[-1] == "Web search: not sent (no answer in time)"


def test_api_call_without_a_browser_returns_the_link(tool_module, gateway, chat):
    out = search(tool_module.Tools(), "from a terminal", chat_id="", browser=None, chat=chat)
    assert "Show the user this link" in out and "model=search_approvals" in out
    assert gateway.searches == [] and chat.statuses[-1] == "Web search: approval needed (link)"
    assert gateway.approvals[gateway.waiting_ids()[0]]["chat"] == ""


def test_open_webui_errors_count_as_no_answer_or_no_browser(tool_module, gateway, browser):
    tool = tool_module.Tools()
    timed_out = search(tool, "q1", browser=browser({"error": "Request timed out"}))
    assert timed_out.startswith("No answer to the approval question in time")
    disconnected = search(tool, "q2", browser=browser({"error": "Client session disconnected"}))
    assert disconnected.startswith("Show the user this link")
    assert gateway.searches == []


def test_box_that_cannot_be_drawn_falls_back_to_the_list(tool_module, gateway, browser):
    def answer(event):
        return {"dialog_error": "TypeError"} if event["type"] == "execute" else "Approve once"

    box = browser(answer)
    assert json.loads(search(tool_module.Tools(), "fallback", browser=box))
    fallback = box.events[1]
    assert fallback["type"] == "input" and fallback["data"]["value"] == "Decline"  # Decline preselected
    assert fallback["data"]["input"]["options"] == ["Decline", "Approve once", "Approve for this session"]


def test_unknown_or_cancelled_answers_count_as_decline(tool_module, gateway, browser):
    tool = tool_module.Tools()
    assert "declined" in search(tool, "odd answer", browser=browser("something else"))
    assert tool_module.TYPED.get("", tool_module.DECLINE) == tool_module.DECLINE  # Cancel returns False
    assert gateway.searches == []


def test_the_approval_expired_while_the_box_was_open(tool_module, gateway, browser):
    box = browser("once", before_answer=gateway.approvals.clear)
    assert search(tool_module.Tools(), "slow answer", browser=box) == "The approval has expired. Run the search again to get a new one."


def test_gateway_down_or_wrong_key_means_not_available(tool_module, gateway, browser, monkeypatch, closed_port, chat):
    tool = tool_module.Tools()
    monkeypatch.setenv("SEARCH_GATEWAY_API_KEY", "wrong")
    assert search(tool, "q", browser=browser("once"), chat=chat) == "Web search is not available right now."
    assert chat.statuses[-1] == "Web search: not available"
    monkeypatch.setenv("SEARCH_GATEWAY_URL", f"http://127.0.0.1:{closed_port}")
    assert search(tool, "q", browser=browser("once")) == "Web search is not available right now."
    assert gateway.searches == []


def test_only_the_logged_in_user_is_sent_to_the_gateway(tool_module, gateway, browser):
    search(tool_module.Tools(), "whose search", browser=browser("closed"), user={"id": "user-42"})
    (approval,) = gateway.approvals.values()
    assert approval["user"] == "user-42" and approval["chat"] == "chat-1"


def test_the_box_shows_the_search_text_as_plain_text(tool_module):
    query = '</script><img src=x onerror=alert(1)> "quoted" åäö'
    event = tool_module.dialog_event(query, 8, "this chat", 300)
    code = event["data"]["code"]
    assert event["type"] == "execute" and "__DATA__" not in code
    data = json.loads(re.search(r"const data = (\{.*?\});\n", code).group(1))
    assert data["query"] == query and data["timeoutMs"] == 300_000
    assert data["sessionNote"] == "No more questions from this chat for 8 hours"
    assert "innerHTML" not in tool_module.DIALOG_JS and "textContent" in tool_module.DIALOG_JS
    assert query not in code  # only the escaped JSON form is in the script


def test_tool_and_assistant_share_the_same_box_code(tool_module, pipe_module):
    def box_code(path, end):
        text = open(path).read()
        part = text[text.index("# The three answers") : text.index(end)]
        return "\n".join(line for line in part.splitlines() if "Keep them the same in" not in line).strip()

    assert box_code(tool_module.__file__, "def as_text(results):") == box_code(pipe_module.__file__, "def approval_id_in(text):")
