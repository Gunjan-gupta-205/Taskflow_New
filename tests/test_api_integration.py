"""
Integration tests for the TaskFlow API. These hit the *real* running API over
HTTP (not an in-memory test client), because the app depends on a real Postgres
and Redis connection at import time. That means: run `docker-compose up` (or
start the API + Postgres + Redis locally) BEFORE running these tests.

If the API isn't reachable, tests are skipped rather than failing the whole
suite - a CI pipeline without the stack running shouldn't report false failures.

Run with: pytest tests/test_api_integration.py
"""
import pytest
import requests

BASE_URL = "http://127.0.0.1:8001"


def _api_is_reachable():
    try:
        requests.get(BASE_URL, timeout=2)
        return True
    except requests.exceptions.ConnectionError:
        return False


pytestmark = pytest.mark.skipif(
    not _api_is_reachable(),
    reason="TaskFlow API is not running at localhost:8001 - start it with docker-compose up first"
)


def test_health_check():
    response = requests.get(f"{BASE_URL}/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


def test_create_task_returns_task_id():
    payload = {"type": "TEST_TASK", "priority": "NORMAL", "data": {"note": "from test suite"}}
    response = requests.post(f"{BASE_URL}/tasks", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert "task_id" in body
    assert body["status"] == "PENDING"
    assert body["queue"] == "queue:normal"


def test_create_task_routes_to_correct_priority_queue():
    payload = {"type": "TEST_TASK", "priority": "HIGH", "data": {}}
    response = requests.post(f"{BASE_URL}/tasks", json=payload)
    assert response.status_code == 200
    assert response.json()["queue"] == "queue:high"


def test_lookup_created_task_by_id():
    create_response = requests.post(
        f"{BASE_URL}/tasks",
        json={"type": "LOOKUP_TEST", "priority": "LOW", "data": {}}
    )
    task_id = create_response.json()["task_id"]

    lookup_response = requests.get(f"{BASE_URL}/tasks/{task_id}")
    assert lookup_response.status_code == 200
    assert lookup_response.json()["id"] == task_id


def test_lookup_nonexistent_task_returns_error():
    response = requests.get(f"{BASE_URL}/tasks/this-id-does-not-exist")
    assert response.status_code == 200  # endpoint returns 200 with an "error" key
    assert "error" in response.json()


def test_queue_stats_endpoint_returns_all_queue_lengths():
    response = requests.get(f"{BASE_URL}/stats/queues")
    assert response.status_code == 200
    body = response.json()
    for key in ["queue_high", "queue_normal", "queue_low", "queue_dead"]:
        assert key in body
        assert isinstance(body[key], int)


def test_task_stats_endpoint_returns_status_counts():
    response = requests.get(f"{BASE_URL}/stats/tasks")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


def test_dead_tasks_endpoint_returns_list():
    response = requests.get(f"{BASE_URL}/tasks/dead")
    assert response.status_code == 200
    body = response.json()
    assert "dead_tasks" in body
    assert "count" in body
