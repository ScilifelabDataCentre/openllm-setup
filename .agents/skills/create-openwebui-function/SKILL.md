---
name: create-openwebui-function
description: Create a new Open WebUI Function (Pipe, Filter, Action, or Event) under openwebui-functionality/functions/, following the upstream Open WebUI plugin contract and this repository's conventions for metadata, Valves, deployment settings, tests, and README. Use when asked to add, scaffold, or design a new installable Open WebUI Function. Excludes Open WebUI Tools, changes to existing Functions that do not add a new one, and deployment manifests.
---

# Create an Open WebUI Function

Functions are single Python files that an administrator pastes into Open WebUI
(**Admin Panel > Functions > Create**). Open WebUI stores the source in its
database, `exec()`s it into a temporary module, and runs it server-side with full
access to the backend. All paths below are relative to the repository root.

Upstream reference: <https://docs.openwebui.com/features/extensibility/plugin/functions/>.
Type-specific pages: `.../functions/pipe`, `.../functions/filter`,
`.../functions/action`, `.../functions/event`; shared pages:
`.../plugin/development/valves`, `.../plugin/development/reserved-args`,
`.../plugin/development/events`. Fetch the relevant page when a detail below is
insufficient or may be outdated. The deployed image is a SciLifeLab fork
(`image:` in `openwebui-kth-cluster-helm/values.yaml`); when an internal API
matters, verify it against that version's source rather than upstream docs alone.

## 1. Choose the type

Open WebUI detects the type from the first matching top-level class name.

| Need | Type | Entry point |
| --- | --- | --- |
| New model, agent, provider proxy, or non-LLM chat interface | `class Pipe` | `pipe()`; optional `pipes()` for several models |
| Inspect, modify, block, or log traffic to existing models | `class Filter` | `inlet()`, `request()`, `stream()`, `outlet()` |
| Button on chat messages | `class Action` | `action()` |
| React to system events or register HTTP routes (no chat UI) | `class Event` | `event()` |

Prefer a Tool (not covered here) when the model itself should call live data
during inference. Check existing Functions and the README first: a new Function
should not duplicate or conflict with an active one (for example, Request Quota
Guard and Rate Limit Enforcer are mutually exclusive filters).

## 2. Create the file

Name it `openwebui-functionality/functions/oi_<snake_case_name>.py`. Start the
file with the frontmatter docstring (it must be the first line), matching the
existing Functions:

```python
"""
title: Human Readable Title
oi_type: filter
function_id: snake_case_id
description: One sentence on what it does.
author: SciLifeLab
version: 0.1.0
requirements: httpx
"""
```

- `title`, `description`, `version`, `author`, `requirements`, `icon_url`, and
  `required_open_webui_version` are read by Open WebUI. `oi_type` and
  `function_id` are repository conventions; use `function_id` as the ID entered
  in the admin UI (alphanumeric and underscores, stored lowercase).
- `requirements` is a comma-separated pip list installed on save and first load
  unless `ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS` is off or `OFFLINE_MODE` is
  on. List every third-party package not already in the image. A package
  installed by hand that is missing from `requirements` makes the Function
  deactivate itself after a restart.
- Document required deployment environment variables, setup prerequisites, and
  Valve environment defaults in the docstring as well, as in
  `oi_email_verification_callback.py`.
- Open WebUI rewrites `from utils`, `from apps`, `from main`, and `from config`
  to `open_webui.*` across the whole file, including docstrings and prompt text.
  Avoid those strings; import `open_webui.*` explicitly.
- Import only the standard library, packages in `requirements`, and
  `open_webui.*`. Never import repository-local modules: the file is installed
  alone.
- Target Python 3.10+, use `async def` for entry points, and keep the event loop
  free: use `httpx.AsyncClient` or wrap blocking calls in `asyncio.to_thread`.
- Use `logging.getLogger(__name__)` (or the `oi.<name>` pattern already used),
  never log secrets or full message content, and avoid `print`.

## 3. Implement the entry point

Declare only the reserved arguments the method needs; Open WebUI injects them
by name, and only those valid for the type (see the reserved-args page).
Commonly used: `__user__` (dict; `__user__["valves"]` is a Pydantic object, so
use attribute access), `__metadata__`, `__model__`, `__request__`,
`__event_emitter__`, `__event_call__`, `__chat_id__` (Pipes and Filter
`inlet`/`request` only), `__files__` (Pipes), and `__id__` (Filters, Actions,
Events). `__event_emitter__` and `__event_call__` can be `None` for direct API
calls; guard them.

### Pipe

```python
class Pipe:
    class Valves(BaseModel):
        MODEL_ID: str = Field(default="")

    def __init__(self):
        self.valves = self.Valves()

    async def pipe(self, body: dict, __user__: dict | None = None):
        return "Hello"
```

- Return a `str`, a sync or async generator for streaming, a `dict`, a Pydantic
  model, or a `StreamingResponse`.
- For several models, define `pipes()` returning `[{"id": ..., "name": ...}]`;
  each model ID becomes `<function_id>.<id>` (split on the first dot). An
  exception or a missing key in `pipes()` removes all of the Function's models.
- Raise to show an error; returning an error string renders as normal text.
- Do not emit `delta.tool_calls` from a self-contained agent; Open WebUI would
  loop back into the pipe.

### Filter

```python
class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Lower values run first.")

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __user__: dict | None = None) -> dict:
        return body

    async def outlet(self, body: dict) -> dict:
        return body
```

- `inlet` runs once per user turn before the model; `request` runs before every
  model call, including each tool-loop step (keep it idempotent); `stream`
  receives each chunk as `event`; `outlet` receives the completed chat.
- Always return the body or event; returning `None` breaks the next filter.
  Raise an exception to reject a request in `inlet`.
- Optional `__init__` attributes: `self.toggle = True` for a per-chat user
  switch, `self.icon = "<hosted URL>"`, `self.file_handler = True` to skip
  built-in RAG (then `inlet` must handle retrieval).
- `outlet` does not change direct API responses; `stream` exceptions drop the
  chunk.

### Action

```python
class Action:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Lower values appear first.")

    def __init__(self):
        self.valves = self.Valves()

    async def action(self, body: dict, __user__=None, __event_emitter__=None, __event_call__=None):
        return {"messages": [{"id": body["id"], "content": "Updated content"}]}
```

- `body["id"]` is the clicked message. Return `{"messages": [...]}` to change
  messages; a bare `{"content": ...}` is ignored. Returning nothing is fine for
  side effects.
- For several buttons, define an `actions` class attribute of
  `{"id", "name", "icon_url"}` entries (IDs without dots) and branch on `__id__`.
- The action runs inside the HTTP request: keep it short, report progress with
  `__event_emitter__`, and check `__event_call__` results for an `error` key.

### Event

```python
class Event:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def event(
        self,
        event: dict,
        __event_id__: str | None = None,
        __event_name__: str | None = None,
        __id__: str | None = None,
        __app__=None,
        __request__=None,
    ):
        if __event_name__ == "auth.signup":
            ...
```

- Every active Event Function receives every event; filter on `__event_name__`
  (`<area>.<action>`, e.g. `system.startup.completed`, `auth.signup`,
  `user.updated`; the full list is in **Admin Settings > General > Events**).
  For plugin toggle events, compare the subject ID with `__id__`.
- Events have `Valves` but no `UserValves`. Handlers run after the triggering
  action, cannot block it, are not retried, and have no exactly-once guarantee:
  make side effects idempotent.
- Startup events fire on every replica and worker, and enabling a Function does
  not replay startup. Make setup safe to run repeatedly and on any event, as
  `oi_email_verification_callback.py` does for route registration. Custom HTTP
  routes bypass the dispatcher, so reload saved Valves inside them, place them
  before Open WebUI's SPA `Mount("/")`, and remove stale copies on reload.

## 4. Configuration: Valves vs. deployment settings

- `Valves` (admin, **Functions > gear icon**) hold operational knobs.
  `UserValves` hold per-user preferences (not for Events). Both are nested
  Pydantic `BaseModel` classes; give every field a default and a description.
- Use `Literal`/enum for selects, `bool` for switches, and
  `json_schema_extra={"input": {"type": "password"}}` on `str` fields that hold
  user-entered secrets. Password input only masks the UI; values are plaintext
  unless `ENABLE_VALVE_ENCRYPTION=true`.
- Add `field_validator`s for anything that must be well-formed (durations,
  header text, URLs). Saving code is refused if stored Valve values no longer
  validate, so plan renames and type changes as migrations.
- Keep credentials, mail identities, and external endpoints in deployment
  environment variables supplied from Kubernetes Secrets, not in Valves. When a
  required variable is missing, stay inactive and log one warning instead of
  failing or acting partially.
- Where useful, default a Valve from an optional environment variable
  (`default=os.getenv("NAME", "fallback")`); saved values then take precedence.
  Set `model_config = ConfigDict(validate_default=True)` so environment defaults
  are validated too.
- Preserve entry-point class names, injected argument names, and Valve field
  names once deployed; administrators' saved values and installations depend on
  them.

## 5. Tests and documentation

Apply the companion skills before finishing:

- [sync-openwebui-function-tests](../sync-openwebui-function-tests/SKILL.md): add
  `openwebui-functionality/tests/functions/test_oi_<name>.py`. Load the file
  standalone with `importlib`, stubbing every `open_webui.*` module it imports in
  `sys.modules` (see `tests/functions/test_oi_email_verification_callback.py`).
  Mock external services; cover Valve defaults and validation, the main success
  path, rejection paths, and any routes or events.
- [sync-openwebui-function-docs](../sync-openwebui-function-docs/SKILL.md): add
  the Function to the matching Components subsection of
  `openwebui-functionality/README.md`, plus a dedicated section for setup,
  environment variables, Valves, and behavior when it needs one.

If new test-only dependencies are required, add them to the `test` extra in
`openwebui-functionality/pyproject.toml` and update `uv.lock` deliberately.

## 6. Validate and report

From `openwebui-functionality/`:

```sh
uv run --extra test pytest tests/functions/test_oi_<name>.py
uv run --extra test pytest
```

Run `git diff --check` from the repository root. Do not install the Function on a
live Open WebUI instance, send real external requests, or touch deployment
databases as validation. In the summary, state the type and Function ID, the
installation steps the administrator must take (create, paste, save, enable;
Global for Filters and Actions that should apply to all models; any groups,
environment variables, or Valves to configure; replica restarts for route
registration), tests run, and anything that still needs runtime verification.
