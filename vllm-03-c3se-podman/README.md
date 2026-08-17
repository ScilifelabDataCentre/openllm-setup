# vllm-03-c3se-podman

vLLM inference backend for the OpenLLM pilot, on Chalmers C3SE A100 nodes, behind a
LiteLLM proxy. Change files here, commit,
pull on the hosts, restart. Never edit a host directly.

## Current state

| What | Where | Status |
|---|---|---|
| LiteLLM proxy `:4000` + Postgres | `sll-login` | running |
| `Qwen3-235B-A22B` multi-node | `sll-m11-41` (head, `:8000`) + `sll-m11-42` | running, TCP transport |
| `Qwen/Qwn3-32B` single-node | `sll-m11-38` | running |
| Public endpoint | `https://scilifelab-ai.c3se.chalmers.se/v1` | via nginx/TLS to Open WebUI |

**Known issues, see [Open items](#open-items):** RDMA is unavailable so cross-node
traffic runs over TCP.

## Architectural considerations

**1. There are two checkouts, because `/mimer` is not mounted on the login node.**

| Machine | Checkout | Used for |
|---|---|---|
| any compute node | `/mimer/NOBACKUP/sll_dc/openllm-setup/vllm-03-c3se-podman` | shared by all five nodes, so they cannot drift |
| `sll-login` | `home/llm//openllm-setup/vllm-03-c3se-podman` | its own clone; only `proxy/` matters here |

Pull both when a change affects each side.

**2. Multi-node operations happen on every node.** Start, stop and restart. There is
no single command that drives a multi-node deployment. Single-node models are one node
only.

**3. A node runs either single-node models or one multi-node deployment, never both.**
The [node allocation](#node-allocation) table is the policy. `node-free-check.sh`
enforces it and will refuse to start when a node is already taken.

## Contents

- [Setup, once per host](#setup-once-per-host)
- Tasks: [single-node model](#deploy-a-single-node-model) &middot;
  [multi-node model](#deploy-a-multi-node-model) &middot;
  [register in the proxy](#register-a-model-in-the-proxy-login-node-only) &middot;
  [add a node](#add-a-node-to-a-multi-node-deployment) &middot;
  [change a setting](#change-a-setting) &middot;
  [stop something](#stop-something)
- Reference: [layout](#layout) &middot; [host paths](#host-paths) &middot;
  [node allocation](#node-allocation) &middot; [pinned versions](#pinned-versions) &middot;
  [reading bootstrap output](#reading-bootstrapsh-output)
- [Troubleshooting](#troubleshooting)
- [Data you cannot recreate](#data-you-cannot-recreate)
- [Transport status: why RDMA is off](#transport-status-why-rdma-is-off)
- [Important Settings](#important-settings)
- [Open items](#open-items) &middot; [Secrets](#secrets)

---

# Setup, once per host

## Prerequisites

```bash
podman --version                          # rootless podman
git --version                             # added by C3SE 2026-08-14 on compute nodes
ls -d /mimer/NOBACKUP/sll_dc              # compute nodes only
curl -sI https://github.com | head -1     # expect HTTP/2 200
nvidia-smi                                # 4 GPUs, and nothing already using them
```

C3SE also added `nano`, `wget`, `ripgrep` and `tmux`. `rg` is much faster than `grep`
on a large NCCL log, and `tmux` is useful for watching a long bring-up.

## Clone

On a compute node, **once**. `/mimer` is shared, so one checkout serves all five:

```bash
cd /mimer/NOBACKUP/sll_dc
git clone https://github.com/ScilifelabDataCentre/openllm-setup.git
cd openllm-setup/vllm-03-c3se-podman
```

On the login node, separately, into the `home/llm/` directory:

```bash
cd ~ && git clone https://github.com/ScilifelabDataCentre/openllm-setup.git
cd openllm-setup/vllm-03-c3se-podman
```


```bash
chmod +x bootstrap.sh node-free-check.sh multi-node/vllm-multinode.sh multi-node/entry.sh
```

## Install to the host paths

From inside the checkout on that machine:

```bash
./bootstrap.sh compute        # on each compute node
./bootstrap.sh login          # on the login node
./bootstrap.sh --check compute    # report drift, change nothing
```

See [reading bootstrap output](#reading-bootstrapsh-output) for what `ok` and
`updated` mean. It never touches `~/litellm.env` or any other secret, and it exits
non-zero rather than reporting success if the layout is wrong or a file is missing.

## Updating later

```bash
# compute nodes: one pull serves all five
git -C /mimer/NOBACKUP/sll_dc/openllm-setup pull
./bootstrap.sh compute                  # only if a unit file or model conf changed

# login node: its own pull
git -C ~/openllm-setup pull
./bootstrap.sh login
systemctl --user restart litellm        # briefly interrupts every model
```

---

# Deploy a single-node model

One model, one node, tensor parallel across its 4 GPUs. 320 GB of GPU memory. Use
this unless the model does not fit.

`vllm@.service` is a systemd template: the instance name picks the config file, so a
config file is the whole definition of a model and the unit never changes.

```
systemctl --user start vllm@qwen3-32b
  -> reads ~/vllm/qwen3-32b.conf as an EnvironmentFile
  -> podman run --name vllm-qwen3-32b ... ${HF_MODEL} --served-model-name ${MODEL_NAME}
```

**1. Check the node is free.** Against the [allocation table](#node-allocation), and
on the node itself.

```bash
systemctl --user list-units 'vllm@*' 'vllm-multinode@*' --no-legend
podman ps --format '{{.Names}} {{.Image}}'
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader
```

**2. Download the weights** to Mimer, once, and note the snapshot path:

```bash
export HF_HOME=/mimer/NOBACKUP/sll_dc/huggingface
source /mimer/NOBACKUP/sll_dc/venv/bin/activate
export HF_TOKEN=<Bitwarden: Data Centre/Whale>
hf download <ORG/MODEL>
ls -d /mimer/NOBACKUP/sll_dc/huggingface/hub/models--<ORG>--<MODEL>/snapshots/*/
```

**A100 has no usable FP8.** Use BF16 or INT4 (AWQ/GPTQ).

**3. Write the config** in `single-node/models/<name>.conf`, starting from
`qwen3-32b.conf`. Variable names are fixed; the unit references them directly.

| Variable | Notes |
|---|---|
| `PODMAN_IMAGE` | Pin the tag. Never `latest`. |
| `VLLM_PORT` | Unique per model **per node**. Conflicts only with models on the same node. |
| `HF_MODEL` | Full snapshot path on Mimer, never a HuggingFace short name. |
| `MODEL_NAME` | API-facing name. Must match `litellm.yaml` exactly. |
| `VLLM_NGPUS` | 4 uses the whole node, which is TP=4. |
| `VLLM_ARGS` | Everything else, space separated. |
| `GPU_MEM_UTIL` | Fraction of each GPU. Usually 0.90. `gemma4.conf` uses 0.4 because it is small; do not copy that to a large model. |

**4. Commit, pull, install, start:**

```bash
git -C /mimer/NOBACKUP/sll_dc/openllm-setup pull
./bootstrap.sh compute
systemctl --user reset-failed vllm@<name>
systemctl --user start vllm@<name>
podman logs -f vllm-<name>              # wait for "Application startup complete"
curl -s http://localhost:8000/v1/models | python3 -m json.tool
```

**5. Register it in the proxy**, [on the login node](#register-a-model-in-the-proxy-login-node-only).

---

# Deploy a multi-node model

For models too large for 320 GB. Tensor parallelism stays inside each node over
NVLink; pipeline parallelism spans nodes, coordinated by Ray.

```
systemd on each node
  vllm-multinode@<name>.service
    └─ vllm-multinode.sh <name>          # one shared copy, in the Mimer checkout
         reads conf/<name>.conf
         works out this node's role from its own 10.52 address
         └─ podman run <image>           # role passed as -e MN_ROLE
              └─ entry.sh                # baked into the image
                   head:   ray head -> wait for all GPUs -> vllm serve
                   worker: ray worker -> join head -> block
```

Two invariants. **The same command runs on every node**: the launcher compares this
node's `10.52.*` address to the first entry in `MULTINODE_NODES`, takes the head role
only on a match, and refuses to start on a node not in the list. **The head waits for
the cluster**, so start order does not matter, which is what lets each node run an
independent unit. systemd cannot express ordering across machines.

```bash
# 1. build the image once, on any compute node, then load it on every node
cd multi-node
podman build -t localhost/vllm-ray:0.24.0-ray2.57.0 -f Containerfile .
mkdir -p /mimer/NOBACKUP/sll_dc/images
podman save -o /mimer/NOBACKUP/sll_dc/images/vllm-ray-0.24.0-ray2.57.0.tar \
  localhost/vllm-ray:0.24.0-ray2.57.0
for n in 41 42; do
  ssh sll-m11-$n 'podman load -i /mimer/NOBACKUP/sll_dc/images/vllm-ray-0.24.0-ray2.57.0.tar'
done

# 2. write conf/<name>.conf from conf/example.conf, commit, pull, then on each node:
REPO=/mimer/NOBACKUP/sll_dc/openllm-setup/vllm-03-c3se-podman
for n in 41 42; do ssh sll-m11-$n "$REPO/bootstrap.sh compute"; done

# 3. start on EVERY node. Order does not matter.
for n in 41 42; do ssh sll-m11-$n \
  'systemctl --user reset-failed vllm-multinode@<name>
   systemctl --user start vllm-multinode@<name>'; done

# 4. watch the head
ssh sll-m11-41 'podman logs -f vllm-multinode-<name>'
```

Milestones in the head's log:

| Roughly | Line | Meaning |
|---|---|---|
| 0s | `[launch] role=head nodes=2 ... commit=abc12345` | conf parsed, topology derived, version recorded |
| | `commit=abc12345-DIRTY` | the checkout has uncommitted changes, so the stamp does not describe what is running |
| 5 to 60s | `waiting for cluster: 4/8 GPUs` | other nodes not in yet, normal |
| | `cluster ready: 8/8 GPUs on ...` | Ray has registered 8 GPUs. **Not** the same as 8 healthy workers; those come next and can still fail |
| +30s | `NET/IB` or `NET/Socket` | which transport NCCL chose |
| 5 to 20m | GPU memory climbing on every node | reading weights off Mimer |
| | `Application startup complete` | serving |

Then confirm the GPUs are genuinely held, and register in the proxy pointing at the
**head** node:

```bash
podman exec -it vllm-multinode-<name> ray status | grep GPU   # want 8.0/8.0
```

---

# Register a model in the proxy (login node only)

**This happens on `sll-login` and nowhere else.** LiteLLM runs only there, and
`~/litellm/litellm.yaml` exists only there. The compute nodes serve HTTP on port 8000
and know nothing about who calls them.

Both serving modes register identically: LiteLLM cannot tell them apart. For a
multi-node deployment, `api_base` is the **head** node, the only one serving HTTP.

Edit `proxy/litellm.yaml`, commit, then on the login node:

```yaml
  - model_name: <ORG/MODEL>
    litellm_params:
      model: hosted_vllm/<ORG/MODEL>
      api_base: http://sll-m11-NN:8000/v1
```

```bash
cd ~/openllm-setup/vllm-03-c3se-podman
cp ~/litellm/litellm.yaml ~/litellm-backup-$(date +%F).yaml   # bootstrap overwrites wholesale
git pull
diff ~/litellm-backup-$(date +%F).yaml proxy/litellm.yaml      # repo must be a SUPERSET
./bootstrap.sh login
systemctl --user restart litellm
source ~/litellm.env
curl -s http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  | python3 -m json.tool
```

Important considerations:

**The three names must be byte-identical**: `model_name`, the string after
`hosted_vllm/`, and `MODEL_NAME` in the model's conf. A mismatch gives a 404 that
looks like the backend is down.

**Take the backup and read the diff.** `bootstrap.sh` replaces `litellm.yaml`
entirely, so a model present live but absent from the repo copy is silently dropped
from routing.

**A restart interrupts every model**, not just the one you changed. Batch edits.

## Scoped keys

You can issue one scoped key for each model if you prefer:

```bash
source ~/litellm.env
curl -s http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"models":["<ORG/MODEL>"],"key_alias":"<consumer>","metadata":{"owner":"<team>"}}' \
  | python3 -m json.tool
```

Scoped keys can carry their own budget and rate limit, are attributable per consumer for pilot reporting, and can be revoked
individually.

**A scoped key only lists the models named in it.** Adding a model to `litellm.yaml`
does not add it to existing keys. Check and update:

```bash
curl -s http://localhost:4000/key/info -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" -d '{"key":"<the key>"}' | python3 -m json.tool

curl -s http://localhost:4000/key/update -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key":"<the key>","models":["<model-a>","<model-b>"]}'
```

## Proxy first-time setup

Only when rebuilding from scratch.

```bash
cp proxy/litellm.env.example ~/litellm.env
chmod 600 ~/litellm.env
# fill in LITELLM_MASTER_KEY and the DB password from Bitwarden: Data Centre/Whale.
# The password appears twice, in POSTGRES_PASSWORD and inside DATABASE_URL, and must match.

podman volume create litellm-pg-data
./bootstrap.sh login

# Postgres first. The first LiteLLM start runs DB migrations, tens of seconds.
systemctl --user enable --now litellm-postgres
sleep 5; journalctl --user -u litellm-postgres -n 20 --no-pager   # want "ready to accept connections"
systemctl --user reenable litellm
systemctl --user reset-failed litellm && systemctl --user restart litellm
journalctl --user -u litellm -n 40 --no-pager                     # want "migrations ... successfully applied"
```

Harmless on first start: Postgres logging `ERROR: relation "..." does not exist` while
LiteLLM probes optional views, and a prisma warning about falling back from distro
"wolfi" to "debian".

---

# Add a node to a multi-node deployment

One line in the conf. Head, node count, total GPUs and pipeline depth all derive from
it:

```bash
MULTINODE_NODES="10.52.30.121 10.52.30.122 10.52.30.120"
```

**Append, never reorder.** The first entry is the head, and changing it invalidates
`api_base` in `litellm.yaml`. Get each address from `hostname -I` on that node and
take the `10.52` one. Then load the image there, run `./bootstrap.sh compute` there,
update the [allocation table](#node-allocation), and restart on all nodes.

Two things to weigh as the pipeline deepens:

**Layer divisibility.** PP splits by layer. Qwen3-235B has 94 layers: even across 2
stages, not across 3, 4 or 5. It still runs, but stages become unbalanced and the
slowest sets the pace.

**Diminishing returns.** Pipeline depth adds latency and bubbles, so a model that
fits in two nodes runs *slower* on four. For more throughput on a model that already
fits, run a second independent deployment as a replica instead.

---

# Change a setting

| Change | What it takes |
|---|---|
| Single-node model conf | Edit, pull, `./bootstrap.sh compute`, restart that unit |
| Multi-node conf | Edit, pull, restart on every node. Nothing to install |
| `vllm-multinode.sh` or `node-free-check.sh` | Pull, restart. Read from the checkout at start |
| `entry.sh` or `Containerfile` | Rebuild the image and reload on every node. `entry.sh` is baked in, so editing it does nothing until you rebuild |
| Any `.service` | Pull, `./bootstrap.sh`, restart |
| `litellm.yaml` | Pull, `./bootstrap.sh login`, restart litellm. Interrupts every model |

`bash -n` any script or conf after editing. Batch changes: a multi-node restart costs
a full weight reload, 5 to 20 minutes.

---

# Stop something

## Single node

```bash
systemctl --user stop vllm@<name>
podman ps --format '{{.Names}}'      # want no vllm-<name>
nvidia-smi                           # want ~0 MiB
```

## Multi node: workers first, then the head

```bash
ssh sll-m11-42 'systemctl --user stop vllm-multinode@<name>'   # workers
ssh sll-m11-41 'systemctl --user stop vllm-multinode@<name>'   # head last
for n in 41 42; do ssh sll-m11-$n \
  'systemctl --user reset-failed vllm-multinode@<name>
   podman ps --format "{{.Names}}"; nvidia-smi'; done
```

**Order matters, and it is workers first.** A worker drains itself out of the Ray
cluster on shutdown. If the head is already gone, that drain times out with
`RpcError: Timed out while waiting for GCS to become available`, the entrypoint exits
1, and the unit lands in `failed`. Nothing is actually wrong, but it needs
`reset-failed` before it will start again, which is why the command above includes it.

**`podman ps` is the check that matters, not `nvidia-smi`.** A deployment that is
*failing* rather than *stopped* holds no GPU memory, so the GPUs look free, but the
container is present and will reclaim the node the moment its peers return.

If something survives, on that node only:

```bash
podman rm -f $(podman ps -q --filter 'name=vllm-') 2>/dev/null
pkill -9 -f 'ray::'; pkill -9 -f vllm
rm -rf /tmp/ray/*
nvidia-smi
```

Clearing `/tmp/ray` matters: stale session state there once made
`ray_current_cluster` report a head address that had never been configured.

---

# Reference

## Layout

```
proxy/                          # login node only
  litellm.yaml                  # THE routing table: every model, both modes
  litellm.service
  litellm-postgres.service
  litellm.env.example           # template; the filled-in copy is never committed
single-node/                    # compute nodes, one model per node
  vllm@.service                 # systemd template; instance name picks the conf
  models/*.conf                 # read by vllm@.service as EnvironmentFile
multi-node/                     # compute nodes, one model across N nodes
  vllm-multinode@.service       # systemd template
  vllm-multinode.sh             # launcher; runs on every node in the deployment
  Containerfile, entry.sh       # the image: vLLM + pinned Ray + entrypoint
  conf/*.conf                   # one per deployment; example.conf is the template
bootstrap.sh                    # install this repo's files to their host paths
node-free-check.sh              # shared guard: refuses to start if the node is taken
```

## Host paths

Home directories are per-machine and only `/mimer` is shared, so unit files land on
each relevant node. `bootstrap.sh` does this; the table is the reference.

| Repo file | Host path | Node |
|---|---|---|
| `proxy/litellm.service` | `~/.config/systemd/user/litellm.service` | login |
| `proxy/litellm-postgres.service` | `~/.config/systemd/user/litellm-postgres.service` | login |
| `proxy/litellm.yaml` | `~/litellm/litellm.yaml` | login |
| `proxy/litellm.env.example` filled in | `~/litellm.env`, chmod 600, **never committed** | login |
| `single-node/vllm@.service` | `~/.config/systemd/user/vllm@.service` | each compute node |
| `single-node/models/<name>.conf` | `~/vllm/<name>.conf` | each compute node |
| `multi-node/vllm-multinode@.service` | `~/.config/systemd/user/vllm-multinode@.service` | each compute node |
| `vllm-multinode.sh`, `node-free-check.sh`, `conf/*.conf` | read from the checkout, not copied | all compute nodes |

Both `.service` templates are **generated**, not copied: `bootstrap.sh` substitutes
the checkout path for `@@REPO@@`. Re-run it if the checkout ever moves.

## Node allocation

**There is no scheduler.** Keep this table current, in the same commit as any change
to a node list.

| Node | Assigned to | Since |
|---|---|---|
| `sll-m11-38` | single-node `Qwen3-32B`** (serves `:8000`)| 2026-08-17 |
| `sll-m11-39` | free | |
| `sll-m11-40` | free | |
| `sll-m11-41` | **multi-node `qwen3-235b`** (head, serves `:8000`) | 2026-08-17 |
| `sll-m11-42` | **multi-node `qwen3-235b`** (worker) | 2026-08-17 |

A multi-node deployment claims all four GPUs at 0.90 utilization, so nothing
meaningful is left for a second model. Sharing produces an out-of-memory error on one
rank, surfaced as a NCCL error reported by the others, several screens from the cause.

`node-free-check.sh` enforces this, matching on the container **image** as well as the
name so a hand-started container counts too. `ALLOW_SHARED_NODE=1` in a conf overrides
it. It deliberately does not check `nvidia-smi`, because a fast restart races with
memory still being released and a guard that false-positives gets deleted.

`bootstrap.sh` installs every model conf on every compute node, since a conf that is
never started does nothing. **A conf being present is not permission to start it.**
This table is.

## Pinned versions

Multi-node only; single-node uses the stock image. A mismatch between nodes produces a
distributed hang, not a version error.

| Component | Version | Pinned in |
|---|---|---|
| Base image | `vllm/vllm-openai:v0.24.0` | `multi-node/Containerfile` |
| Ray | `2.57.0` | `multi-node/Containerfile` |
| torch | `2.11.0+cu130` | inherited from the base image |
| NCCL | `2.28.9+cuda13.0` | inherited |
| Built image | `localhost/vllm-ray:0.24.0-ray2.57.0` | `PODMAN_IMAGE` in each multi-node conf |

## Reading bootstrap.sh output

| Line | Meaning |
|---|---|
| `ok` | the host file is already byte-identical to the repo; nothing written |
| `updated` | it differed, so the repo version was copied over |
| `DIFFERS` | `--check` mode only: it differs and would be updated |
| `MISSING` | the repo file is absent. Exits 66; the checkout is incomplete or you are in the wrong directory |
| `ACTION` | something you must do by hand, such as creating `~/litellm.env` |

`updated` on `litellm.yaml` means the whole file was replaced, so read the diff
against your backup. `ok` on both `.service` files means they were already current.

---

# Troubleshooting

**Logs.** `journalctl --user -u <unit>` shows the unit's view: exit codes, restart
decisions, `ExecStartPre` failures. `podman logs -f <container>` shows what the
process actually printed. Use `journalctl` when a unit will not start, `podman logs`
when it starts but the model misbehaves.

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied` running `./bootstrap.sh` | executable bit lost in transfer | `chmod +x bootstrap.sh node-free-check.sh multi-node/*.sh` |
| `FATAL: expected directory .../proxy does not exist` | files copied individually, so the structure is flat | re-clone with git, or `scp -r` the directory |
| `FATAL: another vLLM container is already running` | the node guard, and it is usually right | `podman ps --format '{{.Names}} {{.Image}}'`, then remove the leftover or stop what does not belong |
| Multi-node unit in `failed` after a planned stop | worker's Ray drain timed out because the head went first | cosmetic. `reset-failed`, and stop workers before the head next time |
| `NCCL WARN Call to ibv_reg_mr_iova2 failed with error Cannot allocate memory` | `memlock` is 8 MB for systemd-started processes | `USE_RDMA=0`. See [transport status](#transport-status-why-rdma-is-off) |
| Silence after `Connected to Ray cluster`, `ray status` shows `0/8 GPU` | podman's default `--pids-limit=2048`, counted in **threads** | already fixed by `--pids-limit=-1`. Confirm `cat /sys/fs/cgroup/pids.max` is not 2048 |
| `RuntimeError: can't start new thread` from Ray | same cause | same fix. `--ulimit nproc` does not help: it governs processes, the cgroup governs threads |
| `Tensor parallel size (8) exceeds available GPUs (4)` warning, then it continues | vLLM compares world size against the **local** device count | cosmetic. Verify once with `python3 -c "import ray; ray.init(address='auto'); print(ray.cluster_resources())"` |
| GPUs empty for many minutes | normal during weight load, **only if** Ray already holds them | check `ray status` first. `0/N` in use means vLLM has not reached the GPUs |
| Out of memory on **one** node while others load | another vLLM is on that node | `nvidia-smi` and `podman ps` on every node in the deployment |
| Model 404s through LiteLLM but works on `:8000` | the three names disagree | make `model_name`, `hosted_vllm/...` and `MODEL_NAME` identical, restart litellm |
| A model missing from `/v1/models` **with a scoped key** | scoped keys list only the models named in them | `key/info`, then `key/update` |
| A model missing from `/v1/models` **with the master key** | its `litellm.yaml` entry failed to load | `journalctl --user -u litellm -n 60 --no-pager \| grep -iE 'error\|warn'` |
| Open WebUI does not show a new model | its model list is cached server-side | Admin Panel, Settings, Connections: open the OpenAI connection and save it again. Verify the public endpoint first, not just `localhost:4000` |
| Postgres up but every query fails on shared memory | unit lost its settings | must keep both `--shm-size=256m` and `-c dynamic_shared_memory_type=mmap` |
| Stuck in `failed`, restart refused | systemd rate limiting | `systemctl --user reset-failed <unit>` then start |
| `status=125` from podman, and a bare `podman ps` also fails | podman crash-loop | see below |

## Podman crash-loop

The tell is that a bare `podman ps` also fails. Two variants: the journal says either
"unhandled reboot has occurred", meaning stale `/tmp/storage-run-1001`, or "cannot
re-exec process to join the existing user namespace", meaning a stale
`/run/user/1001/libpod` pause.pid. Same recovery, on the affected node:

```bash
systemctl --user stop <unit>
pkill -u "$(id -u)" -f 'podman|conmon|catatonit'; sleep 1
rm -rf /run/user/$(id -u)/libpod /tmp/storage-run-$(id -u)/libpod /tmp/storage-run-$(id -u)/containers
podman system migrate && podman ps
systemctl --user reset-failed <unit> && systemctl --user start <unit>
```

Do not move the `/run/user/.../libpod` wipe into `ExecStartPre`; it can disrupt a
healthy running container, so it belongs in manual recovery only.

**Related, and unresolved.** The `/tmp/storage-run-<uid>` wipe that *is* in
`ExecStartPre` is per user, not per container. On a compute node that is safe, because
the guard ensures one vLLM at a time. On the **login node it is not**: `litellm` and
`litellm-postgres` both run under `llm` and both carry that wipe, so restarting one
clears podman runtime state belonging to the other. That predates this repo and is
left alone rather than changed under a running service, but it is a plausible
contributor to the crash-loop above. Do not copy the pattern into a new unit that can
run alongside others.

## Verifying a limit actually applies to a service

Worth knowing, because a plausible-looking check misled us for three days.

```bash
# WRONG: spawns a new process, inheriting from YOUR ssh session, not the service
podman exec <container> bash -c 'ulimit -l'

# RIGHT: the limits the running service actually has
pid=$(systemctl --user show <unit> -p MainPID --value)
grep -i 'max locked memory' /proc/$pid/limits

# RIGHT: what a new service would get, without restarting anything
systemd-run --user --pipe --wait bash -c 'echo soft=$(ulimit -l) hard=$(ulimit -Hl)'
```

An SSH session gets its limits from `pam_limits` and `/etc/security/limits.conf`.
Anything under `systemctl --user` inherits from the long-running `systemd --user`
daemon instead. The two can differ by orders of magnitude, and on these nodes they do.

---

# Data you cannot recreate

| Item | If lost | Recoverable from |
|---|---|---|
| **podman volume `litellm-pg-data`** | **every scoped key and all spend history, gone** | **nothing** |
| `~/litellm.env` | proxy cannot start or authenticate | Bitwarden, if the DB password matches what Postgres was initialised with |
| `~/litellm/litellm.yaml` | proxy stops | git, via `./bootstrap.sh login` |
| model weights on Mimer | long re-download | HuggingFace |

`podman volume rm litellm-pg-data` is the one command in this stack that destroys data
nothing can reconstruct. Stopping or removing the *container* is safe; the volume
survives. The volume lives on the login node's local disk, and Mimer is explicitly not
backed up either, so it is worth confirming what backup covers it:

```bash
podman volume inspect litellm-pg-data --format '{{.Mountpoint}}'
```

---

# Transport status: why RDMA is off

Cross-node NCCL runs over TCP on the InfiniBand fabric, not RDMA, because
`USE_RDMA=0`. This is a workaround, and as of 2026-08-17 it is still required.

**The cause.** RDMA must pin memory, charged against `RLIMIT_MEMLOCK`. NCCL needs more
than 8 MB, so `ibv_reg_mr_iova2` fails with "Cannot allocate memory".

**What was fixed, and what was not.** C3SE raised `memlock` to 1 GiB in
`/etc/security/limits.conf` on 2026-08-14. That applies to SSH sessions via
`pam_limits`. It does **not** apply to the systemd user manager, which is where our
services actually come from:

```
[llm@sll-m11-42 ~]$ ulimit -l                                        # this ssh session
1073741824
[llm@sll-m11-42 ~]$ systemd-run --user --pipe --wait bash -c 'ulimit -l; ulimit -Hl'
8192
8192
[llm@sll-m11-42 ~]$ grep -i 'max locked' /proc/$(pgrep -u $(id -u) -f 'systemd --user' | head -1)/limits
Max locked memory         8388608              8388608              bytes
```

Because the **hard** limit is also 8 MB, `LimitMEMLOCK=infinity` in a unit cannot help:
only root can raise a hard limit.

**What is needed.** Either `user@1001.service` is simply older than the limits change
and a restart of it would pick up the new values, or `DefaultLimitMEMLOCK=infinity` is
needed in `/etc/systemd/system.conf` or as a drop-in on `user@.service`. These
distinguish the two:

```bash
systemctl show "user@$(id -u).service" -p LimitMEMLOCK
grep -rn limits /etc/pam.d/systemd-user
```

Either way it needs root, and restarting `user@1001.service` terminates every user
service, so it should be scheduled.

**Cost of the workaround.** Modest at two nodes. Pipeline parallelism ships only
activations across the node boundary, a few megabytes per handoff, not weights and not
all-reduces. Expect a few percent on generation and around ten percent on long-prompt
time to first token, growing with pipeline depth. Note also that GPU2 and GPU3 are
`SYS` from the NICs on these nodes, a socket-interconnect hop, so GPUDirect
specifically may not help even once RDMA works.

**To flip, once the limit is raised.** Verify first with the `systemd-run` one-liner
above, so you do not spend twenty minutes on a restart to find out. Then set
`USE_RDMA=1`, restart on every node, and check:

```bash
podman logs vllm-multinode-<name> 2>&1 | grep -E 'NET/IB|NET/Socket'   # want NET/IB
podman logs vllm-multinode-<name> 2>&1 | grep -c ibv_reg_mr            # want 0
podman logs vllm-multinode-<name> 2>&1 | grep -E 'GDR [01]|PXN [0-9]'  # what NCCL chose
```

---

# Important Settings


| Setting | Note |
|---|---|
| `--pids-limit=-1` | Podman's 2048 default is counted in **threads** by the cgroup pids controller, starving Ray of the thread it needs for its GCS listener. vLLM then hangs after "Connected to Ray cluster" with no log output anywhere and no GPUs claimed. |
| `VLLM_HOST_IP` per node | Set to the head's address everywhere, ranks on other nodes advertise themselves at the head and distributed init hangs |
| `-e` at podman run, not shell exports | Ray workers inherit the environment of the raylet that spawned them, so a variable exported in a shell on the head never reaches the other node's workers |
| `GLOO_SOCKET_IFNAME` | vLLM uses a Gloo CPU process group for its own init. On a dual-homed host without this it can select the 10.23 management interface and stall |
| `--node-ip-address` on workers too | Ray detects its address by opening a socket toward the default route, which returns the 10.23 management address, giving an asymmetric cluster |
| Ray pinned in the image | `pip install "ray[default]"` at start time can install a different version on different nodes, and the result is a hang rather than a version error |
| The head's wait-for-cluster loop | Start order would matter, needing an ordering relationship systemd cannot express across machines |
| `--shm-size=256m` and `mmap` on Postgres | Podman's default `/dev/shm` is too small and every query fails |

---

# Open items

1. **RDMA is unavailable.** Needs a root-side systemd limits change. See
   [transport status](#transport-status-why-rdma-is-off). Not blocking.
2. **No health monitoring.** Item 2 went unnoticed for five weeks. A periodic probe of
   each `api_base`, or a LiteLLM fallback route, would have caught it on day one.
3. **`--enforce-eager` is still set** on the multi-node deployment. It exists only to
   make first bring-up debuggable. Removing it costs startup time and buys throughput;
   measure before and after.
4. **The login node's `memlock` is still 8 MB** in SSH sessions, unlike the compute
   nodes. Harmless, since nothing there uses RDMA, but inconsistent.

---

# Secrets

Never commit `~/litellm.env` or any filled-in env file; see `.gitignore`. The master
key, the DB password, the HuggingFace token and all issued scoped keys should live in
Bitwarden.

```bash
ls -l ~/litellm.env      # want -rw------- and owner llm
```