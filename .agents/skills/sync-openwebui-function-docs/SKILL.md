---
name: sync-openwebui-function-docs
description: Keep openwebui-functionality/README.md synchronized whenever Python files in openwebui-functionality/functions/ are added, modified, renamed, or removed. Apply as part of the function implementation task, without waiting for a separate documentation request. Excludes unrelated deployment changes and Python functions elsewhere in the repository.
---

# Sync Open WebUI function documentation

Maintain `openwebui-functionality/README.md` in the same task as changes to
`openwebui-functionality/functions/`. All paths below are relative to the
repository root. This workflow covers Filters, Actions, Events, and Valves.

## Determine what changed

Read the current README, the affected function files, and relevant tests. Inspect
staged and unstaged diffs and newly added files so a new function is not missed.
Distinguish the current task's changes from pre-existing user work, preserving
existing README edits. Do not infer behavior solely from filenames or comments.

## Update the README

Keep it short and factual. An administrator should learn what each function does
and how to configure it at a glance; anything beyond that is noise.

- **New function:** add a relative source link, its metadata title, its type
  (Filter, Action, Pipe, Event), and a one- or two-sentence purpose under the
  matching Components subsection. Support modules such as `__init__.py` need no
  entry.
- **Changed function:** update only the facts that changed. For renames or
  removals, fix or delete stale links and guidance.
- **Configuration:** list Valves and environment variables in a compact table or
  list (name, default, one-line meaning). Keep Valves and environment variables
  distinct and verify names and defaults against the code.
- **Special details:** mention only what an administrator would otherwise get
  wrong, such as required dependencies, Global vs. enabled, endpoints, external
  effects, conflicts with other functions, or restart requirements. One line each.
- **Testing:** update only when commands or dependencies change.

Avoid restating code, walking through internals, repeating shared installation
steps, or adding background explanation, examples, or rationale unless they are
essential. Prefer bullets over paragraphs. Add a dedicated section only when a
function cannot be described in its inventory entry and a short configuration
list. Preserve the README's organization and use placeholder secrets and example
addresses.

Always assess documentation after a function change. If the change is purely
internal and the README remains accurate and complete, leave it unchanged and
briefly explain that result in the task summary. Do not add timestamps or
wording-only edits merely to produce a README diff. Record the change in
`CHANGELOG.md` as described in the repository guidance, not in the README.

## Verify and finish

Check affected source links and section anchors. Cross-check new defaults and
examples against the final implementation, review the focused README diff, and
run `git diff --check`. Do not import or execute a function merely to discover its
configuration; it may register routes or perform external actions.

Summarize the README changes alongside the implementation and report any
unresolved documentation uncertainty. This skill edits repository documentation;
it does not install Functions in Open WebUI or deploy services.
