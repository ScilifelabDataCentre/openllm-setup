"""Shared fixtures for the search approval tool and assistant.

The tool and the assistant talk to the search gateway over HTTP. FakeGateway
speaks the same protocol as the real gateway (openllm-search-gateway), keeps its
state in memory, and listens on 127.0.0.1 only, so the real urllib code paths
run while nothing leaves the machine.
"""

import importlib.util
import json
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import pytest

SEARCH_APPROVAL = Path(__file__).resolve().parents[2] / "search-approval"
OPENWEBUI_URL = "https://openwebui.example.org"
KEY = "test-gateway-key"


class FakeGateway:
    """In-memory stand-in for the gateway: approvals, sessions, and searches that "went out"."""

    def __init__(self):
        self.lock = threading.Lock()
        self.approvals = {}  # approval id -> {"user", "query", "chat", "status"}
        self.sessions = set()  # (user, chat) pairs inside an approved session
        self.searches = []  # every search that reached the "search engine"
        self.answers = []  # every decision recorded: (approval id, user, decision)

    # --- replies, shaped like the real gateway's ---
    @staticmethod
    def message(title, text, **extra):
        return [{"link": OPENWEBUI_URL, "title": title, "snippet": text, "message": True, **extra}]

    def waiting(self, rid, query):
        link = f"{OPENWEBUI_URL}/?model=search_approvals&q={quote(f'Send this message to review web search request {rid}')}"
        return self.message(
            "Approval needed: nothing was sent",
            f"Show the user this link to review the search in Open WebUI: {link} "
            f'When they have approved it, run exactly the same search again: "{query}". '
            "The link expires in 15 minutes.",
            link=link,
            approval_id=rid,
            session_hours=8.0,
        )

    def results(self, query, count):
        self.searches.append(query)
        return [
            {"link": f"https://example.org/{n}", "title": f"Result {n}", "snippet": f"About {query}"}
            for n in range(count)
        ]

    # --- endpoints ---
    def search(self, headers, body):
        if headers.get("Authorization") != f"Bearer {KEY}":
            return 401, {"detail": "Wrong or missing API key"}
        user = headers.get("X-OpenWebUI-User-Id") or "unknown"
        chat = headers.get("X-OpenWebUI-Chat-Id") or ""
        query, count = str(body.get("query", "")).strip(), int(body.get("count", 5))
        if (user, chat) in self.sessions:
            return 200, self.results(query, count)
        rid = next((r for r, a in self.approvals.items() if a["user"] == user and a["query"] == query), None)
        if rid:
            status = self.approvals[rid]["status"]
            if status == "waiting":
                return 200, self.waiting(rid, query)
            del self.approvals[rid]
            if status == "denied":
                return 200, self.message("Search declined", f'The user declined the search "{query}".')
            return 200, self.results(query, count)
        rid = secrets.token_urlsafe(16)
        self.approvals[rid] = {"user": user, "query": query, "chat": chat, "status": "waiting"}
        return 200, self.waiting(rid, query)

    def show(self, headers, rid, user):
        if headers.get("Authorization") != f"Bearer {KEY}":
            return 401, {"detail": "Wrong or missing API key"}
        a = self.approvals.get(rid)
        if not a or a["user"] != user:
            return 404, {"detail": "Not found"}
        return 200, {"query": a["query"], "chat": a["chat"], "status": a["status"], "session_hours": 8.0}

    def answer(self, headers, rid, body):
        if headers.get("Authorization") != f"Bearer {KEY}":
            return 401, {"detail": "Wrong or missing API key"}
        a = self.approvals.get(rid)
        user, decision = str(body.get("user", "")), body.get("decision")
        if not a or a["user"] != user:
            return 404, {"detail": "Not found"}
        if a["status"] != "waiting":
            return 200, {"status": a["status"]}
        self.answers.append((rid, user, decision))
        if decision == "once":
            a["status"] = "allowed"
            return 200, {"status": "allowed"}
        if decision == "session":
            self.sessions.add((user, a["chat"]))
            del self.approvals[rid]
            return 200, {"status": "session"}
        if decision == "decline":
            a["status"] = "denied"
            return 200, {"status": "denied"}
        return 400, {"detail": "decision must be once, session or decline"}

    def waiting_ids(self, user=None):
        return [r for r, a in self.approvals.items() if a["status"] == "waiting" and user in (None, a["user"])]


def serve(gateway):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path.startswith("/approvals/"):
                user = parse_qs(url.query).get("user", [""])[0]
                with gateway.lock:
                    self.reply(*gateway.show(self.headers, url.path.rsplit("/", 1)[1], user))
            else:
                self.reply(404, {"detail": "Not Found"})

        def do_POST(self):
            url = urlparse(self.path)
            body = self.body()
            with gateway.lock:
                if url.path == "/search":
                    self.reply(*gateway.search(self.headers, body))
                elif url.path.startswith("/approvals/"):
                    self.reply(*gateway.answer(self.headers, url.path.rsplit("/", 1)[1], body))
                else:
                    self.reply(404, {"detail": "Not Found"})

        def log_message(self, *args):  # keep test output quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server


@pytest.fixture
def gateway(monkeypatch):
    """A running FakeGateway. Open WebUI's environment points at it, as the Helm chart would."""
    fake = FakeGateway()
    server = serve(fake)
    monkeypatch.setenv("SEARCH_GATEWAY_URL", f"http://127.0.0.1:{server.server_address[1]}/")
    monkeypatch.setenv("SEARCH_GATEWAY_API_KEY", KEY)
    yield fake
    server.shutdown()
    server.server_close()


@pytest.fixture
def closed_port():
    """A local port with nothing listening on it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load(filename, name):
    """Load a file the way an admin pastes it into Open WebUI: on its own, not as part of a package."""
    spec = importlib.util.spec_from_file_location(name, SEARCH_APPROVAL / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool_module():
    return load("web_search_tool.py", "web_search_tool_under_test")


@pytest.fixture
def pipe_module():
    return load("search_approvals_pipe.py", "search_approvals_pipe_under_test")


class Browser:
    """Stands in for the user's browser: answers the approval box with a fixed value."""

    def __init__(self, answer, before_answer=None):
        self.answer, self.before_answer, self.events = answer, before_answer, []

    async def __call__(self, event):
        self.events.append(event)
        if self.before_answer:
            self.before_answer()
        return self.answer(event) if callable(self.answer) else self.answer


class Chat:
    """Collects what the tool shows in the chat: status notes and sources."""

    def __init__(self):
        self.events = []

    async def __call__(self, event):
        self.events.append(event)

    @property
    def statuses(self):
        return [e["data"]["description"] for e in self.events if e["type"] == "status"]

    @property
    def sources(self):
        return [e for e in self.events if e["type"] == "source"]


@pytest.fixture
def browser():
    """Make a browser that answers the box: browser("once"), browser("closed"), browser(None) ..."""
    return Browser


@pytest.fixture
def chat():
    return Chat()
