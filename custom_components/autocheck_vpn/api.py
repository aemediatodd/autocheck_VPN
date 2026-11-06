"""Website protocol, independent of Home Assistant."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import json
import re
import time
from urllib.parse import urlsplit

import aiohttp
from yarl import URL


class AutoCheckError(Exception):
    """Website could not be verified."""


class LoginRequired(AutoCheckError):
    """The session has expired."""


class VerificationRequired(AutoCheckError):
    """Interactive password verification is required."""


def normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("Use an HTTPS origin, for example https://vpn.example.com")
    return value


def decode_page(html: str) -> str:
    """Current site wraps its DOM in a base64 document.write envelope."""
    match = re.search(r"\b(?:var|let|const)\s+originBody\s*=\s*([\"'])([A-Za-z0-9+/=\s]+)\1", html)
    if match:
        try:
            return base64.b64decode(match[2]).decode("utf-8")
        except (ValueError, UnicodeError) as err:
            raise AutoCheckError("Invalid page envelope") from err
    return html


class CheckinParser(HTMLParser):
    """Inspect only the check-in container, never scripts or other page text."""
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.found = False
        self.disabled = False
        self.action = False
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "checkin-div":
            self.found = True
            self.depth = 1
            return
        if self.depth:
            if tag not in {"br", "hr", "img", "input", "meta", "link", "wbr"}:
                self.depth += 1
            if tag in {"a", "button"}:
                self.disabled |= "disabled" in attrs.get("class", "").split() or "disabled" in attrs
                self.action |= "checkin" in attrs.get("onclick", "").lower()

    def handle_endtag(self, tag):
        if self.depth and tag not in {"br", "hr", "img", "input", "meta", "link", "wbr"}:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text.append(data)


@dataclass(frozen=True)
class PageStatus:
    checked_in: bool
    marker: str


def parse_status(html: str) -> PageStatus:
    html = decode_page(html)
    parser = CheckinParser()
    parser.feed(html)
    if not parser.found:
        if re.search(r'id\s*=\s*[\"\'](?:email|password|telegram-login-button)[\"\']', html):
            raise LoginRequired("Login page returned")
        raise AutoCheckError("Check-in container missing; website may have changed")
    text = " ".join("".join(parser.text).split())
    success = parser.disabled and any(word in text for word in (
        "明日再来", "已签到", "今日已签", "已经签到", "Already", "already", "Come back tomorrow"))
    if not success and not parser.action:
        raise AutoCheckError("Unrecognized check-in markup")
    return PageStatus(success, text)


def parse_telegram(html: str) -> dict[str, str]:
    html = decode_page(html)
    code = re.search(r'id=[\"\']code_number[\"\'][^>]*>\s*(\d{6})\s*<', html)
    token = re.search(r'\btoken\s*:\s*[\"\']([^\"\']+)[\"\']', html)
    bot = re.search(r'https://t\.me/([A-Za-z0-9_]+)', html)
    if not code or not token or not bot:
        raise AutoCheckError("Telegram challenge missing; website may have changed")
    return {"number": code[1], "token": token[1], "bot": bot[1]}


class AutoCheckClient:
    def __init__(self, base_url: str, cookies: dict[str, str] | None = None):
        self.base_url = normalize_url(base_url)
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver()),
            cookie_jar=aiohttp.CookieJar(),
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "Mozilla/5.0 autocheck_VPN/0.1.0", "Referer": self.base_url + "/user"},
        )
        if cookies:
            self.session.cookie_jar.update_cookies(cookies, response_url=URL(self.base_url))

    async def close(self):
        await self.session.close()

    def cookies(self) -> dict[str, str]:
        return {k: v.value for k, v in self.session.cookie_jar.filter_cookies(URL(self.base_url)).items()}

    async def request(self, path: str, data=None) -> str:
        url = self.base_url + path
        method = "POST" if data is not None else "GET"
        try:
            # Do not send account data to a changed domain through a redirect.
            for _ in range(5):
                async with self.session.request(method, url, data=data, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        target = URL(url).join(URL(response.headers.get("Location", "")))
                        if target.origin() != URL(self.base_url).origin():
                            raise AutoCheckError("Domain changed; update domain in integration settings")
                        url = str(target)
                        if response.status in (301, 302, 303):
                            method, data = "GET", None
                        continue
                    if response.status in (401, 403):
                        raise LoginRequired("Session rejected")
                    if response.status != 200:
                        raise AutoCheckError(f"Website HTTP {response.status}")
                    return await response.text()
            raise AutoCheckError("Too many redirects")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise AutoCheckError("Network request failed") from err

    async def post_json(self, path: str, data: dict) -> dict:
        raw = await self.request(path, data)
        try:
            value = json.loads(raw)
        except ValueError as err:
            # Distinguish an expired login from an altered API response.
            parse_status(raw)
            raise AutoCheckError("Expected JSON response") from err
        if not isinstance(value, dict):
            raise AutoCheckError("Invalid API response")
        return value

    async def status(self) -> PageStatus:
        return parse_status(await self.request("/user"))

    async def checkin(self) -> tuple[PageStatus, str | None, bool]:
        before = await self.status()
        if before.checked_in:
            return before, None, False
        reply = await self.post_json("/user/checkin", {})
        # The site's JS paints a success button for any HTTP-200 JSON response.
        # Only a new GET from the server is evidence of success.
        after = await self.status()
        if not after.checked_in:
            raise AutoCheckError("Fresh page did not confirm check-in")
        message = unescape(re.sub(r"<[^>]*>", "", str(reply.get("msg", ""))))[:500]
        return after, message or None, reply.get("ret") in (1, "1", True)

    async def telegram_begin(self):
        return parse_telegram(await self.request("/auth/login"))

    async def telegram_finish(self, challenge: dict) -> bool:
        payload = {k: challenge[k] for k in ("token", "number")}
        result = await self.post_json("/auth/qrcode_check", payload)
        if result.get("ret") in (-1, "-1"):
            raise VerificationRequired("Telegram code expired")
        try:
            confirmed = float(result.get("ret", 0)) > 0
        except (ValueError, TypeError):
            confirmed = False
        if not confirmed:
            return False
        await self.post_json("/auth/qrcode_login", {**payload, "remember_me": "on"})
        await self.status()
        return True

    async def password_login(self, email: str, password: str, captcha: dict | None = None):
        html = decode_page(await self.request("/auth/login"))
        if "geetestV4Handler" in html and not captcha:
            raise VerificationRequired("Complete website captcha in a browser and import its session")
        data = {"email": email, "passwd": password, "remember_me": "on", "phase": "password",
                "host": URL(self.base_url).host, "pageLoadedAt": str(int(time.time() * 1000))}
        if captcha:
            data.update({f"captcha_result[{key}]": str(value) for key, value in captcha.items()})
        reply = await self.post_json("/auth/login", data)
        if reply.get("phase") in ("totp", "email_code", "reverse_email_verify"):
            raise VerificationRequired("Complete second factor in browser and import its session")
        await self.status()
