# vLLM 02 SafeSpring Docker Deployment

This part of the repository contains instructions and manifests for deploying vLLM on the SafeSpring infrastructure.

## Setup

### Prepare the model data location

Create a volume and mount it as /data.
Then create a directory vllm_models for the model data.

```bash
sudo mkdir /data/vllm_models
sudo chown -R $USER:$USER /data/vllm_models
echo 'export HF_HOME=/data/vllm_models' >> ~/.bashrc
source ~/.bashrc
```

### Install pre-requisites

Install pipx and the huggingface CLI.

```bash
sudo apt install pipx
pipx ensurepath

pipx install huggingface_hub
source ~/.bashrc
huggingface-cli --version
```

### Clone the github repo on the VM

We do a sparse checkout to only fetch the vllm-02-safespring-docker directory.

```bash
cd ~
git clone --filter=blob:none --no-checkout https://github.com/ScilifelabDataCentre/openllm-setup.git
cd ./openllm-setup

git sparse-checkout init --cone
git sparse-checkout set vllm-02-safespring-docker
git checkout main
```

### Create the shared network

```bash
cd ./vllm-02-safespring-docker/deployments/network
docker compose up -d
docker network ls | grep vllm-net
```

## Configuration

Copy the .env.example file as .env and edit.

```bash
cd ./vllm-02-safespring-docker/deployments
cp ./.env.example .env
```

### Environment variables (.env)

This file defines configuration hardware-related settings and secrets.

Key settings
- VLLM_IMAGE_TAG: Docker image version for vLLM. Pin this for reproducibility (avoid latest in production).
- VLLM_API_KEY: Required for all API requests. Clients must include: Authorization: Bearer <API_KEY>
- HF_CACHE_DIR: Directory for Hugging Face cache and model storage. Should be large enough for model weights.
- HF_TOKEN (optional): Set if you intend to pull private or gated models.
- GPU assignments can override compose defaults
- Model service ports can override compose defaults

## Adding a new model deployment

In order to add and configure a new model:

1. Create a sub-directory under ./deployments for the model
2. Create the following file structure:
- compose.yaml
- Dockerfile (only if needed)
- vllm-config.yaml
- README.md
3. Pre-download the model to the host node/VM, for example from Hugginf Face (see below)

### Model deployment files explained

Each model deployment should contain the following files in a dedicated directory for the model.

1. compose.yaml
A Docker compose file.

2. vllm-config.yaml
vLLM configuration that specified the model, gives it an alias name, and specifies any model-vLLM specific configuration settings (such as batching, memory, performance, internal host/port settings).

3. A README file that describes how to start, stop, and test the model.

A custom Dockerfile should only be needed if additional packages or modifications need to be made to the vLLM image.

### Start a model service

Follow the instructions in the model's README.

## Download an LLM model

Use the Hugging Face CLI to pre-download a model to use.

Make sure models are stored on the persistent data volume:

```bash
export HF_HOME=/data/vllm_models
hf download Qwen/Qwen3-0.6B
du -sh /data/vllm_models
```

Note: Using hf download stores the model as: models--ORG--NAME

To remove a model, remove it from the /hub directory, for example:
```bash
rm -rf /data/vllm_models/hub/models--Qwen--Qwen3.5-9B
```

Verify that the space has been freed up:
```bash
du -sh /data/vllm_models
```

## Verify a model service

Start the vLLM service following instructions in the model README.

Verify it is up and running:
```bash
docker ps
docker logs <container-name> -f
```

## Test it

After the docker logs shows that model has been downloaded and that the vLLM service is up and running, you can proceed with some basic tests.
After some startup steps, the logs should show:
```
(APIServer pid=1) INFO:     Started server process [1]
(APIServer pid=1) INFO:     Waiting for application startup.
(APIServer pid=1) INFO:     Application startup complete.
```

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer your-long-random-secret"
```

Edit the model as needed:
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-long-random-secret" \
  -d '{
    "model": "qwen3-0.6b",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 10
  }'
```

## Troubleshooting

### Unauthorized errors
- Symptom: {"error":"Unauthorized"}
- Cause : API key is enabled but not provided in request.
- Fix: Include the API key:
```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer <VLLM_API_KEY>"
```

### Model not found / mismatch
- Symptom: model not found
- Cause: SERVED_MODEL_NAME does not match request or confusion between VLLM_MODEL vs SERVED_MODEL_NAME
- Fix: Use SERVED_MODEL_NAME in the request

### Model re-downloads every start
- Symptom: Model downloads every time container starts
- Cause: HF cache not persisted
- Fix: Ensure volume is mounted correctly

### Container fails to start
- Check logs:
```bash
docker compose logs -f
```

- Common causes:
    - invalid model name
    - insufficient GPU memory
    - missing HF_TOKEN for gated model

### Slow startup
- Cause:
    - first-time model download
    - model initialization
- Fix: Pre-download model (see instructions above)
