# deployments/voxtral-small-24b/Dockerfile
FROM vllm/vllm-openai:latest

# Audio/multimodal extra required for Voxtral (AgglomerativeAudioTokenizer)
RUN pip install --no-cache-dir "vllm[audio]"