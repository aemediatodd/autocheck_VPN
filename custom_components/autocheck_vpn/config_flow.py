"""Configuration, login and options UI."""
from __future__ import annotations

from http.cookies import SimpleCookie
import json
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .api import AutoCheckClient, AutoCheckError, VerificationRequired, normalize_url
from homeassistant.helpers.network import NoURLAvailableError
from .const import DEFAULT_TIME, DEFAULT_TIMEZONE, DEFAULT_URL, DOMAIN, NAME
from .browser_login import begin, registry


def parse_cookies(raw: str) -> dict[str, str]:
    if raw.strip().startswith("{"):
        result = json.loads(raw)
        if not isinstance(result, dict) or not result or not all(isinstance(k, str) and isinstance(v, str) for k, v in result.items()):
            raise ValueError("Invalid cookie dictionary")
        return result
    cookies = SimpleCookie()
    cookies.load(raw.strip())
    if not cookies:
        raise ValueError("No cookies found")
    return {k: v.value for k, v in cookies.items()}


def site_schema(data):
    return vol.Schema({
        vol.Required("base_url", default=data.get("base_url", DEFAULT_URL)): str,
        vol.Required("login_method", default=data.get("login_method", "telegram")): selector.SelectSelector(
            selector.SelectSelectorConfig(options=["password", "telegram"], translation_key="login_method")),
    })


class LoginMixin:
    """Keep challenge cookies in flow memory, never expose its token to the UI."""
    _draft: dict
    _challenge: dict | None = None
    _challenge_cookies: dict | None = None
    _browser_ticket = None
    _browser_token = None
    _browser_url = None

    async def async_step_login(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                base_url = normalize_url(user_input["base_url"])
                if base_url != self._draft.get("base_url"):
                    self._draft.pop("session", None)
                self._draft.update(base_url=base_url, login_method=user_input["login_method"])
            except ValueError:
                errors["base_url"] = "invalid_url"
            else:
                self._challenge = None
                if self._browser_token:
                    registry(self.hass).pop(self._browser_token, None)
                    self._browser_ticket = None
                if self._draft["login_method"] == "telegram":
                    return await self.async_step_telegram()
                return await self.async_step_password()
        return self.async_show_form(step_id="login", data_schema=site_schema(self._draft), errors=errors)

    async def async_step_password(self, user_input=None):
        errors = {}
        if self._browser_ticket and self._browser_ticket["expires"] <= time.monotonic():
            registry(self.hass).pop(self._browser_token, None)
            self._browser_ticket = None
        if not self._browser_ticket:
            try:
                self._browser_token, self._browser_ticket, self._browser_url = begin(self.hass, self._draft["base_url"])
            except NoURLAvailableError:
                # HA may not have a configured internal/external URL yet.
                errors["base"] = "browser_url_missing"
        if self._browser_ticket and self._browser_ticket["session"]:
            self._draft["session"] = self._browser_ticket["session"]
            self._draft.pop("email", None)
            registry(self.hass).pop(self._browser_token, None)
            self._browser_ticket = None
            return await self._login_complete()
        if user_input is not None and not errors:
            errors["base"] = "browser_pending"
        return self.async_show_form(step_id="password", errors=errors, data_schema=vol.Schema({}),
            description_placeholders={"login_url": self._browser_url or "http://127.0.0.1:18765"})

    async def async_step_telegram(self, user_input=None):
        errors = {}
        client = AutoCheckClient(self._draft["base_url"], self._challenge_cookies if self._challenge else None)
        try:
            if self._challenge and user_input is not None:
                if await client.telegram_finish(self._challenge):
                    self._draft["session"] = client.cookies()
                    self._draft.pop("email", None)
                    self._challenge = None
                    return await self._login_complete()
                errors["base"] = "telegram_pending"
            if not self._challenge:
                self._challenge = await client.telegram_begin()
                self._challenge_cookies = client.cookies()
        except VerificationRequired:
            self._challenge = None
            self._challenge_cookies = None
            errors["base"] = "telegram_expired"
        except AutoCheckError:
            errors["base"] = "cannot_connect"
        finally:
            await client.close()
        if self._challenge is None:
            return self.async_show_form(step_id="telegram", data_schema=vol.Schema({}), errors=errors,
                                        description_placeholders={"code": "------", "bot": "（重新提交获取验证码）"})
        return self.async_show_form(step_id="telegram", data_schema=vol.Schema({}), errors=errors,
            description_placeholders={"code": self._challenge["number"], "bot": self._challenge["bot"]})


class AutoCheckConfigFlow(LoginMixin, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return AutoCheckOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        self._draft = {}
        return await self.async_step_login(user_input)

    async def async_step_reauth(self, entry_data):
        self._draft = dict(entry_data)
        return await self.async_step_login()

    async def async_step_reconfigure(self, user_input=None):
        self._draft = dict(self._get_reconfigure_entry().data)
        return await self.async_step_login(user_input)

    async def _login_complete(self):
        if self.source == "reauth":
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=self._draft)
        if self.source == "reconfigure":
            return self.async_update_reload_and_abort(self._get_reconfigure_entry(), data_updates=self._draft)
        return self.async_create_entry(title=NAME, data=self._draft)


class AutoCheckOptionsFlow(LoginMixin, config_entries.OptionsFlow):
    def __init__(self, entry):
        self._entry = entry
        self._draft = dict(entry.data)
        self._settings = dict(entry.options)

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["settings", "login"])

    async def async_step_settings(self, user_input=None):
        errors = {}
        values = user_input or self._settings
        if user_input is not None:
            try:
                ZoneInfo(user_input["timezone"])
                # Parse here as well as in the scheduler.
                from datetime import time
                time.fromisoformat(user_input["checkin_time"])
            except (ValueError, ZoneInfoNotFoundError):
                errors["base"] = "invalid_schedule"
            else:
                return self.async_create_entry(title="", data=user_input)
        schema = vol.Schema({
            vol.Required("auto_checkin", default=values.get("auto_checkin", True)): bool,
            vol.Required("checkin_time", default=values.get("checkin_time", DEFAULT_TIME)): selector.TimeSelector(),
            vol.Required("jitter_minutes", default=values.get("jitter_minutes", 30)): vol.All(vol.Coerce(int), vol.Range(min=0, max=300)),
            vol.Required("timezone", default=values.get("timezone", DEFAULT_TIMEZONE)): str,
            vol.Required("notify_success", default=values.get("notify_success", True)): bool,
            vol.Required("refresh_interval_hours", default=values.get("refresh_interval_hours", 6)): vol.All(vol.Coerce(float), vol.Range(min=1, max=12)),
            vol.Required("refresh_jitter_hours", default=values.get("refresh_jitter_hours", 1)): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=2)),
        })
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)

    async def _login_complete(self):
        self.hass.config_entries.async_update_entry(self._entry, data=self._draft)
        await self.hass.config_entries.async_reload(self._entry.entry_id)
        return self.async_create_entry(title="", data=self._settings)
