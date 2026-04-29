# Tailscale sidecar in this chart

## Why Tailscale is used here

The Open WebUI pod needs to reach services that are not exposed on the public internet — in particular an Ollama instance running on a separate VM that is reachable only over the Tailscale tailnet (e.g. `100.111.61.121:11434`). The Tailscale sidecar joins the pod to that tailnet, giving it a Tailscale IP and kernel WireGuard routing. Without it, `OLLAMA_BASE_URL` would point at an unreachable private address.

The same tunnel lets other tailnet members reach the pod directly by its Tailscale hostname, independently of the cluster Ingress.

## How it maps onto the generic sidecar pattern

The generic pattern (see [Tailscale Kubernetes docs](https://tailscale.com/kb/1185/kubernetes#sample-sidecar)) requires four things: a Secret with the auth key, a ServiceAccount, a Role/RoleBinding so the sidecar can write its state into a Kubernetes Secret, and the sidecar container itself injected alongside the main workload. All four are managed by this chart.

```
templates/
  secret-tailscale-auth.yaml   ← auth key Secret (optional, created from values)
  serviceaccount.yaml          ← ServiceAccount the pod runs as
  rbac.yaml                    ← Role + RoleBinding for state secret access
  deployment.yaml              ← ts-sidecar container inside the Open WebUI pod
```

## The sidecar container

When `tailscale.enabled=true`, `deployment.yaml` appends a second container to the pod spec:

```yaml
- name: ts-sidecar
  image: ghcr.io/tailscale/tailscale:latest
  imagePullPolicy: Always
  env:
    - name: TS_KUBE_SECRET          # secret used to persist WireGuard state across restarts
      value: open-webui-tailscale-state
    - name: TS_USERSPACE
      value: "false"                # kernel WireGuard; requires privileged: true below
    - name: TS_DEBUG_FIREWALL_MODE
      value: auto                   # lets Tailscale pick iptables or nftables automatically
    - name: TS_AUTHKEY              # read from the tailscale-auth Secret
      valueFrom:
        secretKeyRef:
          name: tailscale-auth
          key: TS_AUTHKEY
          optional: true            # pod starts even without the key; authenticate manually if needed
    - name: POD_NAME
      valueFrom:
        fieldRef:
          fieldPath: metadata.name
    - name: POD_UID
      valueFrom:
        fieldRef:
          fieldPath: metadata.uid
  securityContext:
    privileged: true
    runAsUser: 0
    runAsGroup: 0
    runAsNonRoot: false
```

The sidecar shares the pod's network namespace, so WireGuard routes it creates are immediately visible to the `open-webui` container — no proxy or extra configuration needed for outbound traffic to the tailnet.

## RBAC

`rbac.yaml` creates a Role that gives the ServiceAccount three permissions in the release namespace:

| Resource | Verbs | Purpose |
|---|---|---|
| `secrets` (any name) | `create` | First-time state secret creation |
| `secrets` (`open-webui-tailscale-state`) | `get`, `update`, `patch` | Persist WireGuard keys across pod restarts |
| `events` | `get`, `create`, `patch` | Tailscale diagnostic events |

The secret name `open-webui-tailscale-state` is controlled by `rbac.stateSecretName` in `values.yaml` and passed to the sidecar as `TS_KUBE_SECRET`. Both must match.

## Auth key

Use an **ephemeral** auth key from the [Tailscale Admin Console → Keys](https://login.tailscale.com/admin/settings/keys). An ephemeral key removes the node from the tailnet automatically when the pod terminates, keeping the device list accurate.

### Option A — let the chart create the Secret

Set the key in `values-local.yaml` (this file is gitignored):

```yaml
tailscale:
  authSecret:
    value: tskey-auth-<your-key-here>
```

`secret-tailscale-auth.yaml` creates the `tailscale-auth` Secret only when both `tailscale.enabled` and `tailscale.authSecret.value` are non-empty.

### Option B — create the Secret manually

Leave `tailscale.authSecret.value` empty and apply the secret yourself before or after installing the chart:

```bash
kubectl -n llm-stack create secret generic tailscale-auth \
  --from-literal=TS_AUTHKEY=tskey-auth-<your-key-here>
```

Because `authSecret.optional: true`, the pod starts even if the secret is absent. If no key is provided at startup, retrieve the one-time login URL from the sidecar logs:

```bash
kubectl -n llm-stack logs <pod-name> -c ts-sidecar
```

## Relevant values

```yaml
# values.yaml (defaults)

serviceAccount:
  create: true
  name: tailscale                  # ServiceAccount name used by the pod

rbac:
  create: true
  stateSecretName: open-webui-tailscale-state  # must match TS_KUBE_SECRET

tailscale:
  enabled: true
  image:
    repository: ghcr.io/tailscale/tailscale
    tag: latest
    pullPolicy: Always
  authSecret:
    name: tailscale-auth           # Secret that holds TS_AUTHKEY
    key: TS_AUTHKEY
    optional: true
    value: ""                      # set this in values-local.yaml, not here
  env:
    userspace: "false"             # kernel WireGuard
    debugFirewallMode: auto
  securityContext:
    privileged: true
    runAsUser: 0
    runAsGroup: 0
    runAsNonRoot: false
```

## Disabling Tailscale

If the target cluster has direct routed access to all required services and Tailscale is not needed:

```yaml
tailscale:
  enabled: false
```

When disabled, no sidecar container is added, no auth Secret is created, and the RBAC objects are still created (they are controlled by `rbac.create` independently). The pod's `securityContext.runAsNonRoot: true` policy from `podSecurityContext` applies cleanly because the privileged sidecar is absent.

## Verifying connectivity

After the pod is running, confirm the sidecar registered on the tailnet:

```bash
kubectl -n llm-stack logs <pod-name> -c ts-sidecar | grep "Tailscale"
```

From another tailnet device, reach the pod by its Tailscale hostname (the pod name by default):

```bash
tailscale ping <pod-name>
curl http://<pod-name>:8080/health
```

To check the pod can reach the Ollama VM over the tailnet:

```bash
kubectl -n llm-stack exec <pod-name> -c open-webui -- \
  curl -s http://100.111.61.121:11434/api/tags
```