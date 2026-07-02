"""
title: Rate Limit Enforcer
oi_type: filter
function_id: rate_limit_enforcer
description: Enforce the user request rate limit for OI models (max request per minute).
author: SciLifeLab
version: 0.3
"""

from pydantic import BaseModel, Field
from typing import Optional
import time
import asyncio
from collections import defaultdict, deque

# Shared dictionary to track user requests (accessible by other Functions)
user_request_counts = defaultdict(deque)

class RateLimitExceededError(Exception):
    pass

class Filter:
    class Valves(BaseModel):
        # Reference the global Valve setting (Open WebUI will merge them)
        requests_per_minute: int = Field(
            default=60,
            description="Global max requests per minute per user"
        )

    def __init__(self):
        self.valves = self.Valves()
        self.lock = asyncio.Lock()

    async def inlet(self, body: dict, __user__: dict = None) -> dict:
        if not __user__:
            return body

        user_id = __user__.get("id")
        current_time = time.time()

        async with self.lock:
            # Clean up old requests for this user
            global user_request_counts
            user_request_counts[user_id] = deque(
                [t for t in user_request_counts[user_id] if current_time - t < 60]
            )

            if len(user_request_counts[user_id]) >= self.valves.requests_per_minute:
                raise RateLimitExceededError(f"Rate limit exceeded: {self.valves.requests_per_minute} requests/minute")

            user_request_counts[user_id].append(current_time)
        return body
