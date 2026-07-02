import time
import pytest

from request_quota_guard import Filter


@pytest.mark.asyncio
async def test_old_timestamps_are_cleaned():
    filter_instance = Filter()
    filter_instance.valves.requests_per_minute = 1
    filter_instance.valves.log_allowed_requests = False
    filter_instance.valves.log_denied_requests = False

    old_timestamp = time.time() - 120

    global_state = {
        "scilifelab_request_quota_guard": {
            "version": 1,
            "users": {
                "user-123": {
                    "timestamps": [old_timestamp],
                    "limit": 1,
                    "used": 1,
                    "remaining": 0,
                    "reset_at": None,
                    "last_checked_at": None,
                    "last_allowed": None,
                    "last_model": None,
                    "last_interface": None,
                }
            },
            "counters": {
                "user-123": {
                    "requests_attempted": 0,
                    "requests_allowed": 0,
                    "requests_denied": 0,
                }
            },
        }
    }

    body = {"model": "test-model"}
    user = {"id": "user-123"}

    await filter_instance.inlet(
        body,
        __user__=user,
        __global_state__=global_state,
    )

    user_state = global_state["scilifelab_request_quota_guard"]["users"]["user-123"]

    assert user_state["used"] == 1
    assert len(user_state["timestamps"]) == 1
    assert user_state["timestamps"][0] > old_timestamp
    assert user_state["last_allowed"] is True
