"""Local real-site verification. Account data never appears in console output."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("autocheck_protocol", ROOT / "custom_components/autocheck_vpn/api.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


async def verify_http(url, cookies, out):
    client = api.AutoCheckClient(url, cookies)
    try:
        before = await client.status()
        after, reward, performed = await client.checkin()
        repeated, _, duplicate = await client.checkin()
        result = {"before_checked_in": before.checked_in,
                  "after_checked_in": after.checked_in,
                  "marker": after.marker, "performed": performed,
                  "reward": reward, "repeat_checked_in": repeated.checked_in,
                  "repeat_performed": duplicate}
        (out / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "session-cookie.json").write_text(json.dumps(client.cookies()), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        assert after.checked_in and repeated.checked_in and not duplicate
    finally:
        await client.close()


async def main(args):
    url = api.normalize_url(args.url)
    out = ROOT / ".local"
    out.mkdir(exist_ok=True)
    if args.telegram:
        client = api.AutoCheckClient(url)
        try:
            challenge = await client.telegram_begin()
            # Retain a pending challenge locally so a transient tool failure can be resumed.
            (out / "pending-telegram.json").write_text(json.dumps({"challenge": challenge, "cookies": client.cookies(), "url": url}), encoding="utf-8")
            print(f"Send {challenge['number']} to https://t.me/{challenge['bot']} within 5 minutes.", flush=True)
            for _ in range(120):
                if await client.telegram_finish(challenge):
                    cookies = client.cookies()
                    (out / "pending-telegram.json").unlink(missing_ok=True)
                    break
                await asyncio.sleep(2.5)
            else:
                raise TimeoutError("Telegram login not confirmed")
        finally:
            await client.close()
    elif args.cookie_file:
        cookies = json.loads(Path(args.cookie_file).read_text(encoding="utf-8"))
    else:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel=args.browser, headless=False)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(url + "/auth/login", wait_until="domcontentloaded")
                await page.locator("#remember-me").check()
                print("Remember me is checked. Log in in the browser window.", flush=True)
                await page.wait_for_url(url + "/user**", timeout=900000)
                await page.wait_for_selector("#checkin-div")
                state = await context.cookies(url)
                cookies = {c["name"]: c["value"] for c in state}
                # HTTP implementation makes the request, then browser independently verifies.
                await verify_http(url, cookies, out)
                await page.goto(url + "/user", wait_until="domcontentloaded")
                await page.wait_for_selector("#checkin-div")
                marker = await page.locator("#checkin-div").inner_html()
                api.parse_status('<div id="checkin-div">' + marker + '</div>')
                assert api.parse_status('<div id="checkin-div">' + marker + '</div>').checked_in
                (out / "checkin-marker.html").write_text(marker, encoding="utf-8")
                await page.locator("#checkin-div").screenshot(path=str(out / "checkin-marker.png"))
                print("Browser fresh-page marker verified.")
                return
            finally:
                await browser.close()
    await verify_http(url, cookies, out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Website HTTPS origin")
    parser.add_argument("--browser", default="msedge", help="msedge, chrome, or chromium")
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--cookie-file")
    asyncio.run(main(parser.parse_args()))
