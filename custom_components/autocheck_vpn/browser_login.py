"""One-time browser session handoff to a pending configuration flow."""
from __future__ import annotations

import json
import secrets
import time
from urllib.parse import urlencode

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.network import get_url, NoURLAvailableError

from .api import AutoCheckClient, AutoCheckError
from .const import DOMAIN

KEY = DOMAIN + "_browser_login"
CALLBACK_PATH = "/api/autocheck_vpn/browser_login"
TTL = 900


def registry(hass):
    if KEY not in hass.data:
        hass.data[KEY] = {}
        hass.http.register_view(BrowserLoginView(hass))
    pending = hass.data[KEY]
    for token, ticket in list(pending.items()):
        if ticket["expires"] <= time.monotonic():
            pending.pop(token, None)
    return pending


def begin(hass, site):
    pending = registry(hass)
    token = secrets.token_hex(32)
    # Prefer the URL currently being used by this configuration UI.
    try:
        origin = get_url(hass, require_current_request=True)
    except NoURLAvailableError:
        origin = get_url(hass)
    callback = origin.rstrip("/") + CALLBACK_PATH
    ticket = {"site": site, "expires": time.monotonic() + TTL, "session": None, "busy": False}
    pending[token] = ticket
    url = "http://127.0.0.1:18765/?" + urlencode({"site": site, "callback": callback, "token": token})
    return token, ticket, url


class BrowserLoginView(HomeAssistantView):
    url = CALLBACK_PATH
    name = "api:autocheck_vpn:browser_login"
    requires_auth = False  # Authenticated by an expiring, random, one-use flow ticket.

    def __init__(self, hass):
        self.hass = hass

    async def post(self, request):
        if request.content_length and request.content_length > 32768:
            return web.json_response({"error": "payload_too_large"}, status=413)
        try:
            data = await request.json()
        except (ValueError, UnicodeError):
            return web.json_response({"error": "invalid_json"}, status=400)
        if not isinstance(data, dict) or not isinstance(data.get("token"), str):
            return web.json_response({"error": "invalid_request"}, status=400)
        pending = registry(self.hass)
        ticket = pending.get(data["token"])
        if not ticket or ticket["session"] is not None or ticket["busy"]:
            return web.json_response({"error": "expired_or_used_ticket"}, status=403)
        cookies = data.get("cookies")
        if (not isinstance(cookies, dict) or not cookies
                or len(json.dumps(cookies)) > 24000
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in cookies.items())):
            return web.json_response({"error": "invalid_session"}, status=400)
        ticket["busy"] = True
        client = AutoCheckClient(ticket["site"], cookies)
        try:
            await client.status()
            if ticket["expires"] <= time.monotonic():
                return web.json_response({"error": "expired_ticket"}, status=403)
            ticket["session"] = client.cookies()
            return web.json_response({"ok": True})
        except AutoCheckError:
            return web.json_response({"error": "session_not_verified"}, status=400)
        finally:
            ticket["busy"] = False
            await client.close()
