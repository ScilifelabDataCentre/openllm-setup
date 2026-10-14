# Search approval (Open WebUI)

Two components to ensure no web search leaves without the user's approval.

| File | Install as | Function |
|---|---|---|
| `web_search_tool.py` | Tool, id `web_search_approval` | The model's search tool. Asks the user in the chat before anything is sent. |
| `search_approvals_pipe.py` | Function, id `search_approvals` | The "Search approvals" assistant, where searches from terminals and agents get approved. |

