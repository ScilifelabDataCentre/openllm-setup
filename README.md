# Open LLM Setup

## Purpose

This repository contains setup files and scripts for multiple deployment targets (services) that make up the SciLifeLab Open LLM service, including:

- The main Open WebUI deployment (on KTH production cluster)
- A vLLM service (on KTH cluster)
- A vLLM service (on SafeSpring using an H100 GPU)

## Directory structure

Deployment folder names follow the below template names: {what deployed}-{optional nr}-{where deployed}-{how deployed}

```
/openllm-setup
    /openwebui-kth-cluster-helm         This contains helm charts for the Open WebUI KTH cluster deployment.
    /vllm-02-safespring-docker          This contains docker compose files for the vLLM deployment on SafeSpring 02 VM.
```

## Branch strategy

In this repository we merge to main from short-lived feature branches.
