"""Session state, persisted counters and daily randomized execution."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import random
from zoneinfo import ZoneInfo

from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import AutoCheckClient, AutoCheckError, LoginRequired
from .const import DEFAULT_TIME, DEFAULT_TIMEZONE, DOMAIN, NAME
from .models import History, daily_target, daily_window

_LOGGER = logging.getLogger(__name__)


class AutoCheckCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=NAME, update_interval=None, config_entry=entry)
        self.entry = entry
        self.client = AutoCheckClient(entry.data["base_url"], entry.data.get("session"))
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.history = History()
        self.lock = asyncio.Lock()
        self.next_run: datetime | None = None
        self.random_target: datetime | None = None
        self._cancel_timer = None
        self._timer_task = None
        self.next_refresh: datetime | None = None
        self._cancel_refresh = None
        self._refresh_task = None
        self._attempts = 0
        self._closed = False
        self.data = self.snapshot("unknown")

    def now(self):
        return dt_util.utcnow().astimezone(ZoneInfo(self.entry.options.get("timezone", DEFAULT_TIMEZONE)))

    def snapshot(self, status, error=None):
        now = self.now()
        return {
            "status": status, "error": error,
            "total": self.history.total,
            "streak": self.history.current_streak(now.date()),
            "last_success": self.history.last_success,
            "last_reward": self.history.last_reward,
            "last_reward_day": self.history.last_reward_day,
            "last_was_performed": self.history.last_was_performed,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "random_target": self.random_target.isoformat() if self.random_target else None,
            "next_refresh": self.next_refresh.isoformat() if self.next_refresh else None,
        }

    async def async_initialize(self):
        saved = await self.store.async_load() or {}
        self.history = History(**saved.get("history", {}))
        if saved.get("next_run"):
            self.next_run = datetime.fromisoformat(saved["next_run"])
            self.random_target = datetime.fromisoformat(saved.get("random_target") or saved["next_run"])
        # Preserve today's chosen random time across reloads. Schedule edits redraw it.
        if saved.get("schedule") != self.schedule_key():
            self.next_run = None
            self.random_target = None
        await self.async_refresh()
        self.plan()
        await self.save()

    def schedule_key(self):
        return {k: self.entry.options.get(k, default) for k, default in (
            ("auto_checkin", True), ("checkin_time", DEFAULT_TIME),
            ("jitter_minutes", 30), ("timezone", DEFAULT_TIMEZONE))}

    async def save(self):
        await self.store.async_save({"history": self.history.dump(),
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "random_target": self.random_target.isoformat() if self.random_target else None,
            "schedule": self.schedule_key()})

    async def _save_session(self):
        cookies = self.client.cookies()
        if cookies != self.entry.data.get("session"):
            # Session rotation should not trigger an options reload.
            self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, "session": cookies})

    async def _confirm(self, reward=None, performed=False):
        added = self.history.confirm(self.now(), reward, performed)
        self.plan()
        await self.save()
        if added and self.entry.options.get("notify_success", True):
            persistent_notification.async_create(self.hass,
                f"{self.now().date()} 当天签到成功。" + (f"{reward}。" if reward else "")
                + f"累计 {self.history.total} 次，连续 {self.history.streak} 天。",
                title=NAME, notification_id=f"{DOMAIN}_{self.entry.entry_id}_success")

    async def _operate(self, checkin=False, skip_completed=False):
        async with self.lock:
            try:
                observed_day = self.now().date()
                if checkin and skip_completed and self.history.last_day == observed_day.isoformat():
                    return self.snapshot("checked_in")
                if checkin:
                    page, reward, performed = await self.client.checkin()
                else:
                    page, reward, performed = await self.client.status(), None, False
                if self.now().date() != observed_day:
                    raise AutoCheckError("Date changed during request; refresh again to confirm today's status")
                if page.checked_in:
                    await self._confirm(reward, performed)
                await self._save_session()
                persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_{self.entry.entry_id}_login")
                return self.snapshot("checked_in" if page.checked_in else "ready")
            except LoginRequired:
                if self.data.get("status") != "login_required":
                    persistent_notification.async_create(self.hass,
                        "登录状态已失效，请在集成的配置中重新登录。",
                        title=NAME, notification_id=f"{DOMAIN}_{self.entry.entry_id}_login")
                return self.snapshot("login_required")
            except AutoCheckError as err:
                # Error messages are controlled by our protocol, never raw account HTML.
                return self.snapshot("error", str(err))

    async def _async_update_data(self):
        result = await self._operate()
        self.plan_refresh()
        result["next_refresh"] = self.next_refresh.isoformat() if self.next_refresh else None
        return result

    def plan_refresh(self):
        if self._cancel_refresh:
            self._cancel_refresh()
            self._cancel_refresh = None
        if self._closed:
            return
        hours = self.entry.options.get("refresh_interval_hours", 6)
        jitter = self.entry.options.get("refresh_jitter_hours", 1)
        # A large jitter must never produce a zero or negative interval.
        delay = random.SystemRandom().uniform(max(1 / 60, hours - jitter), hours + jitter)
        self.next_refresh = self.now() + timedelta(hours=delay)
        self._cancel_refresh = async_track_point_in_utc_time(self.hass, self._on_refresh_due, self.next_refresh)

    @callback
    def _on_refresh_due(self, _now):
        self._refresh_task = self.hass.async_create_task(self.async_refresh())

    async def async_checkin(self, *, automatic=False):
        result = await self._operate(checkin=True, skip_completed=automatic)
        self.async_set_updated_data(result)
        return result

    def plan(self):
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        if not self.entry.options.get("auto_checkin", True) or self._closed:
            self.next_run = None
            self.random_target = None
            self.async_set_updated_data(self.snapshot(self.data["status"], self.data.get("error")))
            return
        now = self.now()
        if self.history.last_day == now.date().isoformat() and (
            self.next_run is None or self.next_run.astimezone(now.tzinfo).date() <= now.date()
        ):
            self._attempts = 0
            self.next_run = daily_target(now.date() + timedelta(days=1),
                self.entry.options.get("checkin_time", DEFAULT_TIME),
                self.entry.options.get("jitter_minutes", 30), str(now.tzinfo))
            self.random_target = self.next_run
        if self.next_run is None or self.next_run.astimezone(now.tzinfo).date() < now.date():
            self.next_run = daily_target(now.date(), self.entry.options.get("checkin_time", DEFAULT_TIME),
                self.entry.options.get("jitter_minutes", 30), str(now.tzinfo))
            self.random_target = self.next_run
        if self.next_run <= now:
            # Catch-up is permitted only inside today's configured window.
            start, end = daily_window(now.date(), self.entry.options.get("checkin_time", DEFAULT_TIME),
                self.entry.options.get("jitter_minutes", 30), str(now.tzinfo))
            catchup = max(start, now + timedelta(seconds=5))
            if catchup <= end:
                self.next_run = catchup
            else:
                self.next_run = daily_target(now.date() + timedelta(days=1),
                    self.entry.options.get("checkin_time", DEFAULT_TIME),
                    self.entry.options.get("jitter_minutes", 30), str(now.tzinfo))
                self.random_target = self.next_run
        self._cancel_timer = async_track_point_in_utc_time(self.hass, self._on_due, self.next_run)
        self.async_set_updated_data(self.snapshot(self.data["status"], self.data.get("error")))

    @callback
    def _on_due(self, _now):
        self._timer_task = self.hass.async_create_task(self._run_scheduled())

    async def _run_scheduled(self):
        try:
            result = await self.async_checkin(automatic=True)
            self._attempts += 1
            now = self.now()
            retry = now + timedelta(minutes=15)
            _, window_end = daily_window(now.date(), self.entry.options.get("checkin_time", DEFAULT_TIME),
                self.entry.options.get("jitter_minutes", 30), str(now.tzinfo))
            if result["status"] == "error" and self._attempts < 3 and retry <= window_end:
                self.next_run = retry
            else:
                self._attempts = 0
                if self.next_run is None or self.next_run.date() <= now.date():
                    self.next_run = daily_target(now.date() + timedelta(days=1),
                        self.entry.options.get("checkin_time", DEFAULT_TIME),
                        self.entry.options.get("jitter_minutes", 30), str(now.tzinfo))
                    self.random_target = self.next_run
            self.plan()
            await self.save()
        finally:
            self._timer_task = None

    async def async_close(self):
        self._closed = True
        await self.async_shutdown()
        if self._cancel_refresh:
            self._cancel_refresh()
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        if self._cancel_timer:
            self._cancel_timer()
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
        await self.client.close()
