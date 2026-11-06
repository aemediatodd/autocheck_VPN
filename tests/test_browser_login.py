import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlencode

import pytest

from custom_components.autocheck_vpn import browser_login as module
from custom_components.autocheck_vpn.api import LoginRequired


def setup(monkeypatch):
    hass = SimpleNamespace(data={}, http=SimpleNamespace(register_view=Mock()))
    monkeypatch.setattr(module, "get_url", lambda *a, **kw: "http://ha.local:8123")
    token, ticket, url = module.begin(hass, "https://vpn.example.com")
    return hass, token, ticket, url


def test_random_scoped_ticket_and_expiration(monkeypatch):
    hass, token, ticket, url = setup(monkeypatch)
    assert len(token) == 64 and "127.0.0.1:18765" in url
    second, _, _ = module.begin(hass, "https://vpn.example.com")
    assert token != second
    assert hass.http.register_view.call_count == 1
    ticket["expires"] = time.monotonic() - 1
    assert token not in module.registry(hass)


@pytest.mark.asyncio
async def test_handoff_verifies_session_and_rejects_replay(monkeypatch):
    hass, token, ticket, _ = setup(monkeypatch)
    client = SimpleNamespace(status=AsyncMock(), cookies=lambda: {"sid": "rotated-value"}, close=AsyncMock())
    constructor = Mock(return_value=client)
    monkeypatch.setattr(module, "AutoCheckClient", constructor)
    view = module.BrowserLoginView(hass)
    request = SimpleNamespace(content_length=200, json=AsyncMock(return_value={"token": token, "cookies": {"sid": "fake-value"}}))
    result = await view.post(request)
    assert result.status == 200
    assert ticket["session"] == {"sid": "rotated-value"}
    constructor.assert_called_once_with("https://vpn.example.com", {"sid": "fake-value"})
    client.status.assert_awaited_once()
    assert (await view.post(request)).status == 403


@pytest.mark.asyncio
async def test_unverified_cookie_does_not_complete_flow(monkeypatch):
    hass, token, ticket, _ = setup(monkeypatch)
    client = SimpleNamespace(status=AsyncMock(side_effect=LoginRequired()), close=AsyncMock())
    monkeypatch.setattr(module, "AutoCheckClient", Mock(return_value=client))
    request = SimpleNamespace(content_length=200, json=AsyncMock(return_value={"token": token, "cookies": {"sid": "invalid"}}))
    assert (await module.BrowserLoginView(hass).post(request)).status == 400
    assert ticket["session"] is None and not ticket["busy"]
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,status", [({"token": "wrong", "cookies": {"sid": "fake"}}, 403),
    ({"token": 5}, 400), ([], 400)])
async def test_invalid_ticket_rejected(monkeypatch, payload, status):
    hass, _, _, _ = setup(monkeypatch)
    request = SimpleNamespace(content_length=200, json=AsyncMock(return_value=payload))
    assert (await module.BrowserLoginView(hass).post(request)).status == status


def helper_module():
    path = Path(__file__).resolve().parents[1] / "tools/browser_login.py"
    spec = importlib.util.spec_from_file_location("browser_helper_test", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def test_helper_validates_callback_and_one_time_ticket():
    helper = helper_module()
    values = {"site": "https://vpn.example.com", "callback": "http://ha.local:8123/api/autocheck_vpn/browser_login", "token": "a" * 64}
    assert helper.parameters(urlencode(values))[0] == values["site"]
    for change in [{"site": "http://vpn.example.com"}, {"token": "short"},
                   {"callback": "http://ha.local:8123/other"}, {"callback": "http://user:password@ha.local/api/autocheck_vpn/browser_login"}]:
        with pytest.raises(ValueError):
            helper.parameters(urlencode({**values, **change}))


@pytest.mark.parametrize("origin,expected", [("null", 200), (None, 200),
    ("http://127.0.0.1:18765", 200), ("https://other.invalid", 403)])
def test_confirmation_http_origin_and_replay(monkeypatch, origin, expected):
    import threading
    import urllib.request
    import urllib.error
    import re
    helper = helper_module()
    login = AsyncMock()
    monkeypatch.setattr(helper, "browser_login", login)
    server = helper.ThreadingHTTPServer(("127.0.0.1", 0), helper.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    query = urlencode({"site": "https://vpn.example.com", "callback": "http://ha.local/api/autocheck_vpn/browser_login", "token": "a" * 64})
    base = f"http://127.0.0.1:{server.server_port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        request = urllib.request.Request(base + "/?" + query, headers={"Host": "127.0.0.1:18765"})
        with opener.open(request) as response:
            html = response.read().decode()
        nonce = re.search(r'name="nonce" value="([a-f0-9]+)"', html)[1]
        headers = {"Host": "127.0.0.1:18765", "Content-Type": "application/x-www-form-urlencoded"}
        if origin is not None:
            headers["Origin"] = origin
        def submit(value):
            request = urllib.request.Request(base + "/login", data=urlencode({"query": query, "nonce": value}).encode(), headers=headers)
            try:
                with opener.open(request) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
        assert submit("") == 403
        assert submit(nonce) == expected
        assert submit(nonce) == 403
        assert login.await_count == (1 if expected == 200 else 0)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_helper_posts_session_without_writing_credentials(monkeypatch):
    helper = helper_module()
    opener = Mock()
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"ok":true}'
    opener.open.return_value = response
    monkeypatch.setattr(helper.urllib.request, "build_opener", Mock(return_value=opener))
    helper.handoff("http://ha.local/api/autocheck_vpn/browser_login", "a" * 64, {"sid": "fake"})
    request = opener.open.call_args.args[0]
    assert json.loads(request.data) == {"token": "a" * 64, "cookies": {"sid": "fake"}}


@pytest.mark.asyncio
async def test_browser_helper_remembers_and_returns_without_checkin(monkeypatch):
    import playwright.async_api
    helper = helper_module()
    remember = SimpleNamespace(check=AsyncMock())
    page = SimpleNamespace(goto=AsyncMock(), locator=Mock(return_value=remember), wait_for_url=AsyncMock(), wait_for_selector=AsyncMock())
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), cookies=AsyncMock(return_value=[{"name": "sid", "value": "fake"}]))
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    p = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
    class Playwright:
        async def __aenter__(self):
            return p
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(playwright.async_api, "async_playwright", Playwright)
    handoff = Mock()
    monkeypatch.setattr(helper, "handoff", handoff)
    await helper.browser_login("https://vpn.example.com", "http://ha.local/api/autocheck_vpn/browser_login", "a" * 64, "msedge")
    remember.check.assert_awaited_once()
    handoff.assert_called_once()
    assert page.goto.call_args.args[0].endswith("/auth/login")
    assert page.goto.await_count == 1
    browser.close.assert_awaited_once()
