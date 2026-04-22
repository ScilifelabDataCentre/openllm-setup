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

## Configuration

Copy the .env.example file as .env and edit as needed.

```bash
cd ./vllm-02-safespring-docker/vllm-deployment
cp ./.env.template .env
```

In particular,
- Specify the LLM model to be used using env var VLLM_MODEL.
- Give the model a client friendly API contract name as SERVED_MODEL_NAME.
- Set an HF_TOKEN if you intend to pull private or gated models.

Also edit the vLLM configuration in the file vllm-config.yaml

## Start the vLLM service

```bash
docker compose up -d
```

Verify it is up and running:
```bash
docker ps
docker logs vllm_prod -f
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
