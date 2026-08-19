#!/usr/bin/env bash
# vllm-multinode.sh <deployment-name>
#
# Run the SAME command on every node in the deployment. The script finds this node's
# own InfiniBand address, compares it to the head address in the conf, and takes the
# head or worker role accordingly. There is no per-node editing and no per-node
# argument, which is what keeps a 3, 4 or 5 node deployment from hand-copying.
#
# Lives in the git checkout on Mimer and is read from there by every node, so there
# is one copy and it cannot drift. Paths are derived from this script's own location,
# so the checkout can sit anywhere.
set -euo pipefail

NAME="${1:?usage: $(basename "$0") <deployment-name>}"
ROOT="${MULTINODE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
CONF="$ROOT/conf/${NAME}.conf"

# Always this script's own directory, even when MULTINODE_ROOT overrides ROOT, so the
# shared guard is found regardless.
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$SELF_DIR/../node-free-check.sh"

die() { echo "FATAL: $*" >&2; exit 78; }

[ -r "$CONF" ] || die "cannot read conf: $CONF"
# shellcheck source=/dev/null
source "$CONF"

: "${MULTINODE_NODES:?MULTINODE_NODES not set in $CONF}"
: "${PODMAN_IMAGE:?PODMAN_IMAGE not set in $CONF}"
: "${HF_MODEL:?HF_MODEL not set in $CONF}"
: "${MODEL_NAME:?MODEL_NAME not set in $CONF}"

MULTINODE_IB_PREFIX="${MULTINODE_IB_PREFIX:-10.52.}"
MULTINODE_IB_IFNAME="${MULTINODE_IB_IFNAME:-ibs1}"
MIMER="${MIMER:-/mimer/NOBACKUP/sll_dc}"
RAY_PORT="${RAY_PORT:-6379}"
RAY_CPUS="${RAY_CPUS:-16}"
VLLM_NGPUS="${VLLM_NGPUS:-4}"
VLLM_PORT="${VLLM_PORT:-8000}"
SHM_SIZE="${SHM_SIZE:-32g}"
NCCL_DEBUG="${NCCL_DEBUG:-WARN}"
CLUSTER_WAIT_SECS="${CLUSTER_WAIT_SECS:-900}"

# --- which commit is this? --------------------------------------------------
repo_commit() {
  local d="$ROOT" g head c
  for _ in 1 2 3 4; do
    [ -e "$d/.git" ] && break
    d="$(dirname "$d")"
  done
  g="$d/.git"
  [ -e "$g" ] || { echo "not-a-checkout"; return; }

  if command -v git >/dev/null 2>&1; then
    c="$(git -C "$d" rev-parse --short=8 HEAD 2>/dev/null)" || { echo unknown; return; }
    if [ -n "$(git -C "$d" status --porcelain 2>/dev/null)" ]; then
      echo "${c}-DIRTY"      # uncommitted changes: the stamp does not describe what ran
    else
      echo "$c"
    fi
    return
  fi

  # Fallback if git is ever absent again. Marked unverified, because reading HEAD
  # alone cannot tell whether the working tree matches it.
  head="$(cat "$g/HEAD" 2>/dev/null)" || { echo unknown; return; }
  case "$head" in
    ref:*) echo "$(cut -c1-8 < "$g/${head#ref: }" 2>/dev/null || echo unknown)-unverified" ;;
    *)     printf '%.8s-unverified\n' "$head" ;;
  esac
}

# --- derive the topology from the node list ---------------------------------
# The first address in MULTINODE_NODES is the head. Everything else follows from the
# count, so adding a node means editing exactly one line of the conf.
read -r -a NODES <<< "$MULTINODE_NODES"
HEAD_IP="${NODES[0]}"
NODE_COUNT="${#NODES[@]}"
TOTAL_GPUS=$(( NODE_COUNT * VLLM_NGPUS ))

# TP stays inside a node, over NVLink. PP spans nodes. Override in the conf only for
# a deliberate experiment.
VLLM_TP="${VLLM_TP:-$VLLM_NGPUS}"
VLLM_PP="${VLLM_PP:-$NODE_COUNT}"

# --- work out who we are ----------------------------------------------------
# Refuse to guess if a node ever has more than one matching address, rather than
# silently taking the first and producing an asymmetric cluster.
mapfile -t IB_ADDRS < <(hostname -I | tr ' ' '\n' | grep "^${MULTINODE_IB_PREFIX}" || true)
case "${#IB_ADDRS[@]}" in
  0) die "no ${MULTINODE_IB_PREFIX}* address on $(hostname). Is InfiniBand up? Check: hostname -I" ;;
  1) NODE_IP="${IB_ADDRS[0]}" ;;
  *) die "more than one ${MULTINODE_IB_PREFIX}* address on $(hostname): ${IB_ADDRS[*]}
       Refusing to guess which one the cluster should use. Narrow MULTINODE_IB_PREFIX
       in $CONF until exactly one matches." ;;
esac

in_list=0
for n in "${NODES[@]}"; do [ "$n" = "$NODE_IP" ] && in_list=1; done
[ "$in_list" = 1 ] || die "this node ($(hostname), $NODE_IP) is not in MULTINODE_NODES for '$NAME'"

if [ "$NODE_IP" = "$HEAD_IP" ]; then ROLE=head; else ROLE=worker; fi

CNAME="vllm-multinode-${NAME}"

# --- refuse to share a node with another vLLM ------------------------------
# Shared with vllm@.service, so the rule lives in one place. Also covers the RAY_PORT
# and VLLM_PORT collisions that two deployments sharing a head node would hit.
# systemd removes this deployment's own container in ExecStartPre, so a restart does
# not trip the guard; the check excludes it by name in any case.
[ -x "$GUARD" ] || die "guard script missing or not executable: $GUARD"
ALLOW_SHARED_NODE="${ALLOW_SHARED_NODE:-0}" "$GUARD" "$CNAME" || exit 78

echo "[launch] deployment=$NAME role=$ROLE node=$(hostname)/$NODE_IP"
echo "[launch] nodes=$NODE_COUNT head=$HEAD_IP total_gpus=$TOTAL_GPUS TP=$VLLM_TP PP=$VLLM_PP"
echo "[launch] image=$PODMAN_IMAGE rdma=${USE_RDMA:-0}"
echo "[launch] config=$CONF commit=$(repo_commit)"

args=(
  --name "$CNAME" --replace --rm
  --gpus all
  --net host
  --device /dev/infiniband
  --shm-size="$SHM_SIZE"

  # THE fix for the original silent hang. Podman defaults --pids-limit to 2048, and
  # the cgroup pids controller counts threads rather than processes, so Ray plus vLLM
  # exhausted it and Ray could not create its GCS listener thread. The symptom was
  # total silence after "Connected to Ray cluster": no logs, no GPUs claimed, nothing
  # in any journal. Do not remove.
  --pids-limit=-1

  # Lets py-spy attach if something ever stalls again. Cheap insurance.
  --cap-add=SYS_PTRACE

  # 524288 is the hard cap on these nodes. On a host with a lower one, podman will
  # refuse to start: check `ulimit -Hn` there and lower this to match.
  --ulimit nofile=524288:524288

  # No --ulimit memlock here on purpose. Chalmers raised the limit for llm on
  # 2026-08-14 (1 TiB via limits.conf), and the container now inherits "unlimited",
  # so the flag is unnecessary. It was also useless before that: rootless podman
  # cannot exceed a hard limit the user does not hold, so it was silently clamped.

  -e MN_ROLE="$ROLE"
  -e MN_NODE_IP="$NODE_IP"
  -e MN_HEAD_IP="$HEAD_IP"
  -e MN_RAY_PORT="$RAY_PORT"
  -e MN_RAY_CPUS="$RAY_CPUS"
  -e MN_GPUS="$VLLM_NGPUS"
  -e MN_TOTAL_GPUS="$TOTAL_GPUS"
  -e MN_WAIT_SECS="$CLUSTER_WAIT_SECS"

  # Per node, never shared. Each rank must advertise its own address, or ranks on
  # other nodes tell the group to reach them at the head and init hangs.
  -e VLLM_HOST_IP="$NODE_IP"

  # Interface names, identical on every node. Set here at podman run time so every
  # process in the container inherits them, including the workers Ray spawns later.
  # Exporting these in a shell reaches only that shell, never the other node.
  -e NCCL_SOCKET_IFNAME="$MULTINODE_IB_IFNAME"
  -e GLOO_SOCKET_IFNAME="$MULTINODE_IB_IFNAME"

  -e NCCL_DEBUG="$NCCL_DEBUG"
  -e PYTHONUNBUFFERED=1
  -e RAY_DEDUP_LOGS=0

  -v "$MIMER:$MIMER"
)

# Transport. USE_RDMA=0 sends cross-node NCCL over TCP on the InfiniBand fabric;
# USE_RDMA=1 uses RDMA. See conf comments and the Confluence page.
if [ "${USE_RDMA:-0}" = "1" ]; then
  args+=( -e NCCL_IB_HCA="${NCCL_IB_HCA:-mlx5_0}" )
  [ -n "${NCCL_NET_GDR_LEVEL:-}" ] && args+=( -e NCCL_NET_GDR_LEVEL="$NCCL_NET_GDR_LEVEL" )
else
  args+=( -e NCCL_IB_DISABLE=1 )
fi

# Only the head needs the model configuration.
if [ "$ROLE" = "head" ]; then
  args+=(
    -e MN_HF_MODEL="$HF_MODEL"
    -e MN_MODEL_NAME="$MODEL_NAME"
    -e MN_TP="$VLLM_TP"
    -e MN_PP="$VLLM_PP"
    -e MN_GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"
    -e MN_MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
    -e MN_PORT="$VLLM_PORT"
    -e MN_VLLM_ARGS="${VLLM_ARGS:-}"
  )
fi

exec podman run "${args[@]}" "$PODMAN_IMAGE"
