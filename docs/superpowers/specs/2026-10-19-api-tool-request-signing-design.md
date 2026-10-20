# API 工具请求签名设计

- 日期：2026-10-19
- 状态：已评审，已实现（2026-10-19）
- 范围：平台 API 工具（`sys_api_tools`）的请求签名，不改变现有系统级参数与用户变量语义
- 关联能力：工具编辑页、表单/向导试跑、运行时 API 工具调用

## 1. 背景与威胁模型

### 1.1 要解决的问题

业务方需要确认：

1. 请求确实由平台发出，而不是外部攻击者伪造；
2. 请求方法、路径、Query、Body 在传输过程中没有被篡改。

这是一个**调用方身份 + 请求完整性**问题，不是参数业务合法性校验问题。

### 1.2 目标

- 每个 API 工具可以独立开启请求签名；
- 使用共享密钥的 `HMAC-SHA256`；
- 签名覆盖最终实际发出的方法、路径、Query、Body、时间戳、nonce 和密钥版本；
- 试跑与真实 AI 调用使用完全相同的签名逻辑；
- 业务方可以按公开规范独立验签；
- 密钥加密存储、只写不回显，支持轮换；
- 签名关闭时，现有工具行为完全不变。

### 1.3 非目标

- 不阻止模型或用户传入业务上不合理的参数。签名签的是平台最终发出的值，业务方仍须做金额、权限、状态等业务校验；
- 不替代 HTTPS。签名不加密请求内容；
- 第一版不做非对称签名和不可抵赖；
- 第一版不强制业务方实现 nonce 重放缓存；nonce 先纳入签名，为后续防重放预留；
- 不兼容 MD5 拼接格式。若未来出现只能接 MD5 的业务方，按独立 legacy 版本处理，不作为默认算法。

## 2. 方案选择

| 方案 | 结论 | 说明 |
|---|---|---|
| HMAC-SHA256 请求签名 | **采用** | 业务方持有共享密钥，验证平台来源和请求完整性；实现简单，跨语言成熟 |
| Ed25519 / RSA 非对称签名 | 暂不采用 | 适合业务方不可信或需要不可抵赖的场景；密钥分发、轮换和接入成本更高 |
| MD5 参数拼接 | 不采用 | 算法弱；直接拼接存在顺序、嵌套、编码和分隔歧义；不能作为默认安全方案 |

共享密钥意味着业务方自己也能生成合法签名，因此 HMAC 不提供对业务方的不可抵赖性。当前威胁模型是外部伪造和传输篡改，HMAC 足够。

## 3. 签名协议 v1

### 3.1 Header

签名信息放在 HTTP Header，不混入业务参数：

```http
X-Nanzi-Signature-Version: 1
X-Nanzi-Key-Id: 8f3c2a1b7d9e4c60
X-Nanzi-Timestamp: 1729334695
X-Nanzi-Nonce: 5f2c9a7e4b1d8c3a6e0f2b7d9a4c1e8f
X-Nanzi-Signature: 9cc4a7371ed4f5c9e62baffa7614f688c7fdf3a8e420b97c46ec56aea42168ad
```

约定：

- Header 名大小写不敏感，平台按上述形式发送；
- `Version` 固定为 `1`；
- `Timestamp` 是 Unix 秒，十进制、无前导零；
- `Nonce` 是 16 字节随机数的小写十六进制，32 个字符；
- `Key-Id` 是 8 字节随机数的小写十六进制，16 个字符；
- `Signature` 是 HMAC-SHA256 结果的小写十六进制，64 个字符；
- 业务网关必须放行 `X-Nanzi-*` Header；
- 平台保留 `X-Nanzi-*` 命名空间，工具的普通 Headers 不允许配置同前缀，避免覆盖签名。

签名元数据不是业务参数，因此不违反“系统级参数不放 Header”的既有约束。

### 3.2 密钥格式

- 工具编辑页点击“生成”时，前端使用 Web Crypto 生成 32 字节随机密钥，展示为 64 个十六进制字符；同时生成 16 个十六进制字符的 `key_id`；
- HMAC key 是**该密钥字符串的 UTF-8 字节**，不需要再做十六进制解码；
- 业务方粘贴密钥时，允许 32–128 个可打印 ASCII 字符；HMAC key 同样是字符串的 UTF-8 字节；
- 服务端校验密钥长度、字符集和 `key_id` 格式，再加密存储；服务端不回显密钥明文；
- 密钥只通过安全渠道交给业务方，不进入请求、URL、Query、Body 或日志；
- 每个工具独立密钥，不复用；
- 新建草稿的试跑和最终保存使用同一组密钥/Key-Id，避免“试跑签名和上线签名不一致”。

`salt` 不是密钥。这里的配置项统一叫“签名密钥 / Signing Secret”。

### 3.3 规范化请求串

签名串固定为 8 行，UTF-8 编码，行间使用 `\n`，**末尾没有换行**：

```text
{version}
{METHOD}
{PATH}
{CANONICAL_QUERY}
{SHA256_HEX_RAW_BODY}
{TIMESTAMP}
{NONCE}
{KEY_ID}
```

示例：

```text
1
POST
/api/orders
a=1&b=2
191f8fb213eb5569db7324218ab13737cd75320638510f914392ddba0ea1c164
1729334695
5f2c9a7e4b1d8c3a6e0f2b7d9a4c1e8f
8f3c2a1b7d9e4c60
```

逐项规则：

1. **version**：固定 `1`。
2. **METHOD**：大写，例如 `GET`、`POST`。
3. **PATH**：最终请求路径，包含前导 `/`，不含 Query；使用实际发送的百分号编码形式，不做解码后再编码。路径替换和系统级参数注入完成后才取值。
4. **CANONICAL_QUERY**：
   - 从最终 URL 的 Query 解析键值对，保留重复 key 和空值；
   - `+` 按空格解码；
   - 按解码后 key、value 的 UTF-8 字节序升序排序；
   - 使用 RFC3986 编码，空格编码为 `%20` 而不是 `+`，十六进制大写；
   - 用 `key=value` 表示，多个键值对用 `&` 连接；
   - 没有 Query 时为空行。
5. **SHA256_HEX_RAW_BODY**：
   - 对实际发送的 Body 原始字节做 SHA-256，输出小写十六进制；
   - 无 Body 时对空字节 `b""` 做哈希；
   - 空 Body 的固定值是：
     `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`；
   - 不签反序列化后重新序列化的 JSON，避免双方 JSON 格式差异；
   - 若未来启用 Content-Encoding，签实际传输的编码后字节，业务方在解压前验签。
6. **TIMESTAMP**：与 Header 完全一致。
7. **NONCE**：与 Header 完全一致。
8. **KEY_ID**：与 Header 完全一致。

### 3.4 签名计算

```text
signature = lowercase_hex(
    HMAC-SHA256(
        key = secret_utf8_bytes,
        message = canonical_request_utf8_bytes
    )
)
```

业务方必须使用常量时间比较，例如 Python 的 `hmac.compare_digest`。

### 3.5 固定测试向量

用于平台单测和业务方接入自测：

```text
secret   = 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
key_id   = 8f3c2a1b7d9e4c60
timestamp = 1729334695
nonce    = 5f2c9a7e4b1d8c3a6e0f2b7d9a4c1e8f
method   = POST
path     = /api/orders
query    = a=1&b=2
body     = {"amount":100,"applicant":"alice"}
```

Body 的 SHA-256：

```text
191f8fb213eb5569db7324218ab13737cd75320638510f914392ddba0ea1c164
```

签名串：

```text
1
POST
/api/orders
a=1&b=2
191f8fb213eb5569db7324218ab13737cd75320638510f914392ddba0ea1c164
1729334695
5f2c9a7e4b1d8c3a6e0f2b7d9a4c1e8f
8f3c2a1b7d9e4c60
```

期望签名：

```text
9cc4a7371ed4f5c9e62baffa7614f688c7fdf3a8e420b97c46ec56aea42168ad
```

### 3.6 时间戳与 nonce

- 业务方建议接受时间戳与服务器时间偏差不超过 ±300 秒；
- 拒绝明显来自未来的时间戳；
- 业务方可以按 `(key_id, nonce)` 缓存已使用 nonce 600 秒，拒绝重复；
- v1 平台每次都生成新的 timestamp 和 nonce，重试也不会复用；
- 如果业务方暂不实现 nonce 缓存，仍应完成签名和 timestamp 校验，但要明确知道窗口内存在重放风险。

### 3.7 业务方验签流程

1. 读取并校验五个 `X-Nanzi-*` Header 是否齐全；
2. 校验 `Version == 1`；
3. 用 `Key-Id` 查找对应密钥；未知 Key-Id 直接拒绝；
4. 校验 timestamp 在允许窗口内；
5. 可选：检查 `(key_id, nonce)` 是否已使用；
6. 在 JSON/Form 反序列化之前读取原始 Body 字节；
7. 读取原始 Method、Path 和 Query；
8. 按第 3.3 节构造签名串；
9. 计算 HMAC-SHA256 并做常量时间比较；
10. 任一步失败返回 401/403，响应中不要泄露密钥、期望签名或内部密钥信息。

## 4. 数据模型与 API

### 4.1 表结构

`sys_api_tools` 新增字段：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `signing_enabled` | Boolean | `false` | 是否开启请求签名 |
| `signing_key_id` | String(64) | NULL | 当前密钥版本标识 |
| `signing_secret_encrypted` | Text | NULL | Fernet 加密后的密钥 |
| `signing_algorithm` | String(32) | `hmac-sha256` | 第一版只允许该值 |

迁移文件：

- MySQL：`db-prod/V173-add-api-tool-request-signing.sql`
- PostgreSQL：`db-prod-pg/V74-add-api-tool-request-signing.sql`

只新增迁移，不修改已有迁移，不直接操作数据库。两套 SQL 的布尔类型、默认值和列注释按各自方言编写。

### 4.2 API Schema

`SysApiToolBase` 增加：

```text
signing_enabled: bool
signing_key_id: Optional[str]
signing_algorithm: str
```

`SysApiToolResponse` 额外增加派生字段：

```text
signing_secret_configured: bool
```

`signing_secret_configured` 只表示“是否已配置”，不返回密钥。

`SysApiToolCreate` 增加只写字段：

```text
signing_secret: Optional[str]
```

`SysApiToolUpdate` 增加：

```text
signing_enabled: Optional[bool]
signing_key_id: Optional[str]
signing_algorithm: Optional[str]
signing_secret: Optional[str]
```

规则：

- 新建时 `signing_enabled=true` 必须提供 `signing_key_id` 和 `signing_secret`；
- 更新时省略 `signing_secret` 和 `signing_key_id` 表示保留原密钥；
- 提供新的 `signing_secret` 必须同时提供新的 `signing_key_id`，表示轮换；
- 关闭签名只停用、不清空密钥；第一版不提供单独删除密钥的接口，安全需要时通过轮换更换；
- 响应、列表、详情、审计和日志都不返回密钥明文；
- 只允许 `signing_algorithm = "hmac-sha256"`，其他值在创建/更新/试跑时拒绝；
- `signing_key_id` 校验为 16 个十六进制字符，并且必须全局唯一；建议建唯一索引，冲突时拒绝保存而不是覆盖。

`SysApiToolPreviewRequest` 增加：

```text
signing_enabled: bool = False
signing_algorithm: str = "hmac-sha256"
signing_key_id: Optional[str] = None
signing_secret: Optional[str] = None
tool_id: Optional[str] = None
```

试跑取密钥的顺序：

1. 优先使用本次提交的 `signing_secret` / `signing_key_id`（新建草稿或用户主动轮换）；
2. 没有提交时，如果提供了 `tool_id`，从已保存工具读取并解密；
3. 两者都没有且 `signing_enabled=true`，返回配置错误，不发送未签名请求。

`tool_id` 必须做存在性和权限校验，不能通过它读取其他租户/用户的密钥。

### 4.3 密钥生命周期

- 生成：前端 `crypto.getRandomValues(new Uint8Array(32))` 生成密钥（64 个十六进制字符）和 8 字节 Key-Id（16 个十六进制字符）；Web Crypto 不可用时拒绝生成，禁止用 `Math.random()` 兜底；
- 展示：生成后立即展示在密码输入框中，用户自行复制给业务方；保存后只显示“已配置”；
- 存储：使用现有 `get_api_key_manager().encrypt_api_key()` 加密，建议加 `sig_` 前缀便于识别；
- 解密：只在签名时进行，不进入缓存日志；
- 轮换：前端生成新的密钥和 Key-Id，与业务方约定新旧 Key-Id 并存的过渡窗口；平台保存后立即使用新 Key-Id；
- 删除：关闭签名不自动删除；第一版不提供单独删除，安全需要时通过轮换更换；
- 失败：解密失败、密钥缺失、算法不支持时**失败关闭**，绝不降级为未签名请求。

服务端不提供“生成并返回密钥”的接口，避免密钥在额外接口中被回显；密钥只在编辑页生成，随创建/更新请求提交一次。后续如需服务端托管生成，应单独设计一次性密钥签发接口。

## 5. 后端实现

### 5.1 新增模块

新增 `app/services/ai/tools/request_signing.py`：

- `SigningConfig`：enabled、key_id、algorithm、secret；
- `SigningConfigError`；
- `parse_signing_config(tool)`：解析并校验数据库字段；
- `canonicalize_query(raw_query)`；
- `build_canonical_request(...)`；
- `sign_request(method, url, body_bytes, secret, key_id, timestamp=None, nonce=None)`；
- `SIGNATURE_HEADER_NAMES` / 保留前缀判断。

模块只负责纯函数和签名，不负责数据库查询、权限和 UI。

### 5.2 请求内核

在 `GenericApiToolFactory._perform_request` 内：

1. 完成用户变量替换、默认值补齐、系统级参数注入和路径替换；
2. 用 `httpx` 构造最终请求，而不是先发再补 Header；
3. 从构造出的请求中取最终 Method、Path、Query 和原始 Body 字节；
4. 如果签名开启：
   - 校验配置；
   - 生成 timestamp/nonce；
   - 计算签名；
   - 写入五个 `X-Nanzi-*` Header；
5. 将签名后的 Headers 写入 `capture`；
6. 发送请求；
7. 原有返回结构 `(response, elapsed_ms, final_url)` 保持不变。

推荐使用 `client.build_request(...)` + `client.send(request)`，确保签名的 Body 字节就是实际发送的字节。不能先 `json=` 让 httpx 序列化一次，再对另一份 `json.dumps()` 结果签名。

GET 请求的 Query 由构造后的最终 URL 取得；非 GET 的 JSON Body 由构造后的 `request.content` 取得。

### 5.3 校验与错误

创建、更新、试跑时都校验：

- 开启签名必须有 Key-Id 和可解密密钥；
- 密钥长度和字符集符合要求；
- 算法只允许 `hmac-sha256`；
- 工具普通 Headers 不能包含 `X-Nanzi-*` 前缀；
- 关闭签名时忽略密钥配置，不产生签名 Header。

试跑新增错误类型：

```text
invalid_signing_config
```

前端需要把它和 `invalid_parameter_schema`、`missing_user_variable` 区分展示。签名失败时不要发出未签名请求。

### 5.4 试跑快照

`SysApiToolPreviewRequestInfo.headers` 包含最终签名 Header；`mask_sensitive_data` 不会把 `X-Nanzi-Signature`、`X-Nanzi-Timestamp` 当成敏感字段，因此可以在抽屉中展示和复制。`X-Nanzi-Key-Id` 可以展示；密钥本身永远不在 Header 中。

## 6. 前端

### 6.1 工具编辑页

在参数区附近增加独立“请求签名”区块，建议新增 `ToolSigningEditor.vue`：

- 开关：`请求签名（HMAC-SHA256）`；
- 算法：只读 `HMAC-SHA256`；
- 签名密钥：
  - 未配置：密码输入框 + “生成”按钮；
  - 已配置：显示“已配置”，输入框留空表示不修改；
  - 生成后立即显示明文供复制，保存后不再回显；
- Key-Id：生成密钥时一并生成并展示，随保存提交，不手工编辑；
- 轮换：确认后生成新密钥和新 Key-Id，提示业务方同步更新；
- 说明文案：业务方必须使用同一密钥和同一规范化规则验签；
- 开启但未配置密钥时，保存和试跑按钮给出明确错误。

表单模式、编辑模式和向导第 4 步共用同一个组件与同一份表单状态。

### 6.1.1 开关旁的接入说明弹层

签名开关标题右侧增加一个 `?` 图标按钮，无论开关是否开启都显示；点击打开“请求签名接入说明”弹层。图标只负责帮助说明，不改变开关状态，也不触发表单提交。

交互与布局：

- 使用现有 `Modal.vue`，`size="max-w-3xl"`，小屏由 Modal 自动适配；
- 触发器：`aria-label="查看请求签名接入说明"`、`title="查看接入说明"`；
- 关闭：Modal 右上角关闭按钮或点击遮罩；
- 弹层内容只读，不包含真实密钥；
- 代码块、签名串和测试向量使用现有 `CopyButton`，方便业务方复制；
- 弹层内容按协议版本 v1 固化；协议升级时同步更新，并保留版本标题。

弹层内容结构：

1. **平台会发送什么**：五个 Header 的名称、含义和示例；
2. **业务方验签步骤**：
   - 校验版本、Key-Id、timestamp（建议 ±300 秒）；
   - 在反序列化前读取原始 Body；
   - 按签名串模板拼装；
   - HMAC-SHA256 + 常量时间比较；
   - 失败返回 401/403，不泄露密钥；
   - 可选：按 `(key_id, nonce)` 做重放缓存；
3. **签名串模板**：8 行文本，带复制按钮；
4. **Python 最小示例**：包含 canonical query、Body 哈希、HMAC 和 `compare_digest`；
5. **固定测试向量**：使用演示密钥，展示期望签名，带复制按钮；
6. **注意事项**：
   - 必须在 JSON/Form 解析前拿到原始 Body；
   - 网关必须放行 `X-Nanzi-*`；
   - 密钥只通过安全渠道传递，不能写入前端公开配置或日志；
   - timestamp 窗口内仍可能重放，生产环境建议启用 nonce 缓存。

Python 示例示意：

```python
import hashlib
import hmac
from urllib.parse import parse_qsl, quote


def canonical_query(raw_query: str) -> str:
    pairs = parse_qsl(raw_query, keep_blank_values=True)
    pairs.sort(key=lambda pair: (pair[0].encode("utf-8"), pair[1].encode("utf-8")))
    return "&".join(
        f"{quote(key, safe='-._~')}={quote(value, safe='-._~')}"
        for key, value in pairs
    )


def verify_signature(
    method: str,
    raw_path: str,
    raw_query: str,
    raw_body: bytes,
    headers: dict[str, str],
    secret: str,
) -> bool:
    canonical = "\n".join([
        "1",
        method.upper(),
        raw_path,
        canonical_query(raw_query),
        hashlib.sha256(raw_body).hexdigest(),
        headers["X-Nanzi-Timestamp"],
        headers["X-Nanzi-Nonce"],
        headers["X-Nanzi-Key-Id"],
    ])
    expected = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, headers["X-Nanzi-Signature"])
```

说明：示例中的 `raw_path` 必须是实际发送的编码路径，`raw_query` 必须是未解码的原始 Query；不同框架的 Header 名可能被转为小写，读取时按大小写不敏感处理。如果业务框架只能拿到解析后的参数，需要按第 3.3 节重新构造，不能直接用框架的字典序列化结果。

弹层下方放一个“关闭”按钮；不需要“启用签名”之类的操作按钮，避免用户误以为在弹层里保存配置。

### 6.2 试跑

`ApiToolTester` 增加签名相关参数：

- `signingEnabled`
- `signingAlgorithm`
- `signingSecret`（仅新建/轮换时由表单持有）
- `toolId`（已保存工具用于后端取已存密钥）

打开试跑时：

- 若签名开启，抽屉顶部显示“请求签名已开启”；
- “实际发出的请求”中显示五个 `X-Nanzi-*` Header；
- 签名 Header 支持现有复制浮标；
- 不显示密钥；
- 新建草稿必须能使用当前输入的密钥完成试跑；
- 已保存工具不需要前端持有密钥，由后端按 `tool_id` 解密。

### 6.3 类型与状态

更新：

- `frontend/src/api/tool.ts` 的创建/更新/响应/试跑类型；
- `ToolRegistry.vue`、`ToolWizard.vue`、`ToolParamEditor.vue` 的表单字段和保存载荷；
- 表单校验函数：开启签名时要求密钥已配置或本次输入了密钥；
- 试跑请求载荷：携带签名配置和 `tool_id`。

## 7. 测试计划

### 7.1 后端单测

新增 `tests/ai/tools/test_request_signing.py`：

- 固定测试向量得到期望签名；
- GET 无 Body、空 Query；
- POST JSON Body 的原始字节哈希；
- Query 排序、重复 key、空值、空格、中文和特殊字符；
- Path 含编码字符；
- timestamp/nonce 注入；
- 错误密钥、篡改 body/query/path 后验签失败；
- 常量时间比较由业务方示例覆盖。

### 7.2 API 测试

新增 `tests/api/portal/test_tool_request_signing.py`：

- 开启签名但无密钥，创建/更新/试跑被拒绝；
- 合法配置创建成功，响应不含密钥明文，`signing_secret_configured=true`；
- 更新不传密钥保留原密钥；传新密钥轮换 Key-Id；
- 试跑实际请求包含五个 Header，签名可用测试向量验证；
- 已保存工具试跑通过 `tool_id` 使用存储密钥；
- 篡改/错误密钥/过期时间戳由 mock 业务端拒绝；
- 签名配置错误时不会发出未签名请求；
- 普通 Header 使用 `X-Nanzi-*` 被拒绝；
- 关闭签名时无签名 Header，现有行为不变。

### 7.3 前端契约测试

新增 `tests/frontend/test_api_tool_signing_contract.py`：

- 编辑页包含签名开关、密钥、Key-Id、轮换和说明；
- 开启但未配置密钥时保存/试跑被阻止；
- 试跑请求携带签名配置和 `tool_id`；
- 试跑抽屉展示签名 Header 且不展示密钥；
- 签名开关旁有 `?` 帮助按钮，弹层包含 Header、验签步骤、签名串、Python 示例和测试向量；
- 弹层不出现真实密钥，代码/测试向量复制按钮可用；
- API 类型不包含密钥明文字段。

### 7.4 联调

- 用一个本地 mock 业务端按本文规范验签；
- 覆盖成功、错误密钥、篡改 path/query/body、时间戳过期、未知 Key-Id；
- 用同一组固定测试向量验证 Python 与业务方语言的实现一致；
- 业务方接入文档包含 Header、签名串、测试向量和最小验签代码。

## 8. 迁移与上线

1. 提交 MySQL/PostgreSQL 两套迁移；
2. 后端新增字段、加密存储、签名模块和试跑支持；
3. 前端增加签名配置和试跑展示；
4. 默认关闭，现有工具零影响；
5. 发布验签规范、测试向量和示例代码给业务方；
6. 选一个测试工具开启，联调成功后再逐步推广；
7. 监控业务方 401/403，区分签名失败与业务失败。

不在本次变更中修改本地或线上数据库；迁移由用户按现有发布流程执行。

## 9. 安全边界与风险

### 9.1 能防住

- 没有共享密钥的外部攻击者无法伪造合法签名；
- 修改 method、path、query、body 会导致验签失败；
- 不同工具密钥隔离，单个工具泄露不影响其他工具；
- 关闭开关的工具完全不受影响。

### 9.2 防不住

- 模型/用户传错业务参数：签名签的是平台最终发出的值，业务方必须做业务校验；
- 业务方自身伪造：HMAC 共享密钥不提供不可抵赖；
- 密钥泄露：需要加密存储、最小权限、轮换和审计；
- 重放：需要业务方实现 nonce 缓存；
- 内容泄露：签名不加密，仍必须 HTTPS。

### 9.3 接入风险

- **原始 Body**：业务方必须在反序列化前取得原始字节；如果网关会重新序列化 JSON，Body 哈希会对不上，需要改造成规范化 JSON Body 签名或让网关保留原始 Body；
- **原始路径/Query**：网关如果重写路径或 Query，可能导致验签失败；Query 已做规范化排序，路径仍按实际发送值签名；
- **Header 被剥离**：业务网关必须允许 `X-Nanzi-*`；
- **时钟偏差**：双方需要 NTP；建议 ±300 秒容差；
- **密钥轮换**：业务方需要支持新旧 Key-Id 并存，否则轮换会造成短暂中断。

## 10. 验收标准

- [ ] 关闭签名时，工具请求与现在完全一致；
- [ ] 开启签名后，真实调用和试跑都带五个 `X-Nanzi-*` Header；
- [ ] 固定测试向量在平台实现和业务方示例中结果一致；
- [ ] 修改 method/path/query/body 任一项后验签失败；
- [ ] 错误密钥、未知 Key-Id、过期 timestamp 被拒绝；
- [ ] 密钥不出现在 API 响应、试跑响应、日志和审计明文中；
- [ ] 签名配置缺失或解密失败时失败关闭，绝不发送未签名请求；
- [ ] 已保存工具试跑可以通过 `tool_id` 使用已存密钥；
- [ ] 新建草稿可以用本次输入的密钥试跑；
- [ ] 签名开关旁的 `?` 能打开接入说明弹层，内容与 v1 协议一致且不包含真实密钥；
- [ ] 前端/后端/契约测试通过，`vue-tsc --noEmit` 通过。

## 11. 后续可扩展

- 业务方强制 nonce 重放缓存；
- 多 Key-Id 并行过渡和自动轮换；
- 非对称签名（Ed25519）用于不可抵赖场景；
- 可配置签名 Header 前缀，适配业务网关命名要求；
- 签名失败的可观测性面板和告警；
- legacy MD5 适配器，仅在确有业务方无法升级时启用。
