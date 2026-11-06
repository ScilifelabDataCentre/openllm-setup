#!/usr/bin/env bash
# Keep .claude/skills/ in sync with the canonical skills under .agents/skills/.
#
# .agents/skills/ is the source of truth (shared with other agent tools, e.g.
# .codex/). This script makes .claude/skills/<name> a symlink to
# ../../.agents/skills/<name> for every skill directory, and removes any
# symlink in .claude/skills/ that no longer has a matching source skill.
#
# Safe to re-run; only touches symlinks it manages (ones that resolve into
# .agents/skills/), never a real directory a skill-creation flow left behind.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SOURCE_DIR="$REPO_ROOT/.agents/skills"
TARGET_DIR="$REPO_ROOT/.claude/skills"

mkdir -p "$TARGET_DIR"

# Create/update a symlink for every skill directory under .agents/skills/.
if [ -d "$SOURCE_DIR" ]; then
  for skill_dir in "$SOURCE_DIR"/*/; do
    [ -d "$skill_dir" ] || continue
    name="$(basename "$skill_dir")"
    link="$TARGET_DIR/$name"
    target="../../.agents/skills/$name"

    if [ -L "$link" ]; then
      current_target="$(readlink "$link")"
      if [ "$current_target" != "$target" ]; then
        ln -sfn "$target" "$link"
        echo "Updated symlink: .claude/skills/$name -> $target"
      fi
    elif [ -e "$link" ]; then
      echo "Skipping .claude/skills/$name: exists and is not a symlink" >&2
    else
      ln -s "$target" "$link"
      echo "Created symlink: .claude/skills/$name -> $target"
    fi
  done
fi

# Remove stale symlinks: ones that point into .agents/skills/ but whose
# source skill directory no longer exists.
if [ -d "$TARGET_DIR" ]; then
  for link in "$TARGET_DIR"/*/; do
    [ -L "${link%/}" ] || continue
    name="$(basename "$link")"
    current_target="$(readlink "${link%/}")"
    case "$current_target" in
      ../../.agents/skills/*)
        if [ ! -d "$SOURCE_DIR/$name" ]; then
          rm "${link%/}"
          echo "Removed stale symlink: .claude/skills/$name"
        fi
        ;;
    esac
  done
fi
