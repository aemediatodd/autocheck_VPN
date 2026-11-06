"""Generate matching config and options translations."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "custom_components/autocheck_vpn"
errors = {
    "invalid_url": "请输入 HTTPS 根域名，例如 https://vpn.example.com，不要包含 /user。",
    "invalid_auth": "登录失败或会话失效，请重新登录。",
    "invalid_cookie": "会话格式无效，请粘贴本机验证工具生成的 session-cookie.json 内容。",
    "cannot_connect": "无法连接或网站结构已变化，请检查域名、网络及集成版本。",
    "verification_required": "网站要求极验或二次验证。请运行配套浏览器工具完成账户密码登录，再在下方粘贴导出的会话内容。默认已勾选记住我。",
    "telegram_pending": "机器人尚未确认，请发送六位码后再次提交。",
    "telegram_expired": "验证码已过期，再次提交以获取新的六位码。",
    "invalid_schedule": "时间或时区格式不正确，例如 09:00:00、Asia/Hong_Kong。",
    "browser_pending": "尚未收到浏览器会话。请点击登录链接，在助手窗口登录；显示回传成功后再次提交。",
    "browser_url_missing": "无法取得 HA 地址，请先在 HA 网络设置中配置本机可访问的 Home Assistant URL。",
}
steps = {
    "login": {"title": "域名与登录方式", "description": "域名变更后可在此重新配置。账户密码和 Telegram 登录均默认记住我。",
              "data": {"base_url": "网页域名", "login_method": "登录方式"}},
    "password": {"title": "网页账户登录", "description": "请先在正在使用 HA 的电脑上运行 `python tools/browser_login.py`（首次安装 `python -m pip install aiohttp playwright`）。\n\n[打开登录网页]({login_url})\n\n在助手确认页点击打开网页，默认勾选记住我。完成账户密码及网页验证后，会话会自动回传 HA，无需复制 Cookie。助手显示回传成功后，返回此处点击提交。链接有效期 15 分钟，过期后提交获取新链接。手机或其他电脑不能复用这台电脑的本机助手。"},
    "telegram": {"title": "Telegram 机器人登录", "description": "本次六位验证码：**{code}**\n\n请手动发送给机器人 **@{bot}**。发送后点击提交检查登录结果；未确认可再次提交。验证码仅用于本次登录。默认启用记住我，登录会话将保存并复用。"},
    "settings": {"title": "自动签到设置", "description": "浮动分钟数表示基准时间前后各可浮动的范围，最多 ±300 分钟（5 小时）。靠近午夜时限制在当天。",
                 "data": {"auto_checkin": "启用自动签到", "checkin_time": "每日签到基准时间", "jitter_minutes": "上下浮动分钟数（0–300）", "timezone": "网站及签到时区", "notify_success": "签到成功时显示通知", "refresh_interval_hours": "状态查询间隔（1–12 小时）", "refresh_jitter_hours": "状态查询前后浮动（0.5–2 小时）"}},
    "init": {"title": "autocheck_VPN 配置", "menu_options": {"settings": "自动签到设置", "login": "修改域名 / 重新登录"}},
}
data = {
    "title": "autocheck_VPN",
    "config": {"step": {k: v for k, v in steps.items() if k in ("login", "password", "telegram")}, "error": errors,
               "abort": {"reauth_successful": "已重新登录。", "reconfigure_successful": "配置已更新。"}},
    "options": {"step": steps, "error": errors},
    "selector": {"login_method": {"options": {"password": "账户密码（默认记住我）", "telegram": "Telegram 机器人六位码"}}},
    "entity": {
        "sensor": {
            "status": {"name": "签到状态", "state": {"unknown": "等待检查", "ready": "今日待签到", "checked_in": "当天签到成功", "login_required": "需要登录", "error": "签到或网页检查失败"}},
            "total": {"name": "累计签到成功次数"}, "streak": {"name": "当前连续签到成功天数"},
            "last_success": {"name": "最近签到成功时间"}, "last_reward": {"name": "最近签到流量奖励"},
            "next_run": {"name": "下次自动签到时间"},
        },
        "button": {"checkin": {"name": "手动签到"}, "refresh": {"name": "刷新登录与签到状态"}},
    },
}
(root / "translations").mkdir(exist_ok=True)
for filename in [root / "strings.json", root / "translations/zh-Hans.json", root / "translations/zh-Hant.json"]:
    filename.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
