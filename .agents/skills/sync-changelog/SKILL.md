---
name: sync-changelog
description: Add a brief entry to CHANGELOG.md whenever any file in this repository is added, modified, renamed, or removed. Apply automatically as part of every change, without waiting for a separate request.
---

# Sync changelog

Record every repository change in `CHANGELOG.md` (repository root) in the same
task. Keep entries brief: one line, plain facts.

## Write the entry

1. Check `git status` and the diff to see what the task changed. Ignore
   pre-existing, unrelated local work.
2. Add one bullet under `## Unreleased`, in the matching subsection:
   `Added`, `Changed`, `Fixed`, or `Removed`. Create the subsection if missing.
3. Format: `- <Component>: <what changed>.` Use a short component prefix such as
   `Open WebUI`, `Helm`, `Embeddings`, `C3SE`, `SafeSpring`, `Monitoring`,
   `Scripts`, or `Repo`.
4. State the effect for users or operators, not the implementation. Name new
   settings or required actions (e.g. a new env var or restart) only if an
   operator must act on them.

## Keep it short

- One line per change, about 20 words maximum; wrap at 80 columns.
- If a change refines an unreleased entry, edit that line instead of adding one.
- Group related edits from one task into a single bullet.
- No dates, authors, commit hashes, PR links, or rationale.
- Skip pure formatting or typo fixes unless they are the whole change.

## Example

```markdown
## Unreleased

### Added

- Open WebUI: Registration Questionnaire Event collecting affiliation and
  planned use from pending users.

### Fixed

- Open WebUI: ALTCHA clearance now persists across tabs.
```

Run `git diff --check` before finishing.
