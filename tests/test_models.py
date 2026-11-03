from datetime import date, datetime, timedelta
import random
from zoneinfo import ZoneInfo

import pytest

from custom_components.autocheck_vpn.models import History, daily_target

TZ = ZoneInfo("Asia/Hong_Kong")


def test_count_once_per_day_persist_reward_and_continuous_streak():
    history = History()
    first = datetime(2026, 11, 4, 3, tzinfo=TZ)
    assert history.confirm(first, "512 MB", True)
    assert not history.confirm(first + timedelta(hours=2), "duplicate", True)
    restored = History(**history.dump())
    assert restored.total == 1 and restored.last_reward == "512 MB"
    assert restored.confirm(first + timedelta(days=1))
    assert restored.total == 2 and restored.streak == 2
    assert restored.last_reward_day == "2026-11-04"
    assert restored.confirm(first + timedelta(days=3))
    assert restored.total == 3 and restored.streak == 1


def test_missed_day_clears_current_streak_and_clock_rollback_cannot_recount():
    history = History()
    first = datetime(2026, 11, 4, 3, tzinfo=TZ)
    history.confirm(first)
    assert history.current_streak(date(2026, 11, 5)) == 1
    assert history.current_streak(date(2026, 11, 6)) == 0
    assert not history.confirm(first - timedelta(days=1))
    assert history.total == 1


@pytest.mark.parametrize("clock,lower,upper", [
    ("03:00:00", "00:00:00", "08:00:00"),
    ("22:00:00", "17:00:00", "23:59:59"),
    ("00:00:00", "00:00:00", "05:00:00"),
    ("12:00:00", "07:00:00", "17:00:00"),
])
def test_five_hour_jitter_is_clipped_to_same_day(clock, lower, upper):
    day = date(2026, 11, 4)
    low = datetime.fromisoformat(f"{day}T{lower}").replace(tzinfo=TZ)
    high = datetime.fromisoformat(f"{day}T{upper}").replace(tzinfo=TZ)
    rng = random.Random(0)
    for _ in range(100):
        target = daily_target(day, clock, 300, str(TZ), rng)
        assert low <= target <= high
        assert target.date() == day


def test_zero_jitter():
    target = daily_target(date(2026, 11, 4), "03:00:00", 0, str(TZ))
    assert target.hour == 3 and target.minute == target.second == target.microsecond == 0


@pytest.mark.parametrize("jitter", [-1, 301])
def test_jitter_limit(jitter):
    with pytest.raises(ValueError):
        daily_target(date(2026, 11, 4), "03:00:00", jitter, str(TZ))
