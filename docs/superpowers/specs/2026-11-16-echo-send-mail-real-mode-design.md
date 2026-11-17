# Echo `send_mail` 真实模式（mock / verify / send）（2026-11-16）

## 背景

平台内置「NanZi Echo 测试 MCP」的 `send_mail`
（`app/services/mcp/echo_server.py:406`）目前是**纯模拟**：它不建任何 SMTP 连接，
只把注入进来的参数做成「回执」返回——非敏感参数给明文，`password` / `mailkey`
给掩码 + `sha256[:12]` 指纹，最后附一句 `已模拟给 {to} 发送邮件`。

这个设计对「值有没有到达工具」是有效的验收（参数名刻意与「邮箱 SMTP 发送」凭据模板
字段一一对齐，映射面板能一键铺满），但它**无法回答另一个问题：这些值能不能用**。
密码填错、把 465 的授权码配到 587、授权码已过期、账号被禁用——回执里依然全是
`present: true`，看起来一切正常。用户要测的是「凭证注入是否真的有效」，而当前的
回执只能证明前半段。

平台确实有真实发信能力（`app/services/email_delivery_service.py:634`
`EmailDeliveryService.send_mail`），但它解析的是**系统配置里的全局 SMTP** 或
**个人中心 → 消息通知里的自定义 SMTP**，与「凭据注入进来的参数」是两条独立来源。
直接复用它，测的就不是注入链路，而是通知配置。

因此需要给 Echo 的 `send_mail` 增加一种能力：**用注入进来的那套参数去真连 SMTP**。

## 目标

1. 同一个工具、同一套凭据槽位（`arg:send_mail:*`）下，可按需用**注入参数**真实连接
   SMTP：`verify` 验到登录成功（不发信），`send` 真发一封到 `to`。
2. 失败结果能区分两类根因——**「注入没到位」** 与 **「注入到位了但凭据本身无效」**。
3. `mode` 不传时行为与返回体和今天**逐字段一致**，既有注入验收用法不受影响。

## 非目标（明确不做）

| 不做的 | 原因 |
|---|---|
| 改动 `EmailDeliveryService` 的发送链路 | 它服务于系统通知 / 密码重置 / 个人测试发信，来源是配置而非注入；改它等于把两个来源混在一起 |
| 把 `mode` 做成服务级开关（DB 字段 + 管理页） | 需要迁移与前端改造，收益不抵「默认 mock」带来的同等安全性 |
| 防 DNS rebinding（解析后钉死 IP + SNI 校验） | 测试工具的收益/复杂度比不合适；残余风险在本文件「已知取舍」中如实标注 |
| 提供「关闭证书校验」的开关 | 自签证书场景与「拒绝内网地址」的守卫冲突，先不做，避免开一个静默降级的口子 |
| 支持无认证 SMTP | `verify` / `send` 要求 `host` + `username` + `password` 齐备；纯连通性测试另有需求再加 `require_auth=false` |
| 给 `echo` / `echo_slow` 加真实能力 | 与本需求无关 |

## 架构

```
智能体 / 用户
  └─ MCP tools/call: Echo:send_mail(mode=...)
       └─ mcp_client 凭据注入（arg:send_mail:{host,port,security,username,password,from_name,mailkey}）
            └─ echo_server.send_mail（FastMCP tool）
                 ├─ mode == "mock"（默认）
                 │     └─ 现状回执，逐字段不变
                 └─ mode == "verify" | "send"
                       ├─ _require_real_params()   host/username/password 缺任一 → ok:false, reason=missing_params
                       ├─ _guard_smtp_host()       字面 IP 直判 / 域名 DNS 全量解析后判定 → reason=blocked_address
                       ├─ _resolve_security()      显式值优先，缺省复用 EmailDeliveryService.infer_security
                       └─ _run_real_smtp()         线程池执行 smtplib，阶段机 + 超时，返回 real 结果
```

所有阻塞的 `smtplib` / `socket.getaddrinfo` 调用一律丢线程池
（`loop.run_in_executor`），不得直接阻塞事件循环——这与
`app/api/portal/endpoints/auth.py:1008`、`notification_tools.py:261` 的既有做法一致。

## 参数契约

新增两个参数，**一律追加在参数列表末尾**：`tests/secrets/test_locator_e2e.py:231`
用位置参数调用 `send_mail(None, "A", "hi", None, ..., "sk-alpha-111111")`，中间插入新参数
会静默改变这些测试的语义。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `mode` | `Literal["mock", "verify", "send"]` | `"mock"` | `mock` 保持现状；`verify` 真连 + 真登录但不发信；`send` 真发一封到 `to`。用 `Literal` 让 FastMCP 生成 enum schema，模型不会传出第四个值 |
| `body` | `string`，可选 | 自动生成 | 仅 `send` 使用。不给时用内置模板（含 mode、时间戳、回执摘要）；`verify` 下传了 `body` 时**忽略**，不报错 |

### 参数校验分级（verify / send 在建立任何连接之前完成）

| 检查 | 不通过时 reason | message 要说清的 |
|---|---|---|
| `host` 为空 / 含控制字符 / 超过 255 字符 | `invalid_params` | 哪一项、为什么 |
| `port` 不在 1..65535 | `invalid_params` | 收到的值 |
| `security` 不在 `ssl` / `starttls` / `none` | `invalid_params` | 收到的值 + 允许值 |
| `to` 不是单个合法邮箱（含逗号 / 分号 / 无 `@`） | `invalid_params` | 只支持单个收件人 |
| `host` / `username` / `password` 任一为空 | `missing_params` | **缺哪几个**，并提示「检查凭据绑定是否生效」 |

`from_name` 为空时不带发件人显示名，只用 `username` 作为 From；`mailkey` 只进回执，不参与 SMTP。

`port` 与 `security` 允许缺省：`port=None` 时按 `security` 推断（`ssl`→465，其余→587）；
`security=None` 时复用 `EmailDeliveryService.infer_security` 按端口推断（465→ssl，其余→starttls）。
两者都缺省时落到 `587 + starttls`，与个人中心「测试发信」的既有推断同源。

`to` 仍为唯一 `required`。其余参数名（`host` / `port` / `security` / `username` /
`password` / `from_name` / `mailkey`）**一个都不能改**——它们与「邮箱 SMTP 发送」模板
字段的对齐关系是映射面板「按名字自动匹配」的前提。

### 两份 schema 必须同步

- **生效路径**：FastMCP 按函数签名自动生成的 schema，这是模型真正看到的参数表。
- **手写副本**：`send_mail_tool_schema()`（`echo_server.py:456`）。当前**没有任何产品
  代码调用它**（仅测试在用），但它存在的意义是「echo 服务连不上时管理页仍能展示参数」，
  签名一改它就是错的。本次必须同步补 `mode` / `body`，并在 docstring 里点明两份的关系。

## 阶段机

`verify` 与 `send` 共用前置阶段；每个阶段记录自己的名字，失败即止：

| # | stage | 动作 | 常见失败 reason |
|---|---|---|---|
| 0 | `guard` | 参数校验分级 + 出站地址守卫 | `missing_params` / `invalid_params` / `blocked_address` |
| 1 | `dns` | 域名解析（守卫内部完成，失败单列） | `dns_failed` |
| 2 | `connect` | 建连 + TLS 握手 | `connect_failed` / `timeout` / `cert_error` |
| 3 | `ehlo` | EHLO | `connect_failed` |
| 4 | `starttls` | 仅 `security=starttls` 时 | `cert_error` / `connect_failed` |
| 5 | `login` | 仅当 `username` + `password` 齐备（本次恒成立） | `auth_failed` |
| 6 | `sendmail` | 仅 `mode=send` | `send_rejected` |

- `verify` 在 `login` 成功后 `quit()` 收尾，`stage` 定格为 `authenticated`。
- `send` 在 `sendmail()` 成功后 `quit()`，`stage` 定格为 `sent`。
- 连接建立后 `login` / `sendmail` 阶段把 socket 超时放宽到 30s（`sendmail` 可能被对端
  拖慢），其余阶段 10s；总耗时记入 `real.duration_ms`。

## 出站地址守卫

不能直接复用 `app/services/mcp/url_policy.py` 的 `_ip_is_blocked`：那里
**明确放行私网与 loopback**（MCP 服务常部署在私网），注释写在 `url_policy.py:33`。
SMTP 侧需要更严格的判定，因此在 `echo_server.py` 内新增独立守卫：

1. host 先过去空白 / 控制字符 / 反斜杠校验，长度上限 255。
2. 字面 IP（含 `[::1]` 方括号形式）：直接判定。
3. 域名：`socket.getaddrinfo(host, port, proto=IPPROTO_TCP)` 取**全部** IPv4/IPv6 地址
   去重后逐个判定；解析异常 → `reason=dns_failed`。
4. 判定条件（任一命中即拒绝）：`is_loopback` / `is_private` / `is_link_local` /
   `is_reserved` / `is_multicast` / `is_unspecified`，外加 `url_policy.py:34`
   里那组云元数据网段（含 IPv4-mapped IPv6 形式）。
5. 拒绝时 `reason=blocked_address`，`real.blocked` 回显命中的具体地址，便于判断
   「是域名解析到了 127.0.0.1」还是「直接填了 10.x」。

## 返回结构

`mock` 分支返回体**逐字段不变**。`verify` / `send` 在既有键之上增加 `mode` 与 `real`，
`ok` 由真实结果决定：

```jsonc
{
  "ok": false,
  "mode": "verify",
  "to": "a@b.com",
  "subject": "注入验收",
  "smtp": {
    "host":     {"present": true, "value": "smtp.qq.com"},
    "port":     {"present": true, "value": 465},
    "security": {"present": true, "value": "ssl"},
    "username": {"present": true, "value": "ops@b.com"},
    "password": {"present": true, "masked": "****", "fingerprint": "a1b2c3d4e5f6", "length": 16},
    "from_name":{"present": true, "value": "运维"}
  },
  "mailkey": {"present": false},
  "real": {
    "attempted": true,
    "host": "smtp.qq.com", "port": 465, "security": "ssl",
    "stage": "login", "code": 535, "duration_ms": 1180,
    "reason": "auth_failed",
    "server": "535 Login Fail. Please enter your authorization code to login."
  },
  "message": "真实校验失败：认证被拒。注入链路是通的（host/账号已到达），请检查凭据里的账号或授权码"
}
```

`real` 在 `verify` / `send` 下**恒存在**（`mock` 下不出现）：`attempted` 表示是否真的发起过
连接——参数校验或地址守卫拦下时为 `false`，此时 `real` 仍带 `reason` 与 `message` 所需的
信息；被守卫拦下时额外带 `blocked: ["127.0.0.1"]`（命中的具体地址列表）。

`reason` 枚举（固定，前端与测试可依赖）：`ok` / `missing_params` / `invalid_params` /
`blocked_address` / `dns_failed` / `timeout` / `cert_error` / `connect_failed` /
`auth_failed` / `send_rejected`。

### 三类失败的文案必须能一眼分开

这是本需求的核心价值，文案要写死：

| 情况 | reason | `message` 要传达的意思 |
|---|---|---|
| 参数没到（绑定没生效 / 模型没填） | `missing_params` | 「注入链路没跑起来」+ 缺哪几个参数 |
| 参数到了但不合法（端口/加密方式/收件人填错） | `invalid_params` | 「值到了，但用法不对」+ 具体哪一项 |
| 参数到了但登录被拒 | `auth_failed` | 「注入链路是通的，凭据本身不可用」 |
| 域名解析到内网/回环 | `blocked_address` | 命中的地址 + 这是安全拦截、不是凭据问题 |

## 安全与脱敏

- `password` / `mailkey` 明文**永不**出现在返回值、日志、错误文本中；沿用
  `_receipt()` 的掩码 + 指纹口径。
- 所有传给调用方的 SMTP 错误文本过
  `EmailDeliveryService.sanitize_error_text(text, password)`，避免认证失败信息里
  回显口令。
- `real.server` 截断到 300 字符。
- 日志只记 `host` / `port` / `stage` / `duration_ms` / `reason`，不记凭据与正文。
- TLS 证书校验保持默认开启，不提供关闭入口。

## 兼容性

| 受影响面 | 处理 |
|---|---|
| `mode` 缺省 | 走 `mock`，返回体与今天逐字段一致 |
| 既有位置参数调用（`test_locator_e2e.py:231`） | 新参数追加在末尾，位置参数语义不变 |
| 凭据槽位 | 槽位名不变（`arg:send_mail:*`），已配置的绑定无需重配 |
| 前端参数映射面板 | 生效 schema 来自 FastMCP 自动生成，会新增 `mode` / `body` 两行；`body` 属普通参数，标「不需要凭据」即可 |
| `send_mail_tool_schema()` | 同步补 `mode` / `body`，与签名保持一致 |

## 代码落点

| 文件 | 改动 |
|---|---|
| `app/services/mcp/echo_server.py` | `send_mail` 增 `mode` / `body` 参数并分支；新增 `_require_real_params()`、`_guard_smtp_host()`、`_resolve_security()`、`_run_real_smtp()`、`_build_default_body()`；`ECHO_MAIL_TOOL_DESCRIPTION` 增补三档说明；`send_mail_tool_schema()` 同步 |
| `app/services/email_delivery_service.py` | **不改**，仅 import 复用 `infer_security()` / `sanitize_error_text()` / `MASK` |
| `app/services/mcp/url_policy.py` | **不改**；其中的元数据网段常量如需复用，以显式常量复制到 `echo_server.py` 并注明来源，避免把「放行私网」的策略耦合进来 |
| `tests/secrets/test_locator_e2e.py` | 给 `mode` 缺省行为补断言（返回体键集不含 `real`） |

## 测试计划

| 用例 | 断言 |
|---|---|
| `mode` 缺省 | 返回体与改造前键集一致，无 `real`，`message` 仍是「已模拟…」 |
| `verify` 缺 `password` | `ok=false`、`reason=missing_params`、`real.attempted=false`，且**不得**降级成 mock |
| host 为 `127.0.0.1` / `10.0.0.5` / `169.254.169.254` / `[::1]` / `fd00::1` / `0.0.0.0` | `reason=blocked_address`，`real.blocked` 含该地址 |
| 域名解析到私网（monkeypatch `getaddrinfo` 返回 `10.1.2.3`） | `reason=blocked_address`，`real.blocked` 含 `10.1.2.3` |
| `port=99999` / `security=obsolete` / `to="a@b.com,c@d.com"` | `reason=invalid_params`，且**未发起任何连接**（连接替身零调用） |
| 假 SMTP：认证成功 | `verify` → `ok=true`、`stage=authenticated`、未产生 `sendmail` 记录 |
| 假 SMTP：535 认证失败 | `ok=false`、`stage=login`、`code=535`、`reason=auth_failed`、`message` 含「注入链路是通的」 |
| 假 SMTP：`sendmail` 被 5xx 拒 | `ok=false`、`stage=sendmail`、`reason=send_rejected` |
| 连接超时 / 拒绝连接 | `reason=timeout` / `connect_failed`，`duration_ms` 有值 |
| 脱敏 | 密码不出现在任何字段；错误文本里若含密码则被替换为掩码 |

测试基础设施：`requirements.txt` 没有 `aiosmtpd`，用进程内最小假 SMTP 服务器
（`socket` + 线程，按行应答 `220` / `250` / `235` / `535` / `354` / `250 ok`）覆盖上述
路径，不引入新依赖。

**注**：由于守卫拒绝回环地址，假 SMTP 服务器无法在 `127.0.0.1` 上被真实连上。
测试通过**注入替身**解决——守卫函数与连接函数各自可被 monkeypatch，成功路径替身掉
守卫、失败路径替身掉握手结果，避免为了可测性在守卫上开后门。

## 已知取舍

1. **DNS rebinding 未防**：守卫校验的是解析结果，连接时 `smtplib` 仍按 hostname 重新
   解析。窗口存在，测试工具接受；真要收紧得钉死 IP 并保留 SNI/证书校验 hostname。
2. **`mode` 由模型按对话意图传入**：安全性靠「默认 mock + 地址守卫」兜底。模型误传
   `send` 时最多发一封到对话里出现的地址，且必须依赖已注入的真实凭据——不会凭空产生
   凭据。
3. **无认证 SMTP 不可测**：`username` / `password` 必填挡住了无认证中继场景（那类服务器
   本身也不该作为「凭据注入」的验收目标）。
4. **`send_mail_tool_schema()` 是没人调的死副本**：本次只做同步维护，不做删除（删掉前
   需确认管理页离线兜底是否依赖它，属于另一项清理）。

## 验收标准

1. `mode` 缺省时，`tests/secrets/test_locator_e2e.py` 的既有断言全部仍然通过。
2. 用一条**真实** SMTP 凭据（如 QQ 邮箱授权码）跑 `mode=verify`：配错授权码时返回
   `reason=auth_failed` 且文案指明「注入链路是通的」；配对时返回 `stage=authenticated`。
3. 同一凭据跑 `mode=send`，能在目标邮箱收到一封主题为 Echo 测试的邮件。
4. host 填 `127.0.0.1` / 内网地址时被守卫拦下，且不会建立任何连接。
5. 密码明文不出现在返回值、日志与错误文本中。
