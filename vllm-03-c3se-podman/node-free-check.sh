#!/usr/bin/env bash
# node-free-check.sh <own-container-name>
#
# Exits 0 if this node is free for a vLLM to start, non-zero if another one is
# already here. Called from ExecStartPre in vllm@.service and from
# multi-node/vllm-multinode.sh, so the rule lives in one place.
#
# The node allocation table in the README is the policy; this is the enforcement.
# Without it, a second vLLM on the same node produces an out-of-memory error on one
# rank, surfaced as a NCCL error reported by the others, several screens from the
# cause.
#
# It matches on the container IMAGE as well as the name, because a container started
# by hand with `podman run` and no --name gets an arbitrary name like
# "compassionate_tesla" and would otherwise be invisible.
#
# Deliberately does NOT check nvidia-smi for GPU memory. A fast restart can race with
# memory still being released, and a guard that false-positives gets deleted.
#
# Set ALLOW_SHARED_NODE=1 in the model's conf to override.
set -uo pipefail

SELF="${1:?usage: $(basename "$0") <own-container-name>}"

[ "${ALLOW_SHARED_NODE:-0}" = "1" ] && exit 0

# Fields are space separated; neither a container name nor an image reference can
# contain a space, so awk's default splitting is safe here.
others="$(podman ps --format '{{.Names}} {{.Image}}' 2>/dev/null \
  | awk -v self="$SELF" '$1 != self && ($1 ~ /^vllm-/ || $2 ~ /vllm/)')"

[ -z "$others" ] && exit 0

{
  echo "FATAL: another vLLM container is already running on $(hostname):"
  echo "$others" | sed 's/^/         /'
  echo "       A node runs either single-node models or one multi-node deployment,"
  echo "       never both. See the node allocation table in the README."
  echo ""
  echo "       If it is a leftover:      podman rm -f <name>"
  echo "       If it belongs elsewhere:  stop whichever does not belong on this node"
  echo "       If this is deliberate:    set ALLOW_SHARED_NODE=1 in the conf"
} >&2

exit 78
