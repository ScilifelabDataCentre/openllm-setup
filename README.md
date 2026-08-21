# Open LLM Setup

## Purpose

This repository contains setup files and scripts for multiple deployment targets (services) that make up the SciLifeLab Open LLM service, including:

- The main Open WebUI deployment (on KTH production cluster)
- Open WebUI addon functionality definitions as OI Function filters, actions, valves, etc
- A vLLM service (on KTH cluster)
- A vLLM service (on SafeSpring using an H100 GPU)
- A vLLM BGE-M3 embeddings service (on KTH cluster), backing Open WebUI's RAG and exposed to clients via an OpenAI-compatible connection

## Directory structure

Deployment folder names follow the below template names: {what deployed}-{optional nr}-{where deployed}-{how deployed}. This convention is followed for normal cluster deployment objects but not for other components such as python code and scripts to be installed or created via the Open WebUI application.

```
/openllm-setup
    /openwebui-kth-cluster-helm     Helm charts for the Open WebUI deployment on the KTH cluster.
    /embeddings-kth-cluster-helm    Helm chart for the BGE-M3 embeddings backend (vLLM) on the KTH cluster, backing Open WebUI RAG.
    /vllm-02-safespring-docker      Docker Compose files for the vLLM deployment on the SafeSpring 02 VM.
```

## Live deployments

| Environment | ArgoCD app name | URL |
| --- | --- | --- |
| Dev | open-llm-dev | https://openllm.scilifelab.se/ |
| Dev | openllm-embeddings | Internal (ClusterIP): http://embeddings.llm-embeddings.svc.cluster.local:8000/v1 |

The `openwebui-kth-cluster-helm` and `embeddings-kth-cluster-helm` charts are managed via ArgoCD on the KTH cluster.

## Branch strategy

In this repository we merge to main from short-lived feature branches.