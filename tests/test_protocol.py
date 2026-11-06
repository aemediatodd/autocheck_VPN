import base64
import json
from unittest.mock import AsyncMock

import pytest

from custom_components.autocheck_vpn.api import (
    AutoCheckClient, AutoCheckError, LoginRequired, VerificationRequired,
    decode_page, normalize_url, parse_status, parse_telegram,
)

SUCCESS = '<div id="checkin-div" class="breadcrumb-item active"><a href="#" class="btn btn-icon disabled icon-left btn-primary"><i class="far fa-calendar-check"></i> 明日再来</a></div>'
READY = '<div id="checkin-div"><a href="#" onclick="checkin()"><i></i> 每日签到</a></div>'
LOGIN = '<input id="email"><input id="password">'


def test_real_success_marker_and_ready():
    assert parse_status(SUCCESS).checked_in
    assert not parse_status(READY).checked_in


def test_decode_current_site_envelope():
    wrapped = 'var originBody = "' + base64.b64encode(SUCCESS.encode()).decode() + '";'
    assert decode_page(wrapped) == SUCCESS
    assert parse_status(wrapped).checked_in


@pytest.mark.parametrize("html", [LOGIN, 'var originBody = "' + base64.b64encode(LOGIN.encode()).decode() + '";'])
def test_session_expired(html):
    with pytest.raises(LoginRequired):
        parse_status(html)


@pytest.mark.parametrize("html", [
    '<script>明日再来</script><div id="checkin-div"><a onclick="checkin()">签到</a></div>',
    '<div>明日再来</div>' + READY,
])
def test_unrelated_text_cannot_confirm_success(html):
    assert not parse_status(html).checked_in


@pytest.mark.parametrize("html", ["maintenance", '<div id="checkin-div"><a>明日再来</a></div>', '<div id="checkin-div">changed</div>'])
def test_fail_closed_on_changed_markup(html):
    with pytest.raises(AutoCheckError):
        parse_status(html)


def test_telegram_challenge_from_current_markup():
    html = '<a href="https://t.me/example_bot">bot</a><code id="code_number" style="color: red">012345</code><script>data: { token: "test-token", number: "012345" }</script>'
    assert parse_telegram(html) == {"number": "012345", "token": "test-token", "bot": "example_bot"}


@pytest.mark.parametrize("url", ["http://vpn.example.com", "https://a:b@vpn.example.com", "https://vpn.example.com/user", "https://vpn.example.com/#x"])
def test_reject_unsafe_or_invalid_origin(url):
    with pytest.raises(ValueError):
        normalize_url(url)


def test_normalize_domain():
    assert normalize_url(" vpn.example.com/ ") == "https://vpn.example.com"


@pytest.mark.asyncio
async def test_checkin_confirms_fresh_server_page_and_duplicate_is_skipped():
    client = AutoCheckClient("https://vpn.example.com")
    client.request = AsyncMock(side_effect=[READY, json.dumps({"ret": 1, "msg": "你获得了 512 MB流量"}), SUCCESS, SUCCESS])
    try:
        state, reward, performed = await client.checkin()
        assert state.checked_in and performed and "512 MB" in reward
        again, reward, performed = await client.checkin()
        assert again.checked_in and reward is None and not performed
        assert [c.args[0] for c in client.request.await_args_list] == ["/user", "/user/checkin", "/user", "/user"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_http_200_json_is_not_success_without_fresh_marker():
    client = AutoCheckClient("https://vpn.example.com")
    client.request = AsyncMock(side_effect=[READY, '{"ret":1,"msg":"ok"}', READY])
    try:
        with pytest.raises(AutoCheckError, match="Fresh page"):
            await client.checkin()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_login_expiration_during_post():
    client = AutoCheckClient("https://vpn.example.com")
    client.request = AsyncMock(side_effect=[READY, LOGIN])
    try:
        with pytest.raises(LoginRequired):
            await client.checkin()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_password_request_remembers_user_by_default():
    client = AutoCheckClient("https://vpn.example.com")
    client.request = AsyncMock(side_effect=[LOGIN, '{"phase":"authenticated"}', SUCCESS])
    try:
        await client.password_login("test@example.invalid", "fake-test-password")
        payload = client.request.await_args_list[1].args[1]
        assert payload["remember_me"] == "on"
        assert payload["phase"] == "password"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_geetest_requires_interactive_verification():
    client = AutoCheckClient("https://vpn.example.com")
    client.request = AsyncMock(return_value="var geetestV4Handler = null;")
    try:
        with pytest.raises(VerificationRequired):
            await client.password_login("test@example.invalid", "fake-test-password")
        assert client.request.await_count == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_telegram_pending_then_authenticated():
    client = AutoCheckClient("https://vpn.example.com")
    client.request = AsyncMock(side_effect=['{"ret":0}', '{"ret":2}', '{"ret":1}', SUCCESS])
    challenge = {"number": "012345", "token": "test-token", "bot": "example_bot"}
    try:
        assert not await client.telegram_finish(challenge)
        assert await client.telegram_finish(challenge)
        calls = client.request.await_args_list
        assert calls[2].args == ("/auth/qrcode_login", {
            "number": "012345", "token": "test-token", "remember_me": "on"})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_telegram_expired():
    client = AutoCheckClient("https://vpn.example.com")
    client.request = AsyncMock(return_value='{"ret":-1}')
    try:
        with pytest.raises(VerificationRequired):
            await client.telegram_finish({"number": "012345", "token": "test-token"})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cross_domain_redirect_does_not_forward_credentials():
    class Response:
        status = 307
        headers = {"Location": "https://different.invalid/auth/login"}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    client = AutoCheckClient("https://vpn.example.com")
    original = client.session.request
    calls = []
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()
    client.session.request = request
    try:
        with pytest.raises(AutoCheckError, match="Domain changed"):
            await client.request("/auth/login", {"passwd": "fake-test-password"})
        assert len(calls) == 1
    finally:
        client.session.request = original
        await client.close()
