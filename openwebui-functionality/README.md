# Open WebUI Functionality

This part of the repository contains definitions of additional functionality created in the Open WebUI instance. Implementations may contain:

- Open WebUI Functions such as Filters, Actions, and Valves
- Open WebUI Tools
- etc

## Components

### Functions
- Request Quota Guard from oi_request_quota_guard.py. Restricts the number of user requests per minute and also logs user request usage.
- Rate Limit Enforcer from oi_rate_limit_enforcer.py. Restricts the number of user requests per minute.
- Search approvals from search-approval/search_approvals_pipe.py. A Pipe, ID `search_approvals`, where users approve web searches that came from terminals and agents. Part of [web search with approval](#web-search-with-approval).

Activate either the Request Quota Guard or the Rate Limit Enforcer filter, not both.

### Actions
- Rate Limit Feedback from oi_rate_limit_ui_button.py. Adds a UI button that when clicked the user is displayed the current configured user request limit.

### Valves
- oi_valves.py contains configurable settings.

### Tools
- Web search with approval from search-approval/web_search_tool.py. Tool ID `web_search_with_approval`: the model's only web search tool. Every search goes through the search gateway and only leaves after the user approves it. See [web search with approval](#web-search-with-approval).

## Installation

Only admin users can install and manage the Functions in Open WebUI.

To install:
1. Open the Admin Panel in Open WebUI as an admin user.
2. Navigate to the Functions tab and click on New Function.
3. Paste in the definition of the function in the window.
4. The Title, Description, and function id will be filled in automatically for you.
5. Click on Save.
6. Click the back arrow to return to the overview of Functions. For the function you are installing, change it to both Enabled and Global.

To modify a setting (valve) for a function, click on the configuration icon and edit the value.

## Web search with approval

The tool and the Search approvals function in [search-approval/](search-approval/) work with the search gateway, which the Open WebUI Helm chart deploys (`searchGateway` in the `openllm-charts` values files). The chart also gives Open WebUI the gateway's address and key, so both files' `GATEWAY_URL` and `API_KEY` Valves stay empty. Switch off Open WebUI's built-in web search when installing them. See the [README](search-approval/README.md).

## Tests

To run the unit tests:
```bash
cd openwebui-functionality
pytest
```

The web search with approval tests in `tests/search_approval/` run the tool and the function against a local stand-in for the gateway's HTTP API.
