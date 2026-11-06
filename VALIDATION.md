# 验证记录

## v0.1.1（2026-11-06）

Telegram 完成登录请求默认携带 `remember_me=on`，配置页同步显示“默认启用记住我”。协议测试检查验证码、挑战 Token 和记住我参数同时传入登录接口。此更新通过本机自动化测试；网站是否延长 Cookie 有效期由服务器实现决定，本次未重新执行真实账户登录。

日期：2026-11-04（Asia/Hong_Kong，按用户客户端日期）。

## 真实网站验证

- 使用本机 Edge 浏览器访问目标站点登录页，确认账户密码、极验 v4、记住我选项及 Telegram 六位码登录界面。目标站点域名与机器人账户不随项目发布。
- 浏览器登录后调用页面 `checkin()`，实际提交 `POST /user/checkin`。
- 网站实际返回：`{"msg":"你获得了 512 MB流量","ret":1}`。
- 随后重新访问 `/user`，确认服务器页面的 `#checkin-div` 含禁用按钮、`far fa-calendar-check` 图标及 **明日再来**。
- 使用集成的 `AutoCheckClient.status()` 从全新 HTTP 响应识别同一标志，结果为成功。
- 再次执行 `AutoCheckClient.checkin()`，确认 `checked_in=True`、`performed=False`，不重复 POST。
- Telegram：用户手动发送网站六位码并收到机器人成功回复，独立 HTTP 脚本成功完成 `/auth/qrcode_check → /auth/qrcode_login → /user` 会话验证。该网站确认接口按网页逻辑将任意正数返回值视为通过。
- Telegram 会话中再次核验签到标志及重复调用，均成功。

账户密码的交互验证由用户在真实网站浏览器完成。当前极验不能直接在 HA 配置表单中完成；提供默认勾选记住我的浏览器登录工具及会话导入。未宣称纯账户密码 HTTP 请求能够绕过极验。

## 自动化测试

结果：**45 项测试全部通过**；Ruff 静态检查与 Python 编译检查通过。测试输出包含一项 HA 自身 HTTP 模块的弃用警告。

覆盖页面包装解码、成功标志范围、登录失效、结构变化、HTTP 200 假成功、重复签到、记住我参数、极验处理、Telegram 正数确认及过期、跨域凭据保护、统计持久化、连续天数、漏签、时钟回拨、随机范围、配置 UI、并发操作、重启恢复、补执行、重试限制。

特别验证 `03:00 ±300 分钟` 只在当天 `00:00–08:00` 抽取；晚间范围不会跨越午夜。补执行和网络重试也不超出当天允许范围。

本机已有 Home Assistant 2026.2.3。测试使用该版本的配置流、协调器和实体 API；网络、存储及通知在单元测试中按测试目标隔离。

本机预装的 `pytest-homeassistant-custom-component` 插件缺少部分依赖，因此关闭自动插件加载，仅显式启用测试所需的 asyncio 插件：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q -p pytest_asyncio.plugin
python -m ruff check custom_components tools tests
python -m compileall -q custom_components tools
```

本次没有连接或修改用户的 HA 服务器；安装到用户 HA 后的完整运行和跨日长期验证仍需在实际环境观察。

## 构建隔离

发布包只包含集成代码、翻译、验证工具及说明。浏览器登录会话、Cookie、Telegram 挑战 Token、网页完整账户资料均留在本机私有目录，没有进入安装包。
