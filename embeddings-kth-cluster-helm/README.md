# embeddings-kth-cluster-helm

BGE-M3 embedding backend for the SciLifeLab Open LLM service. A dedicated vLLM instance exposing an OpenAI-compatible `/v1/embeddings` API on a single A40 GPU. It serves Open WebUI's RAG (document search) and is available to clients as an OpenAI-compatible embeddings connection.

Runs on the same KTH cluster as Open WebUI and is reached over in-cluster DNS at `http://embeddings.llm-embeddings.svc.cluster.local:8000`.

## Why a separate service

Offloading to vLLM on a GPU is faster and keeps the embedding stack consistent with the chat backend: same `vllm/vllm-openai` image, same operational model.


## Verify

```bash
kubectl -n llm-embeddings port-forward svc/embeddings 8000:8000

# Lists bge-m3
curl -s http://localhost:8000/v1/models \
  -H "Authorization: Bearer $EMBEDDING_API_KEY" | jq .

# Returns 1024 (BGE-M3 vector length)
curl -s http://localhost:8000/v1/embeddings \
  -H "Authorization: Bearer $EMBEDDING_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-m3","input":"hello"}' | jq '.data[0].embedding | length'
```

## Open WebUI integration

Two independent integrations. RAG is the primary one. The connection is optional and only needed to expose embeddings on Open WebUI's public API.

### RAG backend (document search)

Add the block from `integration-openwebui-extraEnv.yaml` to `webui.extraEnv` in the Open WebUI chart values. It sets `RAG_EMBEDDING_ENGINE=openai`, `RAG_EMBEDDING_MODEL=bge-m3`, and the in-cluster base URL. `RAG_EMBEDDING_MODEL` must equal `model.servedName`. Ship this in the same PR as the embedding chart so the service and the setting land together. For a quick check before committing, the same four values can be set under Admin > Settings > Documents.

### Public embeddings API (optional)

To let pilot users call embeddings through `https://openllm.scilifelab.se` with their own Open WebUI API keys (no separate key to distribute), register this service as an OpenAI connection: Admin > Settings > Connections, base URL `http://embeddings.llm-embeddings.svc.cluster.local:8000/v1`, plus the embedding key. Once `bge-m3` appears as an enabled model under Admin > Settings > Models, the unified `POST /api/embeddings` endpoint resolves it:

```bash
curl -s https://openllm.scilifelab.se/api/embeddings \
  -H "Authorization: Bearer <OPEN-WEBUI-API-KEY>" \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-m3","input":"test"}' | jq '.data[0].embedding | length'
```