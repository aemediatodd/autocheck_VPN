import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from homeassistant.core import HomeAssistant
from custom_components.autocheck_vpn import coordinator as module
from custom_components.autocheck_vpn.api import AutoCheckError, LoginRequired, PageStatus
from custom_components.autocheck_vpn.coordinator import AutoCheckCoordinator

NOW = datetime(2026, 11, 4, 3, tzinfo=ZoneInfo("Asia/Hong_Kong"))


@pytest.fixture
def notifications(monkeypatch):
    create = Mock()
    monkeypatch.setattr(module.persistent_notification, "async_create", create)
    monkeypatch.setattr(module.persistent_notification, "async_dismiss", Mock())
    return create


def make_coordinator(tmp_path, monkeypatch, options=None):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(entry_id="test-entry", data={"base_url": "https://vpn.example.com", "session": {}},
                            options=options if options is not None else {"checkin_time": "03:00:00"}, async_on_unload=Mock())
    coordinator = AutoCheckCoordinator(hass, entry)
    coordinator.now = lambda: NOW
    coordinator.store = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock(return_value=None))
    timer = Mock(return_value=Mock())
    monkeypatch.setattr(module, "async_track_point_in_utc_time", timer)
    return coordinator, timer


@pytest.mark.asyncio
async def test_expired_session_exposes_login_required_and_preserves_counters(tmp_path, monkeypatch, notifications):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch)
    coordinator.history.confirm(NOW - timedelta(days=1))
    coordinator.client.status = AsyncMock(side_effect=LoginRequired("expired"))
    try:
        result = await coordinator._operate()
        coordinator.async_set_updated_data(result)
        assert result["status"] == "login_required" and result["total"] == 1
        await coordinator._operate()
        assert notifications.call_count == 1
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
async def test_manual_and_automatic_calls_are_serialized_and_count_once(tmp_path, monkeypatch, notifications):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch)
    active = 0
    maximum = 0
    async def checkin():
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return PageStatus(True, "明日再来"), "512 MB", True
    coordinator.client.checkin = checkin
    try:
        await asyncio.gather(coordinator.async_checkin(), coordinator.async_checkin())
        assert maximum == 1
        assert coordinator.history.total == 1
        assert coordinator.data["status"] == "checked_in"
        assert notifications.call_count == 1
        assert coordinator.store.async_save.await_count == 2
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
async def test_restart_preserves_today_random_target_and_history(tmp_path, monkeypatch, notifications):
    coordinator, timer = make_coordinator(tmp_path, monkeypatch)
    coordinator.history.confirm(NOW - timedelta(days=1))
    target = NOW + timedelta(minutes=20)
    coordinator.store.async_load.return_value = {
        "history": coordinator.history.dump(), "next_run": target.isoformat(), "schedule": coordinator.schedule_key()}
    coordinator.client.status = AsyncMock(return_value=PageStatus(False, "签到"))
    try:
        await coordinator.async_initialize()
        assert coordinator.next_run == target
        assert coordinator.history.total == 1
        assert any(call.args[-1] == target for call in timer.call_args_list)
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
async def test_missed_daily_job_catches_up_on_startup(tmp_path, monkeypatch, notifications):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch)
    coordinator.next_run = NOW - timedelta(minutes=20)
    try:
        coordinator.plan()
        assert coordinator.next_run == NOW + timedelta(seconds=5)
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
async def test_disabling_automatic_checkin_cancels_timer(tmp_path, monkeypatch, notifications):
    coordinator, timer = make_coordinator(tmp_path, monkeypatch, {"auto_checkin": False})
    cancel = Mock()
    coordinator._cancel_timer = cancel
    try:
        coordinator.plan()
        cancel.assert_called_once()
        assert coordinator.next_run is None
        timer.assert_not_called()
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
async def test_network_failure_retries_at_most_three_times(tmp_path, monkeypatch, notifications):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch)
    coordinator.client.checkin = AsyncMock(side_effect=AutoCheckError("Network request failed"))
    try:
        await coordinator._run_scheduled()
        assert coordinator.next_run == NOW + timedelta(minutes=15)
        await coordinator._run_scheduled()
        assert coordinator.next_run == NOW + timedelta(minutes=15)
        await coordinator._run_scheduled()
        assert coordinator.next_run.date() == NOW.date() + timedelta(days=1)
        assert coordinator.history.total == 0
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
async def test_startup_after_eight_does_not_catch_up_outside_zero_to_eight_window(tmp_path, monkeypatch, notifications):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch, {"checkin_time": "03:00:00", "jitter_minutes": 300})
    coordinator.now = lambda: NOW.replace(hour=9)
    coordinator.next_run = NOW.replace(hour=7)
    try:
        coordinator.plan()
        assert coordinator.next_run.date() == NOW.date() + timedelta(days=1)
        assert 0 <= coordinator.next_run.hour <= 8
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
async def test_retry_cannot_exceed_eight_oclock_upper_bound(tmp_path, monkeypatch, notifications):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch, {"checkin_time": "03:00:00", "jitter_minutes": 300})
    coordinator.now = lambda: NOW.replace(hour=7, minute=55)
    coordinator.client.checkin = AsyncMock(side_effect=AutoCheckError("network"))
    try:
        await coordinator._run_scheduled()
        assert coordinator.next_run.date() == NOW.date() + timedelta(days=1)
        assert 0 <= coordinator.next_run.hour <= 8
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
async def test_fresh_success_cancels_today_and_keeps_tomorrow_target(tmp_path, monkeypatch, notifications):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch)
    coordinator.next_run = NOW + timedelta(minutes=20)
    cancel = Mock()
    coordinator._cancel_timer = cancel
    coordinator.client.status = AsyncMock(return_value=PageStatus(True, "明日再来"))
    try:
        await coordinator.async_refresh()
        cancel.assert_called_once()
        tomorrow = coordinator.next_run
        assert tomorrow.date() == NOW.date() + timedelta(days=1)
        assert coordinator.data["status"] == "checked_in"
        await coordinator.async_refresh()
        assert coordinator.next_run == tomorrow
        coordinator.client.checkin = AsyncMock()
        await coordinator.async_checkin(automatic=True)
        coordinator.client.checkin.assert_not_awaited()
        assert coordinator.store.async_save.call_args.args[0]["next_run"] == tomorrow.isoformat()
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
async def test_yesterday_success_does_not_cancel_today_and_next_day_runs(tmp_path, monkeypatch, notifications):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch)
    coordinator.history.confirm(NOW - timedelta(days=1))
    target = NOW + timedelta(minutes=20)
    coordinator.next_run = target
    try:
        coordinator.plan()
        assert coordinator.next_run == target
        coordinator.client.checkin = AsyncMock(return_value=(PageStatus(True, "明日再来"), "512 MB", True))
        await coordinator.async_checkin()
        coordinator.now = lambda: NOW + timedelta(days=1)
        await coordinator.async_checkin()
        assert coordinator.client.checkin.await_count == 2
        assert coordinator.history.total == 3
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
async def test_response_crossing_midnight_cannot_cancel_new_day(tmp_path, monkeypatch, notifications):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch)
    async def status():
        coordinator.now = lambda: NOW + timedelta(days=1)
        return PageStatus(True, "明日再来")
    coordinator.client.status = status
    try:
        result = await coordinator._operate()
        assert result["status"] == "error"
        assert coordinator.history.total == 0
    finally:
        await coordinator.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("options,low,high", [({}, 5, 7),
    ({"refresh_interval_hours": 12, "refresh_jitter_hours": 0.5}, 11.5, 12.5),
    ({"refresh_interval_hours": 1, "refresh_jitter_hours": 2}, 1 / 60, 3)])
async def test_refresh_ranges_and_replaces_previous_timer(tmp_path, monkeypatch, notifications, options, low, high):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch, options)
    try:
        for _ in range(20):
            coordinator.plan_refresh()
            hours = (coordinator.next_refresh - NOW).total_seconds() / 3600
            assert low <= hours <= high
        assert coordinator.update_interval is None
        cancel = coordinator._cancel_refresh
        previous_count = cancel.call_count
        await coordinator.async_close()
        assert cancel.call_count == previous_count + 1
    finally:
        await coordinator.client.close()


@pytest.mark.asyncio
async def test_failed_refresh_still_schedules_next_query(tmp_path, monkeypatch, notifications):
    coordinator, _ = make_coordinator(tmp_path, monkeypatch)
    coordinator.client.status = AsyncMock(side_effect=AutoCheckError("network"))
    try:
        await coordinator.async_refresh()
        assert coordinator.data["status"] == "error"
        assert coordinator.data["next_refresh"] == coordinator.next_refresh.isoformat()
        assert coordinator.next_refresh > NOW
    finally:
        await coordinator.async_close()
