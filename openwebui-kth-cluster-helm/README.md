# Open WebUI KTH Cluster Helm Chart

This chart deploys Open WebUI with:

- a persistent volume for application data
- Open WebUI configured to reach a remote Ollama endpoint
- an optional bundled PostgreSQL dependency for the primary application database
- a bundled Qdrant service as the default vector database backend
- optional external vLLM connectivity through the OpenAI-compatible API
- optional external PostgreSQL, Redis, and vector database configuration for safer remote deployments
- an optional ingress for local or cluster HTTP access

## Default configuration

The chart defaults currently include:

- namespace: `openllm`
- release object name: `open-webui`
- Open WebUI image: `ghcr.io/scilifelabdatacentre/open-webui:git-5c3b3dd`
- Ollama URL: `set-to-your-ollama-url`
- vLLM endpoints: none configured
- persistence: enabled, `ReadWriteOnce`, `5Gi`
- gateway hostnames: `["openllm.scilifelab-2-dev.sys.kth.se"]`

For this deployment, Open WebUI images are built in `https://github.com/ScilifelabDataCentre/open-webui`.
The image artifacts are published under `ghcr.io/scilifelabdatacentre/open-webui:main`.
Documentation for the Open WebUI image build process is in the `open-webui` [repository README](https://github.com/ScilifelabDataCentre/open-webui/blob/scilifelab/main/SCILIFELAB_README.md).

## Remote deployment baseline

For remote or shared deployments, do not rely on the default SQLite-only setup on a Kubernetes PVC.
Open WebUI upstream recommends:

- PostgreSQL for the main application database
- Redis for shared state and future scaling
- `UVICORN_WORKERS=1` in Kubernetes
- a client-server vector database such as `qdrant` or `milvus` if you use RAG at scale

This chart now exposes first-class values for those settings under:

- `database`
- `postgresql`
- `redis`
- `vectorDatabase`
- `webui.uvicornWorkers`

The tracked [values.yaml](/Users/nikch187/Projects/sll/openllm-setup/openwebui-kth-cluster-helm/values.yaml) is intended to be the remote deployment baseline.
Keep secrets and environment-specific overrides in `values-local.yaml`.

If `database.url` is empty and `postgresql.enabled=true`, Open WebUI is wired to the bundled PostgreSQL dependency automatically using `DATABASE_TYPE`, `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_USER`, `DATABASE_PASSWORD`, and `DATABASE_NAME`.
If `database.url` is set, that external database takes precedence.
The chart also waits for the bundled PostgreSQL service before starting Open WebUI, because upstream exits on initial connection refusal instead of retrying cleanly.

## PostgreSQL secret

`postgresql.auth.existingSecret` is the single Secret name used by both the Bitnami PostgreSQL dependency and Open WebUI. When it is set, Bitnami will not create its own credentials Secret.

For ArgoCD deployments, keep this Secret managed outside Helm after the first PostgreSQL boot. Changing the Kubernetes Secret alone does not rotate the password stored for the `openwebui` role inside an already-initialized PostgreSQL data volume; new Open WebUI pods will read the new Secret and fail authentication.

The chart defaults `postgresql.auth.createSecret=false`. If `postgresql.auth.password` or `postgresql.auth.postgresPassword` are set while `createSecret=false`, rendering fails so a private values overlay cannot silently desynchronize the Secret from PostgreSQL. Set `createSecret=true` only for first-time bootstrap before the PostgreSQL PVC is initialized. After bootstrap, rotate credentials by changing both the Kubernetes Secret and the PostgreSQL role password in the database.

For Kubernetes/non-root deployments, the chart also mounts a writable `emptyDir` at `/app/backend/open_webui/static`.
This avoids the known upstream permission errors when Open WebUI tries to write generated static assets like `favicon.png`, `custom.css`, and `loader.js`.

## Qdrant

This chart now defaults Open WebUI to `VECTOR_DB=qdrant`.

The Open WebUI environment variables used here follow the upstream reference:

- `QDRANT_URI`
- `QDRANT_API_KEY`
- `QDRANT_ON_DISK`
- `QDRANT_PREFER_GRPC`
- `QDRANT_GRPC_PORT`
- `QDRANT_TIMEOUT`
- `QDRANT_HNSW_M`
- `ENABLE_QDRANT_MULTITENANCY_MODE`
- `QDRANT_COLLECTION_PREFIX`

The chart deploys Qdrant by default when `vectorDatabase.type=qdrant` and `vectorDatabase.qdrant.enabled=true`.
Set the connection details and bundled service options in `values-local.yaml` or another environment-specific override:

```yaml
vectorDatabase:
  type: qdrant
  qdrant:
    enabled: true
    uri: ""
    scheme: http
    serviceName: ""
    namespace: ""
    port: "6333"
    onDisk: false
    preferGrpc: false
    grpcPort: "6334"
    timeout: "5"
    hnswM: "16"
    enableMultitenancyMode: true
    collectionPrefix: open-webui
    auth:
      enabled: false
      existingSecret: ""
      secretName: ""
      secretKey: api-key
      value: ""
    image:
      repository: qdrant/qdrant
      tag: v1.17.1-unprivileged
      pullPolicy: IfNotPresent
    persistence:
      enabled: true
      existingClaim: ""
      accessModes:
        - ReadWriteOnce
      size: 20Gi
```

If `uri` is empty, the chart builds `QDRANT_URI` automatically as `scheme://serviceName.<namespace>.svc.cluster.local:port`, where `serviceName` defaults to `<release>-qdrant` and `namespace` defaults to the release namespace. Override `uri` directly for an external Qdrant endpoint, or set `vectorDatabase.qdrant.enabled=false` if you do not want the bundled Qdrant Deployment/Service/PVC.
If you already have embeddings stored in a previous vector backend, reindex them after switching. Open WebUI's Qdrant multitenancy mode is enabled by default here, matching the upstream recommendation for lower RAM usage.
If `vectorDatabase.qdrant.auth.enabled=true` and `existingSecret` is empty, this chart creates a Secret for `QDRANT_API_KEY`. If `auth.value` is empty, Helm generates a stable random API key and reuses it on upgrades via `lookup`.
Open WebUI also waits for the bundled Qdrant service before starting, so first boot does not fail on an early connection attempt.

## External vLLM

To point Open WebUI at one or more external vLLM deployments, set the OpenAI-compatible endpoints under `webui`:

```yaml
webui:
  openaiEndpoints:
    - baseUrl: http://your-vllm-host-1:8000/v1
    - baseUrl: http://your-vllm-host-2:8000/v1
  openaiApiKeysSecret:
    name: open-webui-openai
    key: api-keys
```

If any endpoint requires authentication, store the semicolon-delimited key list in a Kubernetes Secret and point `webui.openaiApiKeysSecret` at it. The key order must match `webui.openaiEndpoints`. The chart renders these as `OPENAI_API_BASE_URLS` and `OPENAI_API_KEYS`, matching upstream Open WebUI behavior.

## ArgoCD deployment

This chart is deployed via ArgoCD on the KTH cluster:

- **App name:** `open-llm-dev`
- **URL:** https://openllm.scilifelab.se/

ArgoCD tracks the `main` branch and syncs the chart from the `openwebui-kth-cluster-helm` directory. Environment-specific overrides and secrets are managed outside the chart through ArgoCD's values overlays or a gitignored `values-local.yaml`.

## Backups

The files under `openwebui-kth-cluster-helm/backups/` are not part of the Helm release. They are managed separately with kustomize and are excluded from packaged chart artifacts via `.helmignore`.

Apply the backup resources:

```bash
kubectl apply -k ./openwebui-kth-cluster-helm/backups
```

This creates the backup PVC, backup and restore CronJobs, the Rubrik `ProtectionSet`, and the PostgreSQL backup `NetworkPolicy` in the `openllm` namespace.

To run a backup immediately:

```bash
kubectl -n openllm create job --from=cronjob/postgresql-backup postgresql-backup-manual-$(date +%s)
```

The helper pod manifest `backups/backup-pvc-shell-pod.yaml` is intentionally not included in `kustomization.yaml`; apply it only when you need an interactive pod mounted to the backup PVC.

## Usage

Render the manifests:

```bash
helm dependency update ./openwebui-kth-cluster-helm

helm template open-webui ./openwebui-kth-cluster-helm \
  --namespace llm-stack \
  -f ./openwebui-kth-cluster-helm/values.yaml \
  -f ./openwebui-kth-cluster-helm/values-local.yaml
```

Install the release:

```bash
helm upgrade --install open-webui ./openwebui-kth-cluster-helm \
  --namespace llm-stack \
  --create-namespace \
  -f ./openwebui-kth-cluster-helm/values.yaml \
  -f ./openwebui-kth-cluster-helm/values-local.yaml
```

Keep secrets and local-only overrides in `values-local.yaml`, which is gitignored. This includes `database.url`, `redis.url`, and secret references such as `webui.openaiApiKeysSecret`.

## Values overview

The main configuration sections in [values.yaml](/Users/nikch187/Projects/sll/openllm-setup/openwebui-kth-cluster-helm/values.yaml) are:

- `namespace`: namespace creation, name, and annotations
- `nameOverride` and `fullnameOverride`: release naming
- `persistence`: PVC creation, access mode, size, or reuse of an existing claim
- `podSecurityContext`: pod-level security settings for the workload
- `webui`: image, port, Ollama URL, OpenAI-compatible API settings, auth, resources, probes, and extra env vars
- `database`: external database URL, migration control, connection pool tuning, and SQLite fallback tuning
- `postgresql`: bundled Bitnami PostgreSQL dependency configuration for the primary application database
- `redis`: Redis URL and connection behavior for multi-user or future multi-replica setups
- `vectorDatabase`: vector backend selection, bundled Qdrant deployment, auth, and tuning
- `service`: Kubernetes Service type and ports
- `gateway`: Gateway API host routing
- `networkPolicy`: egress policy enablement for the deployment pods
- `tmpVolume`: `/tmp` emptyDir mount enablement
- `staticVolume`: writable `/app/backend/open_webui/static` mount for non-root deployments

Manage `WEBUI_SECRET_KEY` in the `openllm-secrets` Secret before production use. If any external vLLM endpoint requires authentication, also provide `webui.openaiApiKeysSecret`.

If you scale beyond one pod, also ensure:

- `database.url` points to PostgreSQL, not SQLite
- `redis.url` is set
- `WEBUI_SECRET_KEY` is identical across replicas

The bundled PostgreSQL dependency is only for the main relational database in this chart. Vector storage uses the bundled Qdrant service by default.

## Network policy

If the cluster uses default-deny egress, keep `networkPolicy.enabled: true` so the Open WebUI pod is allowed to make outbound connections.
