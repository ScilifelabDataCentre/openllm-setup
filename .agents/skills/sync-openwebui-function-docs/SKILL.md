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

- For a new installable function, add a relative source link and a concise purpose
  statement under the appropriate Components subsection. Use its metadata title
  and actual entry-point type. Support modules such as `__init__.py` do not need
  their own component entry.
- For an updated function, revise descriptions and instructions affected by its
  behavior. For renames or removals, update or remove obsolete links and guidance.
- Document setup prerequisites, installation and activation, dependencies, and
  supported Open WebUI capabilities when the change introduces or alters them.
  Distinguish enabling a Function from making a filter or action Global.
- Keep configurable Valves and deployment environment variables distinct. Verify
  names, types or accepted values, built-in defaults, environment defaults,
  precedence, and restart requirements against the implementation where relevant.
- Explain user-visible behavior and operational limits: event triggers, endpoints,
  permissions, external effects, and interactions with other functions, as needed
  for an administrator to configure and use the affected component.
- Update testing guidance only when commands, dependencies, or documented coverage
  change. Describe only coverage supported by existing tests.

Preserve the README's organization and relative links. Add a dedicated section
when a component needs more detail than its inventory entry; avoid duplicating
shared installation steps. Use placeholder secrets and example addresses.

Always assess documentation after a function change. If the change is purely
internal and the README remains accurate and complete, leave it unchanged and
briefly explain that result in the task summary. Do not add timestamps, changelog
entries, or wording-only edits merely to produce a README diff.

## Verify and finish

Check affected source links and section anchors. Cross-check new defaults and
examples against the final implementation, review the focused README diff, and
run `git diff --check`. Do not import or execute a function merely to discover its
configuration; it may register routes or perform external actions.

Summarize the README changes alongside the implementation and report any
unresolved documentation uncertainty. This skill edits repository documentation;
it does not install Functions in Open WebUI or deploy services.
