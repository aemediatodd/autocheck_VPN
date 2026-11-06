from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest
import voluptuous as vol

from custom_components.autocheck_vpn import config_flow as module
from custom_components.autocheck_vpn.config_flow import AutoCheckConfigFlow, AutoCheckOptionsFlow, parse_cookies


def config_flow():
    flow = AutoCheckConfigFlow()
    flow.context = {"source": "user"}
    flow.flow_id = "test-flow"
    flow.handler = "autocheck_vpn"
    return flow


@pytest.mark.asyncio
async def test_new_flow_shows_domain_and_auth_choices():
    result = await config_flow().async_step_user()
    assert result["step_id"] == "login"
    assert result["data_schema"]({"base_url": "https://vpn.example.com", "login_method": "telegram"})["login_method"] == "telegram"


@pytest.mark.asyncio
async def test_telegram_flow_displays_code_and_waits_for_confirmation(monkeypatch):
    class Client:
        def __init__(self, *args):
            pass
        async def telegram_begin(self):
            return {"number": "012345", "token": "test-token", "bot": "example_bot"}
        def cookies(self):
            return {"test-session": "fake-test-value"}
        async def telegram_finish(self, challenge):
            assert challenge["number"] == "012345"
            return True
        async def close(self):
            pass
    monkeypatch.setattr(module, "AutoCheckClient", Client)
    flow = config_flow()
    await flow.async_step_user()
    result = await flow.async_step_login({"base_url": "https://vpn.example.com", "login_method": "telegram"})
    assert result["step_id"] == "telegram"
    assert result["description_placeholders"]["code"] == "012345"
    result = await flow.async_step_telegram({})
    assert result["type"] == "create_entry"
    assert result["data"]["session"] == {"test-session": "fake-test-value"}
    assert "token" not in result["data"]


@pytest.mark.asyncio
async def test_password_opens_browser_handoff_and_saves_returned_session(monkeypatch):
    ticket = {"expires": time.monotonic() + 900, "session": None}
    pending = {"test-token": ticket}
    monkeypatch.setattr(module, "begin", Mock(return_value=("test-token", ticket, "http://127.0.0.1:18765/?test")))
    monkeypatch.setattr(module, "registry", lambda hass: pending)
    flow = config_flow()
    flow.hass = SimpleNamespace()
    await flow.async_step_user()
    result = await flow.async_step_login({"base_url": "https://vpn.example.com", "login_method": "password"})
    assert result["description_placeholders"]["login_url"].startswith("http://127.0.0.1:18765/")
    assert result["data_schema"]({}) == {}
    result = await flow.async_step_password({})
    assert result["errors"]["base"] == "browser_pending"
    ticket["session"] = {"test-session": "fake-value"}
    result = await flow.async_step_password({})
    assert result["type"] == "create_entry"
    assert result["data"]["session"] == {"test-session": "fake-value"}
    assert "password" not in result["data"]
    assert not pending


@pytest.mark.asyncio
async def test_options_bound_jitter_and_validate_timezone():
    entry = SimpleNamespace(data={}, options={})
    flow = AutoCheckOptionsFlow(entry)
    flow.flow_id = "test-flow"
    flow.handler = "autocheck_vpn"
    result = await flow.async_step_settings()
    schema = result["data_schema"]
    values = {"auto_checkin": True, "checkin_time": "03:00:00", "jitter_minutes": 300, "timezone": "Asia/Hong_Kong", "notify_success": True}
    assert schema(values)["jitter_minutes"] == 300
    assert schema(values)["refresh_interval_hours"] == 6
    assert schema(values)["refresh_jitter_hours"] == 1
    for key, value in [("refresh_interval_hours", 0.9), ("refresh_interval_hours", 12.1),
                       ("refresh_jitter_hours", 0.4), ("refresh_jitter_hours", 2.1)]:
        with pytest.raises(vol.Invalid):
            schema({**values, key: value})
    with pytest.raises(vol.Invalid):
        schema({**values, "jitter_minutes": 301})
    result = await flow.async_step_settings({**values, "timezone": "invalid-zone"})
    assert result["errors"]["base"] == "invalid_schedule"


def test_session_import_formats():
    assert parse_cookies('{"session":"fake"}') == {"session": "fake"}
    assert parse_cookies("session=fake; id=test") == {"session": "fake", "id": "test"}
    with pytest.raises(ValueError):
        parse_cookies('{"session":5}')
