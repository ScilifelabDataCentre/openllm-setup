# Voxtral Small 24B (2507)

Speech-to-text / audio understanding model from Mistral. Served by vLLM with an
OpenAI-compatible endpoint at http://<host>:8001/v1

## Requirements
- vLLM image with `vllm[audio]` extra (see Dockerfile)
- ~48 GB weights in bf16, ~50+ GB VRAM to serve bf16
- Single GPU works only with a quantized variant; bf16 needs 2 GPUs (tp=2)

## Deploy

```bash
cd deployments/voxtral-small-24b
docker compose up -d --build
```

## Test

```bash
curl http://localhost:8001/v1/models -H "Authorization: Bearer $VLLM_API_KEY"
```

Test using a .wav audio file on the client machine:
```bash
curl http://localhost:8001/v1/audio/transcriptions \
  -H "Authorization: Bearer $VLLM_API_KEY" \
  -F "model=voxtral-small-24b" \
  -F "file=@/path/to/test.wav"
```

Test using base64 audio data:
```bash
curl http://localhost:8001/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $VLLM_API_KEY" \
    -d '{
    "model": "voxtral-small-24b",
    "messages": [{
        "role": "user",
        "content": [
        {"type": "text", "text": "Transcribe this audio."},
        {"type": "input_audio", "input_audio": {
            "data": "<base64 audio>", "format": "wav"
        }}
        ]
    }]
    }'
```

## Notes
- Served model name (alias used in requests): `voxtral-small-24b`
  (the full HF id `mistralai/Voxtral-Small-24B-2507` is only used at load time)
- Endpoint port: 8001 (mapped to container 8000)
- Video files: extract the audio track first (ffmpeg -i in.mp4 -vn out.wav);
  the model consumes audio only.
- Register the endpoint in Open WebUI as an OpenAI-compatible connection.