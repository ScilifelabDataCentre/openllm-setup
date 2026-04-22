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
git clone --filter=blob:none --no-checkout git clone https://github.com/ScilifelabDataCentre/openllm-setup.git
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

Edit the vLLM configuration in the file vllm-config.yaml

## Start the vLLM service

```bash
docker compose up -d
```
