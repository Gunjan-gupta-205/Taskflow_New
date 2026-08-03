"""
Unit tests for worker/main.py's pure logic - the backoff calculation and the
weighted queue schedule. These don't need Postgres, Redis, or the API running;
they just import the module and check the math/logic directly.

Run with: pytest tests/test_backoff.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker.main import compute_backoff_delay, QUEUE_SCHEDULE, QUEUE_WEIGHTS


def test_backoff_doubles_each_attempt():
    """Backoff delay should double with each retry attempt (exponential growth)."""
    delay_1 = compute_backoff_delay(1)
    delay_2 = compute_backoff_delay(2)
    delay_3 = compute_backoff_delay(3)

    assert delay_1 == 2
    assert delay_2 == 4
    assert delay_3 == 8
    assert delay_2 == delay_1 * 2
    assert delay_3 == delay_2 * 2


def test_backoff_never_negative_or_zero():
    for attempt in range(1, 6):
        assert compute_backoff_delay(attempt) > 0


def test_queue_schedule_respects_weights():
    """
    The schedule list should contain each queue exactly as many times as its
    configured weight - this is what makes the round-robin "weighted".
    """
    high_count = QUEUE_SCHEDULE.count("queue:high")
    normal_count = QUEUE_SCHEDULE.count("queue:normal")
    low_count = QUEUE_SCHEDULE.count("queue:low")

    assert high_count == QUEUE_WEIGHTS["high"]
    assert normal_count == QUEUE_WEIGHTS["normal"]
    assert low_count == QUEUE_WEIGHTS["low"]


def test_queue_schedule_never_starves_low_priority():
    """
    Every queue must appear at least once in the schedule - if 'low' had a
    weight of 0, low-priority tasks would starve completely (never get picked).
    """
    assert QUEUE_SCHEDULE.count("queue:low") >= 1
    assert QUEUE_SCHEDULE.count("queue:normal") >= 1
    assert QUEUE_SCHEDULE.count("queue:high") >= 1


def test_high_priority_scheduled_more_often_than_low():
    """High priority should still win more turns on average than low priority."""
    assert QUEUE_SCHEDULE.count("queue:high") > QUEUE_SCHEDULE.count("queue:low")
