# Open LLM Setup

## Purpose

This repository contains setup files and scripts for multiple deployment targets (services) that make up the SciLifeLab Open LLM service, including:

- The main Open WebUI deployment (on KTH production cluster)
- Open WebUI addon functionality definitions as OI Function filters, actions, valves, etc
- A vLLM service (on KTH cluster)
- A vLLM service (on SafeSpring using an H100 GPU)

## Directory structure

Deployment folder names follow the below template names: {what deployed}-{optional nr}-{where deployed}-{how deployed}. This convention is followed for normal cluster deployment objects but not for other components such as python code and scripts to be installed or created via the Open WebUI application.

```
/openllm-setup
    /openwebui-kth-cluster-helm         This contains helm charts for the Open WebUI KTH cluster deployment.
    /vllm-02-safespring-docker          This contains docker compose files for the vLLM deployment on SafeSpring 02 VM.
```

## Live deployments

| Environment | ArgoCD app name | URL |
|---|---|---|
| Dev | `open-llm-dev` | https://openllm.scilifelab.se/ |

The `openwebui-kth-cluster-helm` chart is managed via ArgoCD on the KTH cluster.

## Branch strategy

In this repository we merge to main from short-lived feature branches.
