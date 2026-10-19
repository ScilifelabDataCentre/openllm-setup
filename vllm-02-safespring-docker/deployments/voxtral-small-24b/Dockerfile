# deployments/voxtral-small-24b/Dockerfile
FROM vllm/vllm-openai:v0.32.0-ubuntu2404

# Audio/multimodal extra required for Voxtral (AgglomerativeAudioTokenizer)
RUN pip install --no-cache-dir "vllm[audio]"