# Log monitoring

Configure Alloy on cluster `scilifelab-1-dev` to scrape pod logs from the `openllm` namespace on `scilifelab-2-prod`.

This is meant as a temporary workaround, until an observability stack is available for `scilifelab-2-prod`.

## General idea

Alloy discovers `openllm` pods through the `scilifelab-2-prod` API server, and streams their logs to Loki.

Authentication is performed through a bearer token for the `monitoring` service account,
installed in the `openllm` namespace on `scilifelab-2-prod` with suitable permissions (see [rbac](./rbac))

The `scilifelab-2-prod` control plane address is given as a node IP,
which must be updated when new control plane nodes are created, e.g. after a maintenance window (see [Rotating API server credentials](#rotating-api-server-credentials) below).

Using the Rancher proxy URL would have given a stable address,
but authenticating with a downstream service account is currently disabled.

# Installation

1. Install the RBAC resources on `scilifelab-2-prod`, allowing to discover pods and stream logs.
```
kubectl --context scilifelab-2-prod -n openllm apply -k rbac
```

2. Install Alloy in the `loki-stack` namespace on `scilifelab-1-dev`
```
kubectl --context scilifelab-1-dev -n loki-stack apply -k .
```

# Rotating credentials
To update the service account token and the `scilifelab-2-prod` API server URL from a freshly downloaded Kubeconfig:
```
KUBECONFIG=/path/to/scilifelab-2-prod.yaml ./helpers.sh rotate_token
```

Alternatively, set the environment variables `OPENLLM_K8S_TOKEN` and `OPENLLM_K8S_APISERVER` directly:
```
export OPENLLM_K8S_TOKEN=<token>
export OPENLLM_K8S_APISERVER=<node IP>
./helpers.sh rotate
```

Finally, apply the updated sealed secret and resart Alloy:
```
kubectl apply -f resources/token.ss.yaml
kubectl rollout restart deploy alloy-openllm-logs
```

# Tweaking and debugging

Edit the [Alloy configuration](./files/config.alloy), following the the excellent [documentation](https://grafana.com/docs/alloy/latest/)

I recommend debugging locally using the provided [compose file](./debug/docker-compose.yaml):
```
export OPENLLM_K8S_TOKEN="$(./helpers.sh token)"
export OPENLLM_APISERVER_URL=$(./helpers.sh url)
docker compose up -d -f debug/docker-compose.yaml
```

Unfortunately, log collection might only work from the KTH network due to firewall consideration when using node IPs.
But you can still catch syntax errors in the Alloy config simply by running:
```
docker compose -f debug/docker-compose.yaml up alloy
```
