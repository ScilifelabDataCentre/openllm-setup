#!/usr/bin/env bash
TOKEN=$(kubectl --context scilifelab-2-prod -n openllm create token --duration $((30*24))h monitoring)
kubectl create secret generic -n loki-stack alloy-token-s2p \
        --from-literal=OPENLLM_K8S_TOKEN="${TOKEN}" \
        --from-literal=OPENLLM_K8S_APISERVER="${OPENLLM_K8S_APISERVER:-https://130.237.255.74:6443}" \
        -o json --dry-run=client |
  kubeseal --context scilifelab-1-dev -n loki-stack -w resources/token.ss.yaml
