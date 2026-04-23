# Open WebUI KTH Cluster Helm Chart

This chart deploys Open WebUI with:

- a persistent volume for application data
- a privileged Tailscale sidecar for tailnet access
- Open WebUI configured to reach a remote Ollama endpoint
- optional external vLLM connectivity through the OpenAI-compatible API
- an optional ingress for local or cluster HTTP access

## Default configuration

The chart defaults currently include:

- namespace: `llm-stack`
- release object name: `open-webui`
- Open WebUI image: `ghcr.io/open-webui/open-webui:main`
- Ollama URL: `http://100.111.61.121:11434`
- vLLM URL: `http://vllm.example.com:8000/v1`
- service account: `open-webui-tailscale`
- tailscale state secret: `open-webui-tailscale-state`
- persistence: enabled, `ReadWriteOnce`, `5Gi`
- ingress host: `open-webui.localhost`

## External vLLM

To point Open WebUI at an external vLLM endpoint, set the OpenAI-compatible base URL under `webui.vllm`:

```yaml
webui:
  vllm:
    baseUrl: http://your-vllm-host:8000/v1
    apiKey: ""
```

If your vLLM endpoint requires authentication, set `webui.vllm.apiKey`.

## Usage

Render the manifests:

```bash
helm template open-webui ./openwebui-kth-cluster-helm \
  --namespace llm-stack \
  -f ./openwebui-kth-cluster-helm/values-local.yaml
```

Install the release:

```bash
helm upgrade --install open-webui ./openwebui-kth-cluster-helm \
  --namespace llm-stack \
  --create-namespace \
  -f ./openwebui-kth-cluster-helm/values-local.yaml
```

Keep secrets and local-only overrides in `values-local.yaml`, which is gitignored. This includes `webui.secretKey` and any non-empty `webui.vllm.apiKey`.

## Values overview

The main configuration sections in [values.yaml](/Users/nikch187/Projects/sll/openllm-setup/openwebui-kth-cluster-helm/values.yaml) are:

- `namespace`: namespace creation, name, and annotations
- `nameOverride` and `fullnameOverride`: release naming
- `serviceAccount`: ServiceAccount creation and name
- `rbac`: Role/RoleBinding creation and the Tailscale state secret name
- `persistence`: PVC creation, access mode, size, or reuse of an existing claim
- `podSecurityContext`: pod-level security settings for the workload
- `webui`: image, ports, Ollama URL, vLLM URL, auth, secret key, resources, probes, and extra env vars
- `tailscale`: sidecar enablement, image, auth secret, env, and security context
- `service`: Kubernetes Service type and ports
- `ingress`: ingress enablement, class, annotations, and hosts
- `networkPolicy`: egress policy enablement for the deployment pods
- `tmpVolume`: `/tmp` emptyDir mount enablement

## Tailscale auth secret

The Tailscale sidecar reads `TS_AUTHKEY` from a secret named `tailscale-auth`.

If `tailscale.authSecret.value` is set, the chart creates that Secret for you. The intended place for that value is `values-local.yaml`:

```yaml
tailscale:
  authSecret:
    value: tskey-auth-xxxxxxxx
```

If you prefer managing the Secret outside the chart, leave `tailscale.authSecret.value` empty and create it manually:

```bash
kubectl -n llm-stack create secret generic tailscale-auth \
  --from-literal=TS_AUTHKEY=tskey-auth-xxxxxxxx
```

Override `webui.secretKey` before production use. If your external vLLM endpoint requires authentication, also set `webui.vllm.apiKey`.

## Network policy

If the cluster uses default-deny egress, keep `networkPolicy.enabled: true` so the Open WebUI pod and Tailscale sidecar are allowed to make outbound connections.
