"""
title: Rate Limit Feedback
oi_type: action
function_id: rate_limit_feedback
description: Show the current rate limit value for OI models (max request per minute).
author: SciLifeLab
version: 0.1
"""

from pydantic import BaseModel, Field
from typing import Optional

class Action:
    class Valves(BaseModel):
        # Reference the global Valve setting (Open WebUI will merge them)
        requests_per_minute: int = Field(
            default=60,
            description="Max requests per minute per user for OI models"
        )
        priority: int = 0

    def __init__(self):
        self.valves = self.Valves()

    async def action(self, body: dict, __user__: dict = None, __event_emitter__=None, __event_call__=None) -> Optional[dict]:
        value = self.valves.requests_per_minute

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "notification",
                    "data": {
                        "type": "info",
                        "content": f"Current rate limit: **{value} requests/minute**",
                    },
                }
            )

            await __event_emitter__(
                {
                    "type": "message",
                    "data": {
                        "content": f"\n\nCurrent rate limit: **{self.valves.requests_per_minute} requests/minute**"
                    },
                }
            )

        return {"content": body.get("content", "")}
