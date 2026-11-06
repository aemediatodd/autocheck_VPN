"""Local browser login assistant. Run on the computer displaying the HA UI."""
from __future__ import annotations

import argparse
import asyncio
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("autocheck_browser_protocol", ROOT / "custom_components/autocheck_vpn/api.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
LOCK = threading.Lock()
FORMS = {}
FORMS_LOCK = threading.Lock()


def form_ticket(query):
    with FORMS_LOCK:
        for key, (_, expires) in list(FORMS.items()):
            if expires <= time.monotonic():
                FORMS.pop(key, None)
        nonce = secrets.token_hex(32)
        FORMS[nonce] = (query, time.monotonic() + 900)
        return nonce


def consume_form(nonce, query):
    with FORMS_LOCK:
        saved = FORMS.pop(nonce, None)
    return bool(saved and saved[0] == query and saved[1] > time.monotonic())


def parameters(query):
    values = parse_qs(query, strict_parsing=True)
    site = api.normalize_url(values["site"][0])
    callback = values["callback"][0]
    parts = urlsplit(callback)
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password
            or parts.path != "/api/autocheck_vpn/browser_login" or parts.query or parts.fragment):
        raise ValueError("Invalid HA callback URL")
    token = values["token"][0]
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("Invalid one-time ticket")
    return site, callback, token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def handoff(callback, token, cookies):
    request = urllib.request.Request(callback, method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"token": token, "cookies": cookies}).encode())
    # A callback must never forward session credentials through a redirect.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=45) as response:
        if json.load(response).get("ok") is not True:
            raise ValueError("HA rejected session")


async def browser_login(site, callback, token, channel):
    from playwright.async_api import async_playwright, Error as BrowserError, TimeoutError as BrowserTimeout
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel=channel, headless=False)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            try:
                await page.goto(site + "/auth/login", wait_until="domcontentloaded", timeout=30000)
            except BrowserError:
                # Keep the window open: the user can refresh a failed or slow page.
                pass
            deadline = time.monotonic() + 840
            remembered = False
            while time.monotonic() < deadline:
                if page.is_closed() or not browser.is_connected():
                    raise ValueError("browser_closed")
                try:
                    parts = urlsplit(page.url)
                    if parts.scheme + "://" + parts.netloc != site:
                        await asyncio.sleep(0.5)
                        continue
                    if not remembered:
                        # The site styles the native checkbox as hidden; clicking it
                        # times out. Set its DOM property and dispatch form events.
                        remembered = await page.evaluate("""() => {
                            const box = document.querySelector('#remember-me');
                            if (!box) return false;
                            box.checked = true;
                            box.dispatchEvent(new Event('input', {bubbles:true}));
                            box.dispatchEvent(new Event('change', {bubbles:true}));
                            return true;
                        }""")
                    if parts.path == "/user" or parts.path.startswith("/user/"):
                        await page.wait_for_selector("#checkin-div", timeout=1000)
                        cookies = {cookie["name"]: cookie["value"] for cookie in await context.cookies(site)}
                        try:
                            await asyncio.to_thread(handoff, callback, token, cookies)
                        except Exception:
                            # Keep browser context alive until the user closes it.
                            # Only controlled error codes leave this function.
                            notice = await context.new_page()
                            await notice.set_content('<meta charset="utf-8"><h1>会话回传未成功</h1>'
                                '<p>登录窗口已保留。请检查本机至 HA 的网络和链接有效期；关闭浏览器后返回 HA 重新生成登录链接。</p>')
                            while browser.is_connected() and not page.is_closed():
                                await asyncio.sleep(0.5)
                            raise ValueError("handoff_failed") from None
                        return
                except (BrowserTimeout, BrowserError):
                    # Temporary redirects and DOM replacement are normal at login.
                    pass
                await asyncio.sleep(0.5)
            raise ValueError("login_timeout")
        finally:
            await browser.close()


class Handler(BaseHTTPRequestHandler):
    channel = "msedge"

    def log_message(self, *_args):
        pass  # Request URLs contain one-time tickets; never log them.

    def html(self, message, status=200):
        body = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="referrer" content="no-referrer"><title>autocheck_VPN 登录</title>'
                '<body style="font:18px sans-serif;max-width:720px;margin:60px auto;line-height:1.8">'
                + message + '</body></html>').encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host") != "127.0.0.1:18765":
            return self.html("无效访问地址。", 400)
        if not urlsplit(self.path).query:
            return self.html("<h1>浏览器助手已就绪</h1><p>请返回 HA 配置页，点击打开登录网页。</p>")
        try:
            query = urlsplit(self.path).query
            site, callback, _ = parameters(query)
        except (ValueError, KeyError):
            return self.html("登录链接无效，请从 HA 重新打开。", 400)
        self.html('<h1>打开账户登录网页</h1><p>网站：<b>' + escape(site)
                  + '</b></p><p>登录后，会话仅回传给此 Home Assistant：<b>' + escape(callback)
                  + '</b></p><p>默认勾选记住我。请在新窗口完成密码、极验及其他验证。</p>'
                  + '<form method="post" action="/login"><input type="hidden" name="query" value="'
                  + escape(query, quote=True) + '"><input type="hidden" name="nonce" value="'
                  + form_ticket(query) + '"><button type="submit">打开网页并登录</button></form>')

    def do_POST(self):
        if (self.path != "/login" or self.headers.get("Host") != "127.0.0.1:18765"
                or self.headers.get("Origin") not in (None, "null", "http://127.0.0.1:18765")):
            return self.html("请从助手的确认页面操作。", 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError()
            form = parse_qs(self.rfile.read(length).decode())
            query = form["query"][0]
            site, callback, token = parameters(query)
            if not consume_form(form.get("nonce", [""])[0], query):
                return self.html("确认页已过期或已使用，请从 HA 重新打开登录链接。", 403)
        except (ValueError, KeyError, UnicodeError):
            return self.html("请求无效。", 400)
        if not LOCK.acquire(blocking=False):
            return self.html("已有登录窗口正在使用，请先完成或关闭该窗口。", 409)
        try:
            asyncio.run(browser_login(site, callback, token, self.channel))
            parts = urlsplit(callback)
            destination = f"{parts.scheme}://{parts.netloc}/config/integrations"
            self.html('<h1>登录已回传 HA</h1><p>会话已由 HA 验证。返回原来的配置对话框，点击提交完成配置。</p>'
                      + '<a href="' + escape(destination, quote=True) + '">返回 Home Assistant</a>')
        except Exception as error:
            reasons = {"browser_closed": "登录浏览器被关闭。", "login_timeout": "等待登录超时，链接已接近过期。",
                       "handoff_failed": "网站登录已完成，但会话未能回传 HA。"}
            reason = reasons.get(str(error), "无法启动或继续登录浏览器。")
            self.html(escape(reason) + " 请检查 HA 地址是否可由本机访问、网站登录是否成功、15 分钟链接是否过期；返回 HA 重新打开登录链接。", 400)
        finally:
            LOCK.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default="msedge", choices=["msedge", "chrome", "chromium"])
    args = parser.parse_args()
    Handler.channel = args.browser
    print("autocheck_VPN browser assistant: http://127.0.0.1:18765 (Ctrl+C to exit)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 18765), Handler).serve_forever()
