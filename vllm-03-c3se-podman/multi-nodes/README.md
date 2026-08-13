# Multi-node vLLM toolkit

`/mimer/NOBACKUP/sll_dc/multinode/`

Serves one model across several C3SE A100 nodes when it is too big for one. Every node reads this directory from Mimer, so this is the single copy to keep correct.

Operator procedures (deploy, verify, troubleshoot, add nodes) are in confluence doc. This README covers only what the files are and how to change them safely.

**Deployed now:** `qwen3-235b`, Qwen3-235B-A22B-Instruct-2507 BF16 across `sll-m11-41` (head, serves port 8000) and `sll-m11-42`. Live since 2026-08-13.

## Files

| File | Lives where | Purpose |
|---|---|---|
| `README.md` | here | This file. |
| `conf/<name>.conf` | here | One per deployment. The file you edit routinely. |
| `vllm-multinode.sh` | here | Host launcher. Run by systemd on every node. |
| `entry.sh` | baked into the image | Runs inside the container. The copy here is the build source only. |
| `Containerfile` | here | Builds the image: vLLM + pinned Ray + `entry.sh`. |
| `vllm-multinode@.service` | `~/.config/systemd/user/` on each node | systemd template. The one file that cannot live on Mimer. |

## Model

```
systemd on each node
  vllm-multinode@<name>.service
    └─ vllm-multinode.sh <name>          # one shared copy on Mimer
         reads conf/<name>.conf
         derives this node's role from its own IB address
         └─ podman run <image> (head|worker via -e MN_ROLE)
              └─ entry.sh                # baked into the image
                   head:   ray head → poll until all GPUs register → vllm serve
                   worker: ray worker → join head → block
```

Two invariants that make it work:

- **Same command on every node.** The launcher matches this node's `10.52.*` address against the first entry in `MULTINODE_NODES` and takes the head role only on a match. It refuses to start on a node not in the list. No per-node argument, no per-node file.
- **The head waits for the cluster.** It polls `ray.cluster_resources()` until every node has registered its GPUs, then launches vLLM. So nodes need no start ordering, which is why each runs an independent systemd unit.

## Adding a node

One line. Head, node count, total GPUs, and pipeline depth all derive from `MULTINODE_NODES`:

```bash
MULTINODE_NODES="10.52.xx.xxx 10.52.xx.xxx 10.52.xx.xxx"
```

Append, never reorder: the first entry is the head, and changing it invalidates `api_base` in `litellm.yaml`. Then load the image and install the unit on the new node. Full procedure and pipeline-depth caveats: runbook section 10.

## Changing something

| Change | What it takes |
|---|---|
| Model, port, flags, node list | Edit `conf/<name>.conf`, restart on every node. Nothing to redistribute. |
| `entry.sh` or `Containerfile` | Rebuild the image, reload on every node. `entry.sh` is baked in; editing the copy here does nothing until you rebuild. |
| `vllm-multinode.sh` | Restart on every node. Read from Mimer at start, no copying. |
| `vllm-multinode@.service` | Recopy to each node's `~/.config/systemd/user/`, `daemon-reload`. |

`bash -n` any script or conf after editing. Batch changes: every restart costs a full weight reload (5 to 20 minutes).

## Getting files here

`/mimer` is mounted on the compute nodes, not the login node.

```bash
scp <files> sll-m11-41:/mimer/NOBACKUP/sll_dc/multinode/
```

## Pinned versions

Do not float these. The combination is verified; a mismatch between nodes could produce a distributed hang, not a version error.

| Component | Version | Pinned in |
|---|---|---|
| Base image | `vllm/vllm-openai:v0.24.0` | `Containerfile` |
| Ray | `2.57.0` | `Containerfile` |
| torch | `2.11.0+cu130` | inherited from base |
| NCCL | `2.28.9+cuda13.0` | inherited |
| Built image | `localhost/vllm-ray:0.24.0-ray2.57.0` | `PODMAN_IMAGE` in each conf |

Every node runs the same loaded image. Rebuild, `podman save` to `/mimer/NOBACKUP/sll_dc/images/`, `podman load` on each node.

## Two rules

- **A node in `MULTINODE_NODES` runs no single-node `vllm@` model.** A multi-node deployment claims whole nodes (all four GPUs at 0.90 utilization). Sharing gives an out-of-memory on one rank, reported as a NCCL error by the other seven.
- **Start, stop, restart happen on every node.** There is no single command that drives the whole deployment.

## Known limitation: RDMA off

Cross-node NCCL runs over TCP on the IB fabric, not RDMA, because `memlock` for `llm` is capped at 8 MB and RDMA memory registration fails against it. `USE_RDMA=0` in the conf is the workaround. Confluence document has the evidence, the cost (modest at two nodes, grows with pipeline depth), the Chalmers request (raise `memlock`, load `nvidia-peermem`), and the flip procedure. Flip to `USE_RDMA=1` once that lands.

## Important Settings

- `--pids-limit=-1`: Podman's 2048 default is counted in threads; it starves Ray of its GCS listener thread, producing a silent hang with no log output.
- `VLLM_HOST_IP` per node, not shared.
- Environment via `-e` at `podman run`, never shell-exported: Ray workers inherit the raylet's environment.
- `GLOO_SOCKET_IFNAME` alongside `NCCL_SOCKET_IFNAME`.
- `--node-ip-address` on workers, not only the head.