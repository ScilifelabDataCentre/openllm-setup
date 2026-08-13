#!/usr/bin/env bash
# /mimer/NOBACKUP/sll_dc/multinode/vllm-multinode.sh <deployment-name>
#
# Run the SAME command on every node in the deployment. The script finds this
# node's own InfiniBand address, compares it to the head address in the conf, and
# takes the head or worker role accordingly. There is no per-node editing and no
# per-node argument, which is the main thing that keeps a 3, 4 or 5 node
# deployment from becoming a hand-copying exercise.
#
# Lives on Mimer, not in a home directory, because homes on these machines are
# per-machine and N copies of a launcher will drift.
set -euo pipefail

NAME="${1:?usage: $(basename "$0") <deployment-name>}"
ROOT="${MULTINODE_ROOT:-/mimer/NOBACKUP/sll_dc/multinode}"
CONF="$ROOT/conf/${NAME}.conf"

die() { echo "FATAL: $*" >&2; exit 78; }

[ -r "$CONF" ] || die "cannot read conf: $CONF"
# shellcheck source=/dev/null
source "$CONF"

: "${MULTINODE_NODES:?MULTINODE_NODES not set in $CONF}"
: "${PODMAN_IMAGE:?PODMAN_IMAGE not set in $CONF}"
: "${HF_MODEL:?HF_MODEL not set in $CONF}"
: "${MODEL_NAME:?MODEL_NAME not set in $CONF}"

MULTINODE_IB_PREFIX="${MULTINODE_IB_PREFIX:-10.52.}"
RAY_PORT="${RAY_PORT:-6379}"
RAY_CPUS="${RAY_CPUS:-16}"
VLLM_NGPUS="${VLLM_NGPUS:-4}"
VLLM_PORT="${VLLM_PORT:-8000}"
SHM_SIZE="${SHM_SIZE:-32g}"
NCCL_DEBUG="${NCCL_DEBUG:-WARN}"
CLUSTER_WAIT_SECS="${CLUSTER_WAIT_SECS:-900}"

# --- derive the topology from the node list --------------------------------
# First address in MULTINODE_NODES is the head. Everything else follows from the
# count, so adding a node means editing exactly one line of the conf.
read -r -a NODES <<< "$MULTINODE_NODES"
HEAD_IP="${NODES[0]}"
NODE_COUNT="${#NODES[@]}"
TOTAL_GPUS=$(( NODE_COUNT * VLLM_NGPUS ))

# TP stays inside a node, over NVLink. PP spans nodes. Override in the conf only
# for a deliberate experiment.
VLLM_TP="${VLLM_TP:-$VLLM_NGPUS}"
VLLM_PP="${VLLM_PP:-$NODE_COUNT}"

# --- work out who we are ---------------------------------------------------
NODE_IP="$(hostname -I | tr ' ' '\n' | grep -m1 "^${MULTINODE_IB_PREFIX}" || true)"
[ -n "$NODE_IP" ] || die "no ${MULTINODE_IB_PREFIX}* address on $(hostname). Is InfiniBand up? Check: hostname -I"

in_list=0
for n in "${NODES[@]}"; do [ "$n" = "$NODE_IP" ] && in_list=1; done
[ "$in_list" = 1 ] || die "this node ($(hostname), $NODE_IP) is not in MULTINODE_NODES for '$NAME'"

if [ "$NODE_IP" = "$HEAD_IP" ]; then ROLE=head; else ROLE=worker; fi

CNAME="vllm-multinode-${NAME}"

echo "[launch] deployment=$NAME role=$ROLE node=$(hostname)/$NODE_IP"
echo "[launch] nodes=$NODE_COUNT head=$HEAD_IP total_gpus=$TOTAL_GPUS TP=$VLLM_TP PP=$VLLM_PP"
echo "[launch] image=$PODMAN_IMAGE"

args=(
  --name "$CNAME" --replace --rm
  --gpus all
  --net host
  --device /dev/infiniband
  --shm-size="$SHM_SIZE"

  # THE fix for the original silent hang. Podman defaults --pids-limit to 2048,
  # and the cgroup pids controller counts threads rather than processes, so Ray
  # plus vLLM exhausted it and Ray could not create its GCS listener thread.
  # Symptom was a total silence after "Connected to Ray cluster", no logs, no
  # GPUs claimed. Do not remove.
  --pids-limit=-1

  # Lets py-spy attach if something ever stalls again. Cheap insurance.
  --cap-add=SYS_PTRACE

  --ulimit nofile=524288:524288

  # --ulimit memlock is deliberately absent. The host hard limit for llm is 8 MB;
  # rootless podman cannot exceed a hard limit the user does not hold, so the
  # flag was silently clamped and did nothing. See the runbook, RDMA section.

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

  # Interface names, identical on every node. Set here at podman run time so that
  # every process in the container inherits them, including the workers Ray
  # spawns later. Exporting these in a shell reaches only that shell.
  -e NCCL_SOCKET_IFNAME="${MULTINODE_IB_IFNAME:-ibs1}"
  -e GLOO_SOCKET_IFNAME="${MULTINODE_IB_IFNAME:-ibs1}"

  -e NCCL_DEBUG="$NCCL_DEBUG"
  -e PYTHONUNBUFFERED=1
  -e RAY_DEDUP_LOGS=0

  -v /mimer/NOBACKUP/sll_dc:/mimer/NOBACKUP/sll_dc
)

# Transport. USE_RDMA=0 sends cross-node NCCL over TCP on the IB fabric, which is
# the current workaround for the 8 MB memlock cap. Flip to 1 only after Chalmers
# raises memlock and loads nvidia-peermem.
if [ "${USE_RDMA:-0}" = "1" ]; then
  args+=( -e NCCL_IB_HCA="${NCCL_IB_HCA:-mlx5_0}" )
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
