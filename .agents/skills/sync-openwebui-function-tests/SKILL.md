---
name: sync-openwebui-function-tests
description: Review and update openwebui-functionality/tests/ whenever Python files in openwebui-functionality/functions/ are added, modified, renamed, or removed. Apply automatically during function implementation tasks to identify coverage gaps, update relevant tests, and run focused validation. Excludes unrelated deployment changes and Python functions elsewhere in the repository.
---

# Sync Open WebUI function tests

Assess whether changes to `openwebui-functionality/functions/` require updates in
`openwebui-functionality/tests/`, and make the necessary updates in the same task.
All paths below are relative to the repository root.

## Review the change and existing coverage

Read the affected implementation, relevant tests, `tests/conftest.py` inside
`openwebui-functionality/`, the component README, and `pyproject.toml`. Inspect
staged and unstaged diffs and new files. Separate the current task's changes from
pre-existing user work; preserve unrelated changes.

Identify changed observable behavior and map it to existing assertions. Check
whether tests still express the intended contract and whether they cover the
changed success, failure, and boundary cases. Do not assume that an unchanged test
file or a passing suite proves the new behavior is covered.

## Update tests when needed

- Extend the existing module under `tests/functions/`, or add
  `test_<function_module>.py` for a new function with testable behavior. Reuse
  existing fixtures; add shared fixtures only when multiple tests need them.
- For bug fixes, add a regression test that exposes the original failure where
  practical. For changed behavior, update obsolete expectations and add coverage
  for materially new cases. For renames or removals, adjust imports and remove
  tests only for contracts that intentionally no longer exist.
- Assert observable results and effects, such as returned data, raised errors,
  quota state, route responses, or external calls made or suppressed. Avoid tests
  that merely mirror implementation details or match source-code wording.
- Select cases based on the actual change: Valve validation and defaults,
  environment/saved-value precedence, event dispatch, authorization, token
  validity, quota boundaries, or error handling. These are examples, not a
  mandatory checklist for every edit.
- Mock Open WebUI database APIs, Gmail, and other external services before any
  path can access them. Use test-only credentials and local application instances.
  Keep environment variables, imported module stubs, routes, clocks, and mutable
  global state isolated between tests. Prefer controlled time over real sleeps.
- Follow the existing pytest and pytest-asyncio conventions. Use `AsyncMock` for
  awaited dependencies. Installable Function modules may require import stubs;
  the email verification tests demonstrate loading a standalone module with
  mocked Open WebUI APIs. Preserve real logic under test rather than mocking it
  away. Do not send emails, contact live databases, or start deployment services.

Always perform the coverage review. If existing tests already cover the change,
or it only changes comments or formatting, avoid artificial test edits and
briefly explain why no updates are needed.

## Validate and report

Use the configured Python environment; when PyCharm's Python environment tools
are available, resolve the interpreter through them before Python commands.
The repository's documented commands, from `openwebui-functionality/`, are:

```sh
# Example: validate an email verification change.
uv run --extra test pytest tests/functions/test_oi_email_verification_callback.py

# Run the component suite when shared fixtures, imports, dependencies, or
# interactions across functions are affected.
uv run --extra test pytest
```

Choose the relevant test files for other functions. Resolve task-related failures
and rerun the affected tests. Do not weaken assertions to hide a regression or
change unrelated code to repair a pre-existing failure. Report unavailable
dependencies or unrelated failures explicitly; never claim unrun tests passed.

Run `git diff --check`. Summarize the coverage reviewed, tests added or updated
(or why none were needed), commands and outcomes, and any remaining gaps. If test
commands, dependencies, or documented coverage change, apply the companion
`sync-openwebui-function-docs` skill as required by `AGENTS.md`.
