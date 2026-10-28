# Search approval (Open WebUI)

Two pieces that an Open WebUI admin pastes into Open WebUI. Together with the search gateway, they make sure no web search leaves without the user's approval.

| File | Install as | What it does |
|---|---|---|
| `web_search_tool.py` | Tool, id `web_search_with_approval` | The model's search tool. Asks the user in the chat before anything is sent. |
| `search_approvals_pipe.py` | Function, id `search_approvals` | The "Search approvals" assistant, where searches from terminals and agents get approved. |

The gateway itself is in its own repository, [openllm-search-gateway](https://github.com/ScilifelabDataCentre/openllm-search-gateway).

## Checks

The checks run the tool and the assistant together with the gateway's code, so clone `openllm-search-gateway` next to `openllm-setup` first (or set `SEARCH_GATEWAY_DIR` to your clone). Then, from the `openllm-setup` root:

```bash
python3 openwebui-functionality/search-approval/tests/test_tool_and_assistant.py
```

Plain Python 3.10 or newer, nothing to install. It ends with `all checks passed`.
