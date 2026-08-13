#!/usr/bin/env bash
# /usr/local/bin/entry.sh  (baked into the image, not edited on the nodes)
#
# Runs inside the container on every node. Everything arrives as MN_* environment
# variables set by vllm-multinode.sh on the host.
#
# Worker nodes: start Ray, join the head, block forever.
# Head node: start Ray, wait until every node has registered its GPUs, then exec
# vllm serve. That wait is what makes node start order irrelevant, which in turn
# is what lets each node run an independent systemd unit.
#
# MN_ prefix, not VLLM_, on purpose. vLLM warns about every unrecognised VLLM_*
# variable it finds in the environment, and those warnings are noise that hides
# real problems in the log.
set -euo pipefail

log() { printf '[entry %s] %s\n' "$(date -u '+%H:%M:%S')" "$*"; }

: "${MN_ROLE:?MN_ROLE not set}"
: "${MN_NODE_IP:?MN_NODE_IP not set}"
: "${MN_HEAD_IP:?MN_HEAD_IP not set}"
MN_RAY_PORT="${MN_RAY_PORT:-6379}"
MN_RAY_CPUS="${MN_RAY_CPUS:-16}"
MN_GPUS="${MN_GPUS:-4}"

ray_common=(
  --node-ip-address="$MN_NODE_IP"
  --num-cpus="$MN_RAY_CPUS"
  --num-gpus="$MN_GPUS"
  --disable-usage-stats
)

# ---------------------------------------------------------------- worker role
if [ "$MN_ROLE" = "worker" ]; then
  log "worker ${MN_NODE_IP} joining head ${MN_HEAD_IP}:${MN_RAY_PORT}"
  # --block keeps Ray in the foreground, so the container lives exactly as long
  # as this node's participation in the cluster.
  exec ray start --address="${MN_HEAD_IP}:${MN_RAY_PORT}" "${ray_common[@]}" --block
fi

# ------------------------------------------------------------------ head role
: "${MN_TOTAL_GPUS:?MN_TOTAL_GPUS not set}"
: "${MN_HF_MODEL:?MN_HF_MODEL not set}"
: "${MN_MODEL_NAME:?MN_MODEL_NAME not set}"

log "head ${MN_NODE_IP} starting Ray on port ${MN_RAY_PORT}"
ray start --head --port="$MN_RAY_PORT" "${ray_common[@]}"

want="$MN_TOTAL_GPUS"
deadline=$(( SECONDS + ${MN_WAIT_SECS:-900} ))

while :; do
  # Asking through the Python client, not the ray CLI, is deliberate: this is the
  # exact call vLLM makes, so if it disagrees with `ray status` we want to know
  # before vLLM starts rather than after.
  read -r have nodes <<<"$(python3 - <<'PY' 2>/dev/null || echo "0 none"
import ray
ray.init(address="auto", logging_level="ERROR")
res = ray.cluster_resources()
ips = sorted(k.split(":", 1)[1] for k in res
             if k.startswith("node:") and not k.startswith("node:__"))
print(int(res.get("GPU", 0)), ",".join(ips) or "none")
PY
)"

  if [ "${have:-0}" -ge "$want" ]; then
    log "cluster ready: ${have}/${want} GPUs on ${nodes}"
    break
  fi

  if [ "$SECONDS" -ge "$deadline" ]; then
    log "FATAL: only ${have}/${want} GPUs registered before timeout"
    log "joined so far: ${nodes}"
    log "check that the unit is running on every node listed in the conf"
    exit 1
  fi

  log "waiting for cluster: ${have}/${want} GPUs, joined: ${nodes}"
  sleep 5
done

# Word splitting here is intended: MN_VLLM_ARGS is a space separated flag list.
read -r -a extra <<< "${MN_VLLM_ARGS:-}"

log "launching vllm serve, TP=${MN_TP:-4} PP=${MN_PP:-2}, port ${MN_PORT:-8000}"
exec vllm serve "$MN_HF_MODEL" \
  --served-model-name "$MN_MODEL_NAME" \
  --distributed-executor-backend ray \
  --tensor-parallel-size "${MN_TP:-4}" \
  --pipeline-parallel-size "${MN_PP:-2}" \
  --gpu-memory-utilization "${MN_GPU_MEM_UTIL:-0.90}" \
  --max-model-len "${MN_MAX_MODEL_LEN:-32768}" \
  --port "${MN_PORT:-8000}" \
  "${extra[@]}"
