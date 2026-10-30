# Repository guidance

## Purpose and layout

This repository configures the SciLifeLab Open LLM service. It contains deployment
manifests, operational scripts, and Open WebUI extensions; it does not contain the
Open WebUI application source or its image build pipeline.

- `openwebui-kth-cluster-helm/`: main Helm chart, including PostgreSQL, Qdrant,
  optional vLLM embeddings, networking, and backup/restore resources.
- `embeddings-kth-cluster-helm/`: remaining standalone embeddings environment
  values. This directory currently has no `Chart.yaml`; bundled embeddings are
  implemented in the main Open WebUI chart. Older deployment examples may be stale.
- `openwebui-functionality/`: Python Functions installed through the Open WebUI
  admin interface, plus pytest tests and dependency configuration.
- `vllm-02-safespring-docker/`: SafeSpring vLLM deployment using Docker Compose.
- `vllm-03-c3se-podman/`: C3SE single-node and multi-node vLLM services, model
  configuration, and the LiteLLM/PostgreSQL proxy, using Podman and user systemd.
- `monitoring/`: Kustomize resources for the OpenTelemetry bridge and remote logs.

Read the relevant component README before changing it. Use current manifests and
configuration to resolve outdated examples. Deployment folder names follow
`{what}-{optional-number}-{where}-{how}`.

## Working conventions

- Check `git status` before editing and preserve unrelated local changes and
  untracked files. Local operational scripts and database dumps are not fixtures.
- Keep changes focused on the requested component. Follow nearby formatting;
  avoid unrelated reformatting or dependency upgrades.
- Use short-lived feature branches targeting `main`, as described in `README.md`.
- Update the component README when changing configuration, setup, or operational
  behavior. Keep examples free of real credentials.
- Keep image and dependency versions explicit. For intentional Helm dependency
  changes, update `Chart.yaml` and `Chart.lock` together.

## Component constraints

### Open WebUI Functions

- When creating a new Function, read and apply the
  [create-openwebui-function skill](.agents/skills/create-openwebui-function/SKILL.md).
- Whenever adding, modifying, renaming, or removing Python files under
  `openwebui-functionality/functions/`, automatically read and apply the
  [sync-openwebui-function-docs skill](.agents/skills/sync-openwebui-function-docs/SKILL.md)
  before completing the task. Keep `openwebui-functionality/README.md` synchronized
  in the same change; no separate documentation request is needed.
- Target Python 3.10 or later, as specified in `pyproject.toml`.
- Functions are pasted individually into Open WebUI. Preserve their metadata
  headers, entry-point classes, injected argument names, and Pydantic Valves.
  Avoid introducing repository-local imports that will fail after installation.
- Whenever adding, modifying, renaming, or removing Python files under
  `openwebui-functionality/functions/`, automatically read and apply the
  [sync-openwebui-function-tests skill](.agents/skills/sync-openwebui-function-tests/SKILL.md).
  Review coverage in `openwebui-functionality/tests/`, make necessary test updates,
  and run relevant tests before completing the task. If no updates are needed,
  explain why. Mock Open WebUI internals and external services; unit tests must
  not send real emails or access deployment databases.
- Request Quota Guard and Rate Limit Enforcer are alternatives; do not configure
  both as active filters.

### Deployments

- The KTH Open WebUI deployment is managed through ArgoCD. Treat changes to
  `main` as potentially deployable configuration. Local validation should render
  manifests without applying them to a cluster.
- Preserve resource names, Deployment selectors, PVCs, and backup retention
  behavior unless the task explicitly includes a migration. Read the chart README
  before changing PostgreSQL secrets or backup/restore resources: changing a
  Kubernetes Secret alone does not rotate an initialized database's password.
- For C3SE, change configuration in this repository and deploy through the
  documented pull/restart workflow. Keep the README's Current state and Node
  allocation tables updated with model or node changes. There is no scheduler;
  retain node-sharing checks and keep model names consistent with LiteLLM routes.
- For SafeSpring, keep deployment settings in Compose/environment configuration
  and vLLM server tuning in `vllm-config.yaml`.

## Validation

Run checks relevant to the files changed. There is no repository-wide test runner
or configured CI workflow. Commands below run from the repository root unless a
different directory is shown.

For Python extensions, use the documented test dependencies:

```sh
cd openwebui-functionality
uv run --extra test pytest
```

For the main Helm chart, restore locked dependencies if needed, then lint and
render locally:

```sh
helm dependency build ./openwebui-kth-cluster-helm
helm lint ./openwebui-kth-cluster-helm
helm template open-webui ./openwebui-kth-cluster-helm --namespace openllm > /tmp/openllm-rendered.yaml
```

Also render the affected optional settings with non-secret test overrides; inspect
the resulting resources and environment variables. Avoid dependency updates merely
to validate an unrelated chart change.

For monitoring configuration:

```sh
kustomize build --enable-helm ./monitoring/otel-bridge
kubectl kustomize ./monitoring/remote-logs
kubectl kustomize ./monitoring/remote-logs/rbac
```

For shell changes, run `bash -n path/to/changed-script.sh`. For SafeSpring Compose
changes, validate without starting containers or printing interpolated secrets:

```sh
cd vllm-02-safespring-docker/vllm-deployment
docker compose --env-file .env.example config --quiet
```

For documentation-only edits, verify paths, commands, and `git diff --check`.
Report which checks ran and any unavailable tooling or runtime validation still
needed. Rendering and syntax checks do not establish GPU or cluster health.

## Secrets and operational data

- Keep credentials in the documented secret stores, Kubernetes Secrets, or ignored
  local configuration. Do not commit `.env`, `values-local.yaml`, kubeconfigs,
  private keys, database dumps, or rendered manifests containing credentials.
- Do not inspect or print local secret files or dump contents unless needed for
  the requested task. Use placeholders in documentation and test overrides.
- Do not run bootstrap, migration, restore, secret rotation, cluster apply,
  service restart, or model-download commands as routine validation. Run such
  operations only within the user's authorized scope, using explicit target
  environments and namespaces where applicable.
