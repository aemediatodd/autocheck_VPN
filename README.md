# autocheck_VPN

Home Assistant 自定义集成，为兼容网站提供每日自动签到、Telegram 六位码登录、登录失效提示和签到统计。网站域名由用户填写，可随时修改。

## 功能

- 登录状态失效时显示“需要登录”，并通知重新登录。
- 签到后重新读取服务器页面，确认成功后显示“当天签到成功”。
- 记录累计成功次数、当前连续成功天数、最近成功时间和最近流量奖励。
- 提供手动签到和状态刷新按钮。
- 支持每日基准时间及前后各最多 5 小时的随机浮动，并显示实际触发时间。
- 随机时间和统计持久保存，正常重启不会重新抽取。
- 账户密码登录默认“记住我”；支持人工完成网页验证后导入会话。
- Telegram 登录在 HA 配置页显示本次六位验证码和对应机器人。

## 兼容范围

适用于具有以下页面和接口结构的网站：

- 用户页：`GET /user`，签到容器为 `#checkin-div`。
- 签到接口：`POST /user/checkin`。
- 成功标志：签到容器中的禁用按钮及“明日再来”等文字。
- Telegram 登录：`/auth/qrcode_check` 和 `/auth/qrcode_login`，验证码及机器人名称从登录页读取。

支持页面使用的 `originBody` Base64 包装。仅修改域名即可适配同一页面结构的域名迁移；网站更改接口或布局时需要更新代码。

## 安装

### HACS 自定义仓库

1. 在 HACS 的 **自定义存储库** 中添加 `https://github.com/aemediatodd/autocheck_VPN`，类别选择 **集成**。
2. 下载后重启 Home Assistant。
3. 在 **设置 → 设备与服务 → 添加集成** 中搜索 **autocheck_VPN**。

### 手动安装

1. 从 [Releases](https://github.com/aemediatodd/autocheck_VPN/releases) 下载 `autocheck_vpn-0.1.0.zip`。
2. 将包内 `custom_components/autocheck_vpn` 复制到 HA 的 `/config/custom_components/autocheck_vpn`。
3. 重启 HA，然后添加 **autocheck_VPN**。

基于 Home Assistant 2026.2.3 API 开发。HA 集成域名和组件目录使用小写 `autocheck_vpn`。

## 登录

首次配置时填写网站的 **HTTPS 根域名**，不要包含 `/user` 等路径。下文的 `https://vpn.example.com` 仅为格式示例，请替换为你的实际域名。

### Telegram

1. 选择 Telegram 登录，配置页显示从目标网站取得的六位验证码及机器人名称。
2. 手动将六位码发给对应机器人。
3. 返回 HA 点击 **提交** 检查登录结果。尚未确认可再次提交；验证码过期后再次提交获取新码。

需先按网站要求完成 Telegram 账户绑定，无需向本集成提供 Telegram API Key 或机器人 Token。

### 账户密码与“记住我”

账户密码请求默认携带 `remember_me=on`，浏览器登录工具默认勾选“记住我”。密码仅用于当次请求，不保存；登录会话会保存在 HA 配置中并复用，因此每次签到无需重新登录。

**若目标网站要求极验、邮件码或双因素验证，需要先在浏览器完成人工验证，再导入登录会话。** 在包含 `tools` 目录的项目文件夹内运行：

```powershell
python -m pip install aiohttp playwright
python tools/verify.py --url https://vpn.example.com
```

工具默认打开本机 Edge 并勾选“记住我”。在窗口登录后，工具会尝试签到，重新读取页面验证结果，检查重复调用，并导出 `.local/session-cookie.json`。将该文件的完整内容粘贴到 HA 配置页的 **登录会话** 字段。已有会话时，邮箱和密码可留空。

其他浏览器可用 `--browser chrome`；使用 `--browser chromium` 前运行 `python -m playwright install chromium`。HACS 用户可下载项目源码或 Release 安装包取得 `tools/verify.py`。

本机测试 Telegram 登录可运行：

```powershell
python tools/verify.py --url https://vpn.example.com --telegram
```

工具会显示六位码和机器人链接，等待你手动发送。会话失效后，在 **集成配置 → 修改域名 / 重新登录** 中重新验证。

## 自动签到时间

默认每天 `09:00:00`，浮动 `±30` 分钟，时区 `Asia/Hong_Kong`。可在 **集成配置 → 自动签到设置** 中修改。

| 设置 | 实际随机范围 |
|---|---|
| `03:00 ±5 小时` | 当天 `00:00–08:00` |
| `22:00 ±5 小时` | 当天 `17:00–23:59:59` |
| `09:00 ±0 分钟` | 当天 `09:00` |

随机范围限制在同一自然日。每个签到日保存一次随机结果，**下次自动签到时间** 实体显示实际触发时间，属性显示基准时间、浮动分钟数、随机时刻和允许范围。

- HA 停机错过触发时间后，若仍在允许范围内，会补尝试一次；超出范围则安排明天。
- 网络错误最多当天尝试 3 次，间隔 15 分钟，重试也不超出允许范围。
- 会话失效时等待人工重新登录。
- 每 15 分钟检查网页状态，也可点击刷新立即检查。
- 手动签到不受自动时间范围限制。

## 状态与统计

| 实体 | 含义 |
|---|---|
| 签到状态 | 今日待签到 / 当天签到成功 / 需要登录 / 检查失败 |
| 累计签到成功次数 | 集成首次确认以来成功的不同自然日数 |
| 当前连续签到成功天数 | 相邻自然日确认成功才递增，漏签后归零 |
| 最近签到成功时间 | 集成最近一次确认新的成功日的时间 |
| 最近签到流量奖励 | 实际签到接口返回的奖励原文及奖励日期 |
| 下次自动签到时间 | 已保存的下一次执行时间 |
| 手动签到 | 立即检查并按需签到 |
| 刷新登录与签到状态 | 立即检查会话及网页状态 |

同一天重复点击或刷新不会重复计数。网站未提供的历史次数和实际签到时刻不会推算；若当天已在网页签到，集成确认后计一个成功日，时间记录为确认时刻。该情况下不会编造流量奖励，会保留最近一次实际取得的奖励及其日期。

更换账户时请新增独立的集成条目，以分别保存统计。

## 开发与验证

```powershell
python -m pip install homeassistant==2026.2.3 pytest pytest-asyncio tzdata ruff
python -m pytest -q
python -m ruff check custom_components tools tests
python tools/build.py
```

安装包输出到 `dist/autocheck_vpn-0.1.0.zip`。真实站点验证及测试范围见 [VALIDATION.md](VALIDATION.md)。

本机登录会话位于 `.local`，已加入忽略规则；发布构建只包含组件、验证工具和说明。自动跨域重定向被禁止，以便用户在域名变化时自行确认并更新配置。
