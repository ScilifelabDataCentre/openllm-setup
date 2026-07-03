# Open WebUI Functionality

This part of the repository contains definitions of additional functionality created in the Open WebUI instance. Implementations may contain:

- Open WebUI Functions such as Filters, Actions, and Valves
- Open WebUI Tools
- etc

## Components

### Functions
- Request Quota Guard from oi_request_quota_guard.py. Restricts the number of user requests per minute and also logs user request usage.
- Rate Limit Enforcer from oi_rate_limit_enforcer.py. Restricts the number of user requests per minute.

Activate either the Request Quota Guard or the Rate Limit Enforcer filter, not both.

### Actions
- Rate Limit Feedback from oi_rate_limit_ui_button.py. Adds a UI button that when clicked the user is displayed the current configured user request limit.

### Valves
- oi_valves.py contains configurable settings.

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

## Tests

To run the unit tests:
```bash
cd openwebui-functionality
pytest
```
