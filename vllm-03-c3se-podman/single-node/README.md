# vllm-03-c3se-podman

vLLM inference backend for the OpenLLM pilot, hosted on Chalmers C3SE A100 nodes and served through a LiteLLM proxy. This directory holds the deployment artefacts (systemd unit files, model configs, proxy config, env template). It is the source of truth; the hosts are deployment targets.

## Architecture

```
Open WebUI (KTH cluster) --HTTPS 443--> nginx/TLS --> LiteLLM :4000 (login node)
                                                          |
                                          hosted_vllm --> vLLM :8000 (compute nodes, TP=4)
```

- Login/proxy node: `sll-login` runs LiteLLM (`:4000`) and its Postgres key store, both as `systemctl --user` podman services under account `llm` (UID 1001).
- Compute: `sll-m11-38` .. `sll-m11-42`, each 4 x A100-SXM4-80GB. vLLM runs one instance per model, TP=4, via a systemd template unit.
- Shared storage: `/mimer/NOBACKUP/sll_dc` (model weights + a download venv). Not backed up.
- Public endpoint: `https://scilifelab-ai.c3se.chalmers.se/v1`.

## Layout

```
systemd/
  vllm@.service            # engine template (compute nodes); one instance per model
  litellm.service          # proxy (login node)
  litellm-postgres.service # key/spend store for LiteLLM (login node)
models/
  gemma4.conf              # example: multimodal + tools
  qwen3-32b.conf           # example: text, TP=4
litellm/
  litellm.yaml             # proxy routing table (secrets via os.environ, none stored)
litellm.env.example        # env template; real ~/litellm.env holds secrets (Bitwarden), never committed
```

## Host paths

These files are deployed to fixed locations. Home directories are per-machine (only `/mimer` is shared), so unit files and model configs must be placed on each relevant node.

| Repo file | Host path | Node |
|---|---|---|
| `systemd/vllm@.service` | `~/.config/systemd/user/vllm@.service` | each compute node |
| `models/<name>.conf` | `~/vllm/<name>.conf` | the compute node running that model |
| `systemd/litellm.service` | `~/.config/systemd/user/litellm.service` | login |
| `systemd/litellm-postgres.service` | `~/.config/systemd/user/litellm-postgres.service` | login |
| `litellm/litellm.yaml` | `~/litellm/litellm.yaml` | login |
| `litellm.env.example` -> `~/litellm.env` (filled in) | `~/litellm.env` | login |

## Deploy: add a model

On the target compute node.

1. Download weights, then get the snapshot path:
   ```bash
   export HF_HOME=/mimer/NOBACKUP/sll_dc/huggingface
   source /mimer/NOBACKUP/sll_dc/venv/bin/activate
   export HF_TOKEN=<Bitwarden>
   hf download <ORG/MODEL>
   ls -d /mimer/NOBACKUP/sll_dc/huggingface/hub/models--<ORG>--<MODEL>/snapshots/*/
   ```
2. Copy a `models/*.conf` as a template, set `HF_MODEL` to the snapshot path, `MODEL_NAME`, and a unique `VLLM_PORT`. Place it at `~/vllm/<name>.conf`.
3. Start and verify:
   ```bash
   systemctl --user reset-failed vllm@<name>
   systemctl --user start vllm@<name>
   podman logs -f vllm-<name>              # journalctl --user is unreliable on compute nodes
   curl -s http://localhost:8000/v1/models | python3 -m json.tool
   ```
4. Register in the proxy: add a `model_list` entry to `litellm/litellm.yaml` (and the live `~/litellm/litellm.yaml`), then on the login node `systemctl --user restart litellm`.

## Deploy: proxy + key store (one-time)

On the login node.

1. `cp litellm.env.example ~/litellm.env`, fill in a master key (`sk-...`) and DB password, `chmod 600 ~/litellm.env`. Store the values in Bitwarden.
2. `podman volume create litellm-pg-data`
3. Install the three unit files and `litellm/litellm.yaml` to their host paths.
4. Bring up (Postgres first; first LiteLLM start runs DB migrations, tens of seconds):
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now litellm-postgres
   systemctl --user reenable litellm && systemctl --user restart litellm
   ```

## Virtual (scoped) keys

The master key is admin-only. Issue one scoped key per consumer:

```bash
curl -s http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"models":["<ORG/MODEL>"],"key_alias":"<consumer>","metadata":{"owner":"<team>"}}'
```

Scoped keys are preferred over the shared master key: each is restricted to named models, carries its own budget and rate limit, is attributable per consumer for usage reporting, and can be revoked individually. A leaked scoped key is contained to one consumer and one model set.

## Operational notes

- **Podman crash-loop** (`status=125`; a bare `podman ps` also fails). Variants: "unhandled reboot has occurred" (stale `/tmp/storage-run-1001`) or "cannot re-exec process to join the existing user namespace" (stale `/run/user/1001/libpod`). Recover on the affected node:
  ```bash
  systemctl --user stop litellm litellm-postgres     # or vllm@<name>
  pkill -u "$(id -u)" -f 'podman|conmon|catatonit'; sleep 1
  rm -rf /run/user/$(id -u)/libpod /tmp/storage-run-$(id -u)/libpod /tmp/storage-run-$(id -u)/containers
  podman system migrate && podman ps
  systemctl --user reset-failed <unit> && systemctl --user start <unit>
  ```
  Do not add a `/run/user/.../libpod` wipe to `ExecStartPre` (it can disrupt a healthy container). Linger is enabled, so the login-node services auto-recover on reboot; `vllm@` model services are started manually.
- **Postgres shared-memory failures** ("could not open shared memory segment"): the unit must keep `--shm-size=256m` and `-c dynamic_shared_memory_type=mmap`.
- **Logs**: `journalctl --user` works on the login node; on compute nodes use `podman logs -f vllm-<name>`.
- **A100 has no usable FP8**: use BF16 or INT4 (AWQ/GPTQ), not FP8 quantisation.
- **Single-node only**: `vllm@.service` serves one model on one node (TP=4). Multi-node serving (Ray + pipeline parallelism over InfiniBand) is not configured here.

## Secrets

Never commit `~/litellm.env` or any filled-in env file (see `.gitignore`). Master key, DB password, and issued scoped keys live in Bitwarden.
