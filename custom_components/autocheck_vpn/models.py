"""Persistent accounting and a bounded daily schedule."""
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta
import random
from zoneinfo import ZoneInfo


@dataclass
class History:
    total: int = 0
    streak: int = 0
    last_day: str | None = None
    last_success: str | None = None
    last_reward: str | None = None
    last_reward_day: str | None = None
    last_was_performed: bool = False

    def confirm(self, now: datetime, reward: str | None = None, performed: bool = False) -> bool:
        today = now.date()
        if self.last_day == today.isoformat():
            return False
        previous = date.fromisoformat(self.last_day) if self.last_day else None
        # Clock rollback must not create a second historical day.
        if previous and today < previous:
            return False
        self.streak = self.streak + 1 if previous == today - timedelta(days=1) else 1
        self.total += 1
        self.last_day = today.isoformat()
        self.last_success = now.isoformat()
        self.last_was_performed = performed
        if reward:
            self.last_reward = reward
            self.last_reward_day = today.isoformat()
        return True

    def current_streak(self, today: date) -> int:
        if self.last_day and date.fromisoformat(self.last_day) >= today - timedelta(days=1):
            return self.streak
        return 0

    def dump(self):
        return asdict(self)


def daily_window(day: date, clock: str, jitter: int, timezone: str) -> tuple[datetime, datetime]:
    if not 0 <= jitter <= 300:
        raise ValueError("Jitter must be between 0 and 300 minutes")
    tz = ZoneInfo(timezone)
    center = datetime.combine(day, time.fromisoformat(clock), tzinfo=tz)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz) - timedelta(seconds=1)
    return max(start, center - timedelta(minutes=jitter)), min(end, center + timedelta(minutes=jitter))


def daily_target(day: date, clock: str, jitter: int, timezone: str, rng=None) -> datetime:
    start, end = daily_window(day, clock, jitter, timezone)
    low, high = start.timestamp(), end.timestamp()
    tz = start.tzinfo
    return datetime.fromtimestamp((rng or random.SystemRandom()).uniform(low, high), tz)
