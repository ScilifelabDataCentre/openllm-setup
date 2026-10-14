# Search gateway

Every web search from Open WebUI goes through this gateway. Nothing reaches the search engine until the user approves it, and the user always decides inside Open WebUI.


- **Approval inside Open WebUI.** In a chat, a dialog appears in the same chat. From a terminal or an agent, the model shows a link that opens the "Search approvals" assistant in Open WebUI. Both are behind Open WebUI's login, and only the user who made the search can answer.
- **Three answers.** Approve once, approve for this session (the same chat, or for terminals and agents the same user, for 8 hours), or decline.
- **Internal only.** The gateway has no public page or route any more. Only Open WebUI talks to it, with an API key.
- **Search engine.** DuckDuckGo, through the DDGS library.
- **Limits.** Unanswered approvals expire after 15 minutes. Each user can have at most 5 waiting and request at most 30 per hour.

## Components

| Component | Directory | Function |
|---|---|---|
| Gateway | `search-gateway/` (its own pod) | Decides: searches DuckDuckGo only for approved searches |
| Web search tool | `openwebui-functionality/search-approval/web_search_tool.py` | The model's search tool; asks the user in the chat |
| Search approvals assistant | `openwebui-functionality/search-approval/search_approvals_pipe.py` | Where terminal and agent searches get approved |

## Checks

```bash
python3 search-gateway/tests/test_search_approval.py
```

Python 3.10 or newer.
