"""Tests for the "Search approvals" assistant (search-approval/search_approvals_pipe.py)."""

import asyncio
from urllib.parse import unquote


def open_link(pipe, message, user="user-1", browser=None):
    body = {"messages": [{"role": "user", "content": message}]}
    return asyncio.run(pipe.pipe(body, __user__={"id": user}, __event_call__=browser))


def new_approval(gateway, query, user="user-1", chat=""):
    status, reply = gateway.search({"Authorization": "Bearer test-gateway-key", "X-OpenWebUI-User-Id": user, "X-OpenWebUI-Chat-Id": chat}, {"query": query})
    assert status == 200
    return reply[0]


def test_valves_fall_back_to_the_settings_from_the_chart(pipe_module, monkeypatch):
    valves = pipe_module.Pipe().valves
    assert valves.GATEWAY_URL == "" and valves.API_KEY == "" and valves.ASK_TIMEOUT_SECONDS == 300
    monkeypatch.setenv("SEARCH_GATEWAY_URL", "http://search-gateway.openllm.svc.cluster.local:8000")
    monkeypatch.setenv("SEARCH_GATEWAY_API_KEY", "from-chart")
    assert pipe_module.gateway_settings(valves) == ("http://search-gateway.openllm.svc.cluster.local:8000", "from-chart")


def test_the_message_from_the_link_approves_once(pipe_module, gateway, browser):
    approval = new_approval(gateway, "agent search")
    message = unquote(approval["link"].split("&q=", 1)[1])
    assert message == f"Send this message to review web search request {approval['approval_id']}"
    box = browser("once")
    reply = open_link(pipe_module.Pipe(), message, browser=box)
    assert reply.startswith('Web search: "agent search"\n\nApproved once.')
    assert box.events[0]["type"] == "execute"
    assert gateway.answers == [(approval["approval_id"], "user-1", "once")]
    assert "Back to the chat" not in reply  # it came from a terminal, not a chat


def test_session_from_a_terminal(pipe_module, gateway, browser):
    approval = new_approval(gateway, "agent session")
    reply = open_link(pipe_module.Pipe(), approval["approval_id"], browser=browser("session"))
    assert "Approved for this session" in reply and "for the next 8 hours" in reply
    assert ("user-1", "") in gateway.sessions
    # The box says what such a session covers.
    box = browser("decline")
    other = new_approval(gateway, "another agent search", user="user-2")
    open_link(pipe_module.Pipe(), other["approval_id"], user="user-2", browser=box)
    assert "No more questions from your terminals and agents for 8 hours" in box.events[0]["data"]["code"]


def test_someone_elses_link_does_nothing(pipe_module, gateway, browser):
    approval = new_approval(gateway, "private search", user="user-1")
    box = browser("once")
    assert open_link(pipe_module.Pipe(), approval["approval_id"], user="intruder", browser=box) == pipe_module.GONE
    assert box.events == [] and gateway.answers == []


def test_an_answered_search_is_not_asked_again(pipe_module, gateway, browser):
    approval = new_approval(gateway, "answered")
    open_link(pipe_module.Pipe(), approval["approval_id"], browser=browser("once"))
    box = browser("decline")
    assert open_link(pipe_module.Pipe(), approval["approval_id"], browser=box) == "This search was already answered (allowed)."
    assert box.events == []


def test_closed_box_or_no_answer_decides_nothing(pipe_module, gateway, browser):
    approval = new_approval(gateway, "undecided")
    pipe = pipe_module.Pipe()
    assert "Closed without an answer" in open_link(pipe, approval["approval_id"], browser=browser("closed"))
    assert "No answer in time" in open_link(pipe, approval["approval_id"], browser=browser("timeout"))
    assert "could not be shown here" in open_link(pipe, approval["approval_id"], browser=None)
    assert gateway.answers == [] and gateway.waiting_ids() == [approval["approval_id"]]


def test_a_pasted_link_works(pipe_module, gateway, browser):
    approval = new_approval(gateway, "pasted link")
    reply = open_link(pipe_module.Pipe(), approval["link"], browser=browser("decline"))
    assert reply.endswith("Declined. Nothing was sent.")


def test_a_search_from_a_chat_offers_the_way_back(pipe_module, gateway, browser):
    approval = new_approval(gateway, "from a chat", chat="chat-9")
    reply = open_link(pipe_module.Pipe(), approval["approval_id"], browser=browser("once"))
    assert reply.endswith("[Back to the chat](/c/chat-9)")


def test_without_an_id_it_explains_itself(pipe_module, gateway):
    assert open_link(pipe_module.Pipe(), "hello") == pipe_module.HELP


def test_gateway_down(pipe_module, gateway, monkeypatch, closed_port, browser):
    approval = new_approval(gateway, "down")
    monkeypatch.setenv("SEARCH_GATEWAY_URL", f"http://127.0.0.1:{closed_port}")
    assert open_link(pipe_module.Pipe(), approval["approval_id"], browser=browser("once")) == "The search gateway is not available right now."
