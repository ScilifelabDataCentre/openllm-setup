import pytest


@pytest.fixture
def global_state():
    return {}


@pytest.fixture
def test_body():
    return {
        "model": "test-model",
        "messages": [
            {"role": "user", "content": "Hello"}
        ],
    }


@pytest.fixture
def test_user():
    return {
        "id": "user-123",
        "email": "user@example.org",
        "name": "Test User",
        "role": "user",
    }


@pytest.fixture
def test_metadata():
    return {
        "interface": "open-webui",
    }
