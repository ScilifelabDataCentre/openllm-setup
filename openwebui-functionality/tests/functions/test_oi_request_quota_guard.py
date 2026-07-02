import pytest

from functions.oi_request_quota_guard import Filter, RateLimitExceededError


@pytest.fixture
def quota_guard():
    filter_instance = Filter()
    filter_instance.valves.requests_per_minute = 2
    filter_instance.valves.log_allowed_requests = False
    filter_instance.valves.log_denied_requests = False
    return filter_instance


@pytest.mark.asyncio
async def test_allowed_request_updates_global_state(
    quota_guard,
    global_state,
    test_body,
    test_user,
    test_metadata,
):
    result = await quota_guard.inlet(
        test_body,
        __user__=test_user,
        __metadata__=test_metadata,
        __global_state__=global_state,
    )

    assert result == test_body

    state = global_state["scilifelab_request_quota_guard"]
    user_state = state["users"]["user-123"]
    counters = state["counters"]["user-123"]

    assert user_state["limit"] == 2
    assert user_state["used"] == 1
    assert user_state["remaining"] == 1
    assert user_state["last_allowed"] is True
    assert user_state["last_model"] == "test-model"
    assert user_state["last_interface"] == "open-webui"

    assert counters["requests_attempted"] == 1
    assert counters["requests_allowed"] == 1
    assert counters["requests_denied"] == 0


@pytest.mark.asyncio
async def test_request_above_quota_is_denied(
    quota_guard,
    global_state,
    test_body,
    test_user,
):
    quota_guard.valves.requests_per_minute = 1

    await quota_guard.inlet(
        test_body,
        __user__=test_user,
        __global_state__=global_state,
    )

    with pytest.raises(RateLimitExceededError):
        await quota_guard.inlet(
            test_body,
            __user__=test_user,
            __global_state__=global_state,
        )

    state = global_state["scilifelab_request_quota_guard"]
    user_state = state["users"]["user-123"]
    counters = state["counters"]["user-123"]

    assert user_state["used"] == 1
    assert user_state["remaining"] == 0
    assert user_state["last_allowed"] is False

    assert counters["requests_attempted"] == 2
    assert counters["requests_allowed"] == 1
    assert counters["requests_denied"] == 1
