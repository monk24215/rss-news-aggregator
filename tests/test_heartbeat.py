"""Scheduler logic tests.

We test the pure cron-second computation without needing Redis. The heartbeat task's DB
write is exercised by the real-services CI step, not here.
"""

from app.scheduler import _cron_second_set


def test_cron_seconds_divides_60():
    assert _cron_second_set(30) == {0, 30}
    assert _cron_second_set(15) == {0, 15, 30, 45}
    assert _cron_second_set(60) == {0}


def test_cron_seconds_clamped():
    # Interval below 1 clamps to 1 (fires every second).
    assert _cron_second_set(0) == set(range(0, 60))
    # Interval above 60 clamps to 60.
    assert _cron_second_set(120) == {0}
