# Gemma3 27b

Generic small model from Google.
Served by vLLM with an OpenAI-compatible endpoint at http://<host>:8000/v1

## Requirements
- ~55 GB weights in bf16
- KV cache: reserve at least ~16 GB (covers 5 concurrent users at 32K context each, ~3 GB/user).
- Single GPU H100 works

## Deploy

```bash
cd deployments/gemma3-27b
docker compose up -d --build
```

## Test

```bash
curl http://localhost:8000/v1/models -H "Authorization: Bearer $VLLM_API_KEY"
```

Test using a .wav audio file on the client machine:
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $VLLM_API_KEY" \
  -d '{
    "model": "gemma3-27b",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }'
```

## Notes
- Served model name (alias used in requests): `gemma3-27b`
  (the full HF id `google/gemma-3-27b-it` is only used at load time)
- Endpoint port: 8000 (mapped to container 8000)
- Register the endpoint in Open WebUI as an OpenAI-compatible connection.