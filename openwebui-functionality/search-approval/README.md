# Web search with approval

Web search for OpenLLM (Open WebUI) in which no search leaves the cluster unapproved. Every search waits at the search gateway until the user who asked for it approves it inside Open WebUI: in the same chat, or through a link for searches from terminals and agents.

## Components

| Component | Source | Put in place by |
|---|---|---|
| Search gateway | [openllm-search-gateway](https://github.com/ScilifelabDataCentre/openllm-search-gateway), image `ghcr.io/scilifelabdatacentre/openllm-search-gateway` | ArgoCD, with the Open WebUI Helm chart in `openllm-charts` (`searchGateway` in the values files) |
| Web search with approval (Tool, ID `web_search_with_approval`) | [`web_search_tool.py`](web_search_tool.py) | An Open WebUI admin |
| Search approvals (Function, ID `search_approvals`) | [`search_approvals_pipe.py`](search_approvals_pipe.py) | An Open WebUI admin |

## How it works

- The tool is the model's only way to search; Open WebUI's built-in web search stays off.
- Each search goes to the gateway first. A new search returns *approval needed*, and nothing is sent.
- In a chat, the tool shows a box with **Approve once**, **Approve for this session** (8 hours) and **Decline**. Without a browser, the answer carries a link that opens the Search approvals assistant with the same choice.
- Only the user who made a search can answer it: identities come from Open WebUI's login, and the gateway checks them.
- Approved searches go to one configured search engine. The gateway is internal: only the Open WebUI pods may call it, with a shared key.

## Setup

1. **Deploy the gateway** with the Open WebUI chart: create the `search-gateway` Secret (entry `api-key`) in the Open WebUI namespace, and merge the values. The chart also gives Open WebUI `SEARCH_GATEWAY_URL`, `SEARCH_GATEWAY_API_KEY` and `WEBSOCKET_EVENT_CALLER_TIMEOUT`.
2. **Configure Open WebUI** as an admin: switch off the built-in web search, paste in the tool and the function with the IDs above, leave their `GATEWAY_URL` and `API_KEY` Valves empty, and give models the tool with Function Calling set to Native.

## Configuration

| Where | Settings |
|---|---|
| Chart, `searchGateway.settings` | Open WebUI address for approval links, search engine, session length, approval expiry, per-user limits |
| Chart, `searchGateway.openwebui` | Whether Open WebUI gets the gateway's address and key, and the browser question time limit |
| Tool Valves | `RESULT_COUNT` (5), `ASK_TIMEOUT_SECONDS` (300) |
| Function Valves | `ASK_TIMEOUT_SECONDS` (300) |

## Tests

```bash
cd openwebui-functionality
uv run --extra test pytest tests/search_approval
```

The tests run the tool and the function against a local stand-in for the gateway's HTTP API.
