#!/usr/bin/env bash
set -eu -o pipefail

openllm_k8s_apiserver() {
  kubectl config view -o json |
    jq -r '.clusters |
             map(select(.name | test("scilifelab-2-prod-controlplane"))) |
             first |
             .cluster.server'
}

openllm_k8s_token() {
  kubectl --context scilifelab-2-prod -n openllm create token --duration $((30*24))h monitoring
}

openllm_k8s_rotate_token() {
  kubectl create secret generic -n loki-stack alloy-token-s2p \
          --from-literal=OPENLLM_K8S_TOKEN="${OPENLLM_K8S_TOKEN:-$(openllm_k8s_token)}" \
          --from-literal=OPENLLM_K8S_APISERVER="${OPENLLM_K8S_APISERVER:-$(openllm_k8s_apiserver)}" \
          -o json --dry-run=client |
    kubeseal --context scilifelab-1-dev -n loki-stack -w resources/token.ss.yaml
}

case "${1:-}" in
  r|rotate_token|openllm_k8s_rotate_token)
    openllm_k8s_rotate_token ;;
  a|apiserver|openllm_k8s_apiserver)
    openllm_k8s_apiserver ;;
  t|token|openllm_k8s_token)
    openllm_k8s_token ;;
  *)
    echo "Usage: $0 rotate|url|token" >&2
    exit 1 ;;
esac
