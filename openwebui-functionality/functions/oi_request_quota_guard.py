"""
title: Request Quota Guard
oi_type: filter
function_id: request_quota_guard
description: Enforce per-user request quotas and expose current request usage state.
author: SciLifeLab
version: 0.1
"""

from pydantic import BaseModel, Field
import asyncio
import json
import logging
import time

logger = logging.getLogger("oi.request_quota_guard")
logger.setLevel(logging.INFO)
FALLBACK_GLOBAL_STATE = {}

class RateLimitExceededError(Exception):
    pass


class Filter:
    class Valves(BaseModel):
        requests_per_minute: int = Field(
            default=60,
            description="Maximum requests per minute per user",
        )

        log_allowed_requests: bool = Field(
            default=True,
            description="Emit structured logs for allowed requests",
        )

        log_denied_requests: bool = Field(
            default=True,
            description="Emit structured logs for denied requests",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.lock = asyncio.Lock()

    def log_event(self, event: str, **fields):
        record = {
            "event": event,
            "component": "request_quota_guard",
            "ts": time.time(),
            **fields,
        }
        logger.info(json.dumps(record, default=str, sort_keys=True))

    def get_user_identity(self, __user__: dict) -> dict:
        __user__ = __user__ or {}

        return {
            "user_id": __user__.get("id") or __user__.get("sub") or "unknown",
            "user_email": __user__.get("email"),
            "user_name": __user__.get("name"),
            "user_role": __user__.get("role"),
        }

    def get_model(self, body: dict):
        return body.get("model") or body.get("model_id")

    def get_interface(self, __metadata__: dict):
        __metadata__ = __metadata__ or {}

        interface = __metadata__.get("interface")
        if interface:
            return interface

        # Fallback: requests with a chat context are from the WebUI
        if __metadata__.get("chat_id"):
            return "open-webui"

        # Unknown (likely direct API, but not certain)
        return "unknown"

    def get_state_root(self, __global_state__: dict) -> dict:
        """
        Create and return this filter's namespace inside __global_state__.
        """
        namespace = "scilifelab_request_quota_guard"

        if namespace not in __global_state__:
            __global_state__[namespace] = {
                "version": 1,
                "users": {},
                "counters": {},
            }

        return __global_state__[namespace]

    async def inlet(
        self,
        body: dict,
        __user__: dict = None,
        __metadata__: dict = None,
        __global_state__: dict = None,
    ) -> dict:
        if not __user__:
            return body

        if __global_state__ is None:
            # Fallback safety. The filter can still run, but state will not be shared.
            __global_state__ = FALLBACK_GLOBAL_STATE

        now = time.time()
        window_seconds = 60
        limit = self.valves.requests_per_minute

        identity = self.get_user_identity(__user__)
        user_id = identity["user_id"]
        model = self.get_model(body)
        interface = self.get_interface(__metadata__)

        log_payload = None
        should_raise = False

        async with self.lock:
            state = self.get_state_root(__global_state__)

            user_state = state["users"].setdefault(
                user_id,
                {
                    "timestamps": [],
                    "limit": limit,
                    "used": 0,
                    "remaining": limit,
                    "reset_at": None,
                    "last_checked_at": None,
                    "last_allowed": None,
                    "last_model": None,
                    "last_interface": None,
                },
            )

            counters = state["counters"].setdefault(
                user_id,
                {
                    "requests_attempted": 0,
                    "requests_allowed": 0,
                    "requests_denied": 0,
                },
            )

            counters["requests_attempted"] += 1

            timestamps = [
                timestamp
                for timestamp in user_state.get("timestamps", [])
                if now - timestamp < window_seconds
            ]

            used_before_request = len(timestamps)

            if used_before_request >= limit:
                counters["requests_denied"] += 1

                reset_at = (
                    min(timestamps) + window_seconds
                    if timestamps
                    else now + window_seconds
                )

                user_state.update(
                    {
                        **identity,
                        "timestamps": timestamps,
                        "limit": limit,
                        "window_seconds": window_seconds,
                        "used": used_before_request,
                        "remaining": 0,
                        "reset_at": reset_at,
                        "last_checked_at": now,
                        "last_allowed": False,
                        "last_model": model,
                        "last_interface": interface,
                    }
                )

                log_payload = {
                    "event": "rate_limit_denied",
                    **identity,
                    "model": model,
                    "interface": interface,
                    "limit": limit,
                    "used": used_before_request,
                    "remaining": 0,
                    "reset_at": reset_at,
                    "window_seconds": window_seconds,
                    "counters": dict(counters),
                }

                should_raise = True

            else:
                timestamps.append(now)

                used_after_request = len(timestamps)
                remaining_after_request = max(limit - used_after_request, 0)

                counters["requests_allowed"] += 1

                reset_at = (
                    min(timestamps) + window_seconds
                    if timestamps
                    else now + window_seconds
                )

                user_state.update(
                    {
                        **identity,
                        "timestamps": timestamps,
                        "limit": limit,
                        "window_seconds": window_seconds,
                        "used": used_after_request,
                        "remaining": remaining_after_request,
                        "reset_at": reset_at,
                        "last_checked_at": now,
                        "last_allowed": True,
                        "last_model": model,
                        "last_interface": interface,
                    }
                )

                log_payload = {
                    "event": "rate_limit_allowed",
                    **identity,
                    "model": model,
                    "interface": interface,
                    "limit": limit,
                    "used": used_after_request,
                    "remaining": remaining_after_request,
                    "reset_at": reset_at,
                    "window_seconds": window_seconds,
                    "counters": dict(counters),
                }

                should_raise = False

        # Logging happens outside the lock.
        if log_payload:
            event = log_payload.pop("event")

            if event == "rate_limit_allowed" and self.valves.log_allowed_requests:
                self.log_event(event, **log_payload)

            if event == "rate_limit_denied" and self.valves.log_denied_requests:
                self.log_event(event, **log_payload)

        if should_raise:
            raise RateLimitExceededError(
                f"Rate limit exceeded: {limit} requests/minute"
            )

        return body
