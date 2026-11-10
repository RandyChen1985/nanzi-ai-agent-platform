# 凭据中心技术设计

> 面向三类读者：要**用它**的业务开发（怎么把凭据接到自己那条链路上）、要**管它**的运维与管理员（配什么、查什么、怎么排查）、要**改它**的后端开发（为什么这么设计、哪一层在拦）。
>
> 代码位置：`app/services/secrets/`（后端）、`frontend/src/views/SecretCenter.vue` 与 `AdminSecretCenter.vue`（前端）。上线步骤见 [`docs/credential-center-verification.md`](../../docs/credential-center-verification.md)。

---

## 一、解决什么问题

### 1.1 之前的状态

外部服务的密钥散落在三处，每一处都有自己的毛病：

| 位置 | 谁维护 | 问题 |
| :--- | :--- | :--- |
| MCP 服务的「固定 Token」 | 管理员 | 全平台一个值，任何人改服务配置都能看到；无法按人区分 |
| API 工具的请求头 | 管理员 | 同上，且值以明文躺在配置表里 |
| 用户自己在提示词里写 | 每个用户 | 明文进对话记录、进模型上下文、进审计表 |

真正的矛盾不在「存哪儿」，而在**「模型不该知道，但请求又必须带上」**。

只要值经过模型，就一定会在某个环节被回显：模型复述一遍、工具把收到的参数扔进日志、远端服务在报错信息里带上它。这些都不是我们能控制的代码。

### 1.2 三条设计原则

**① 模型看不见，就不可能编。**

凭据参数不从模型的工具定义里暴露（schema 剔除），模型根本没机会填。平台在**请求发出的那一刻**补进去。

这一条决定了后面所有设计：因为值从不进模型上下文，所以「模型猜到值」和「模型泄漏值」这两个风险同时消失。

**② 零授权是默认，不是例外。**

新建的凭据默认谁都用不了。要生效必须显式做两件事：**绑定**（声明它给哪个槽位用）+ **授权**（系统级凭据还要声明给谁能用）。

两件事分开，是因为它们回答不同问题：绑定回答「用在哪」，授权回答「谁能用」。

**③ 拿不到就说清楚，不猜。**

取不到值时不回退到「用配置里的旧值」或「留空发出去」。前者会掩盖故障，后者会换来远端一个 `401`——用户看到的是对方服务的错误，根本想不到是自己这条凭据过期了。

---

## 二、核心概念

### 2.1 所有权（scope）

| scope | `owner_id` | 谁创建 | 适用 |
| :--- | :--- | :--- | :--- |
| `user` | 用户的 `user_id` | 用户自己 | 个人账号的 Token、个人数据库密码 |
| `system` | 空串 `''` | 管理员 | 平台级服务（共用 MCP 服务、公共 API） |

`owner_id` 用空串而不是 `NULL`：`uk_secret_owner_key(owner_scope, owner_id, secret_key)` 是唯一索引，而 SQL 里 `NULL != NULL`，唯一索引对 NULL 行不生效。用空串才能真正约束住「同一个系统级 key 不能建两条」。

> 设计里预留了 `dept` 所有权，V1 不开放。部门级的共享用「系统级凭据 + 部门授权」表达，不需要第四种 scope。

### 2.2 类型（secret_type）

| 类型 | 能否读明文 | 行为 |
| :--- | :--- | :--- |
| `credential` | **永远不能**（含创建者与管理员） | 只能配置、绑定、轮换；界面上显示 `••••••••••••` |
| `variable` | 能，且可复制 | 租户号、接口地址这类必须能拿去别处用的值；每次读取记一条流水 |

这个区分不是权限级别，是**能力有无**。凭据类没有「查看明文」这个接口——不是管理员没权限调，是这个端点对 `credential` 一律拒绝。

```python
# app/services/secrets/center.py: read_variable_value
if row.secret_type != TYPE_VARIABLE:
    raise SecretValidationError("凭证类凭据不可读取；请在配置中引用，由平台注入")
```

### 2.3 场景（scene）

一条凭据声明「允许在哪些场景下被使用」，不勾就不生效。

| 常量 | 值 | 含义 | 状态 |
| :--- | :--- | :--- | :--- |
| `SCENE_MCP` | `mcp` | MCP 服务的请求头 + 工具参数 | ✅ 已接线 |
| `SCENE_API_TOOL` | `api_tool` | 通用 API 工具的头 / 查询 / 请求体 / 路径 | ✅ 已接线 |
| `SCENE_SCRIPT_ENV` | `script_env` | 脚本工具的环境变量 | ✅ 已接线（默认双层关闭） |
| `SCENE_SCHEDULED_TASK` | `scheduled_task` | — | ⚠️ **历史遗留**，不参与任何判定 |
| `SCENE_AGENT` | `agent` | 智能体 | ⚠️ **空壳**，只有常量和槽位白名单，无注入代码 |

`scheduled_task` 曾经用来给「无人值守调用」单独加一道闸门，后来发现定时任务和其他调用走同一套判定就够了，这个闸门反而制造了「配置了却不生效」的困惑。常量为兼容已有数据保留，**不要在新代码里用它做条件**。

另外 `variable_read` 只出现在使用流水里（标记「有人读了变量类明文」），它不是闸门场景，不受 `allowed_scenarios` 约束。

### 2.4 槽位（slot）

槽位回答「这个值落在请求的哪个位置」。格式是 `<前缀>:<名字>`：

| 前缀 | 含义 | 允许的消费者 |
| :--- | :--- | :--- |
| `header:` | HTTP 请求头 | `mcp_server` / `api_tool` / `agent` |
| `path:` | URL 里的 `{param}` 占位 | `api_tool` |
| `query:` | 查询串参数 | `api_tool` / `agent` |
| `body:` | JSON 请求体字段 | `api_tool` / `agent` |
| `arg:` | MCP 工具方法的入参 | `mcp_server` |
| `env:` | 脚本环境变量 | `script_tool` |

白名单在 `center.ALLOWED_SLOT_PREFIXES`，`bind_secret` 会拒绝跨消费者的组合（比如给脚本工具绑 `header:`）。

**API 工具的槽位不需要人算。** 前端只传参数名，后端按 URL 和方法推算：

```python
def resolve_api_tool_slot(*, name, method, url_template) -> str:
    if url_template and f"{{{name}}}" in url_template:
        return f"path:{name}"          # ← 必须先看 URL 占位
    if (method or "GET").upper() == "GET":
        return f"query:{name}"
    return f"body:{name}"
```

顺序不能反。`POST /refund/{applicant}` 里的 `applicant` 是路径参数，只按 method 判会被绑到 `body:` 上——记录写进去了、界面显示「已绑定」，运行时永远匹配不到。

### 2.5 绑定（binding）与授权（grant）

两个独立的概念，经常被混为一谈：

```
绑定：这个凭据  →  用在哪个消费者的哪个槽位
授权：这个凭据  →  哪些用户可以使用
```

- **个人凭据**：自己的东西，不需要授权。绑定之后只对自己生效。
- **系统级凭据**：管理员建的。必须授权（用户 / 部门 / 全体）才能被看见和使用。

绑定又分两种主体：

| `bound_by_user_id` | 含义 | 谁能建 |
| :--- | :--- | :--- |
| `''`（空串） | **全局绑定**，对所有调用者生效 | 管理员，且必须是系统级凭据 |
| `非空` | **个人绑定**，只对这个人生效 | 任何人（系统级需先被授权） |

同一槽位同时存在两种绑定时，**个人覆盖全局**（`resolver._pick_binding` 的 `order_by` 保证）。否则用户改了自己的绑定等于没改。

---

## 三、数据模型

六张表，全部在 `db-prod/V209-add-credential-center.sql` / `db-prod-pg/V109-add-credential-center.sql`。

### 3.1 `sys_secrets` — 凭据本体

| 字段 | 说明 |
| :--- | :--- |
| `id` | UUID |
| `owner_scope` / `owner_id` | 见 2.1 |
| `secret_key` | 引用标识，小写字母数字点下划线连字符；`(owner_scope, owner_id, secret_key)` 唯一 |
| `name` | 显示名 |
| `secret_type` | `credential` / `variable` |
| `value_encrypted` | Fernet 密文（`ENCRYPTION_KEY`） |
| `value_fingerprint` | SHA-256 前 16 位，供落库扫描比对 |
| `value_preview` | **仅 `variable`** 存前 8 字符；`credential` 必须为 `NULL` |
| `allowed_hosts` | 域名白名单 JSON 数组 |
| `allowed_scenarios` | 场景 JSON 数组 |
| `status` | `active` / `disabled` |
| `expires_at` | 过期时间（naive UTC） |
| `previous_value_encrypted` / `previous_expires_at` | 轮换双写窗口 |
| `category` / `remark` | 分类与备注（纯展示） |
| `last_rotated_at` | 最近轮换时间 |

### 3.2 `sys_secret_bindings` — 白名单

```sql
UNIQUE KEY uk_binding_slot (consumer_type, consumer_id, slot, bound_by_user_id)
```

这四列唯一，意味着「同一槽位只能有一条全局绑定 + 每人各一条个人绑定」。`bound_by_user_id` 用 `NOT NULL DEFAULT ''` 而不是可空，原因同 2.1：唯一索引对 NULL 不生效。

> 迁移前建的历史数据里可能是 `NULL`。`resolver._pick_binding` 与 `unbind_slot` 都同时认 `NULL` 和 `''`，代码读的是 `row.bound_by_user_id or ""`。

### 3.3 `sys_secret_grants` — 授权范围

| `grantee_type` | `grantee_id` | 含义 |
| :--- | :--- | :--- |
| `user` | `user_id` | 指定用户 |
| `dept` | `dept_code` | 整个部门（**实时查用户当前部门**，调岗自动跟随） |
| `all` | 空串 | 全体用户 |

`all` 这一档是后补的。没有它，平台级共享凭据只能逐部门授权，新增一个部门就必然漏掉一批人。它的 `grantee_id` 固定空串，不指向任何实体，所以 `_has_grant` 里对它**不比对** `grantee_id`。

`expires_at` 可空，到期后授权自动失效（这条比较用 `datetime.utcnow()`，与库里口径一致）。

### 3.4 `sys_secret_usage_logs` — 使用流水

每次解析（**包括被拒绝的**）写一条。刻意不设 `secret_id` 外键：删凭据不能连带删记录，否则「这个 token 曾经被谁用过」永久丢失。`secret_key_snapshot` 让记录在凭据消失后仍可读。

`result` 字段存 `success` 或 7 种拒绝原因之一。

### 3.5 `sys_secret_audit_logs` — 变更审计

与使用流水分开：一个记「谁用了」，一个记「谁改了」。合成一张表会让两类查询互相拖累——排查「这条 token 谁轮换过」时得在几万条调用记录里翻。

`action` 取值：`create` / `update` / `rotate` / `delete` / `disable` / `enable` / `grant` / `revoke` / `bind` / `unbind`。`actor_name` 存操作者当时的显示名，避免用户改名后审计失去可读性。

审计用**独立 session 和事务**（`center._audit`）。代价是极端情况下「操作成功、审计没写」，收益是审计表还不存在的那天，建凭据不会被它拖垮。

### 3.6 `sys_secret_user_prefs` — 用户偏好

只有一列有效值 `allow_script_env`。它只能**收紧**，不能放开：平台策略关着的时候，用户打开也没用。

---

## 四、判定流程

### 4.1 三层闸门取交集

```
① 平台策略（系统参数，管理员配）
   ├─ secret_policy_allow_external_host   外部域名注入总开关
   ├─ secret_policy_allow_script_env      脚本环境变量注入总开关（默认关）
   └─ secret_policy_max_inject_per_call   单次最多注入几条（默认 3，范围 1..50）

② 用户偏好（每个人自己的）
   └─ allow_script_env                    只影响 script_env，且只能收紧

③ 凭据自身的约束（每条凭据配）
   ├─ status                              停用直接拒
   ├─ expires_at / previous_expires_at     过期直接拒（双写窗口内放行）
   ├─ allowed_scenarios                   场景必须在里面
   └─ allowed_hosts                       目标域名必须在里面
```

再加一道**授权**：系统级凭据必须 grant 给这个人（直接 / 部门 / 全体）。

任何一层不通过就拒绝。**没有「例外放行」的路径。**

### 4.2 判定顺序（`policy.evaluate_secret`）

顺序是固定的，因为拒绝原因会落进流水，顺序变了统计口径就变了：

| # | 检查 | 不通过的结果 |
| :--- | :--- | :--- |
| 1 | `status == active` | `disabled` |
| 2 | 有效期（含双写窗口） | `expired` |
| 3 | `scene ∈ allowed_scenarios` | `denied_by_scenario` |
| 4 | **若 scene 是 `script_env`，到此直接返回** | `denied_by_script_policy` |
| 5 | `allow_external_host` 为假且目标是外部域名 | `denied_by_host` |
| 6 | `host_allowed(target, allowed_hosts)` | `denied_by_host` |

第 4 步值得单独说：脚本是唯一「值会离开平台」的场景——脚本能读 `os.environ` 就一定能 `print`，而 stdout 会进工具结果。所以它跳过域名检查，改用开关整类管控。

### 4.3 域名白名单规则（`policy.host_allowed`）

| `allowed_hosts` | 目标有域名 | 目标无域名 |
| :--- | :--- | :--- |
| 空数组 | 只允许内部域名（`localhost` / `127.0.0.1` / `*.local` / `*.internal` / `*.svc` / `*.cluster.local`） | ✅ 允许 |
| `["*"]` | ✅ 允许 | ❌ **拒绝** |
| `["api.example.com"]` | 精确匹配 | ❌ 拒绝 |
| `["*.example.com"]` | 匹配子域，**不**匹配 `example.com` 本身 | ❌ 拒绝 |

**`["*"]` 在目标域名为空时也拒绝**，这一条是刻意的：

```python
if not target:
    # 平台内部调用：只要声明了外部白名单，就不该在无目标域名的场景下注入——
    # 否则「先注入、再拼 URL」的绕过路径就成立了。
    return False
```

「先注入再拼 URL」指：如果允许无目标域名时注入，一个能控制 URL 的工具就可以先拿到值，再把值拼到任意地址上发出去，白名单形同虚设。

> 这条规则曾经导致一个真实故障：MCP 参数级注入没有把目标域名传下来，用户把白名单设成 `*` 仍然报 `denied_by_host`，而凭据页面上看不出任何问题。修法是**补传域名**（`injection._mcp_server_host` 按 `server_id` 回查 `sse_url`），不是放宽判定。

### 4.4 解析主流程（`resolver.SecretResolver.resolve`）

```
1. 取绑定          _pick_binding()   个人优先于全局；查不到 → denied_by_binding
2. 取凭据行        session.get()     行不存在 → denied_by_binding
3. 系统级授权      _has_grant()      未授权 → denied_by_grant（写流水）
4. 读平台策略 + 用户偏好
5. 判定            evaluate_secret()
6. 选密文          _pick_ciphertext() 新值过期才回退旧值
7. 解密            → SensitiveValue
8. 写流水          success 或拒绝原因
```

**fail-closed**：整段包在 `try/except` 里，任何异常（表不存在、库抖动）一律返回 `ok=False, denial=denied_by_binding`。绝不因为「查不到」就放行——那等于把白名单变成摆设。

代价是注释里写明的：迁移没执行时，所有注入静默失效。这是有意的取舍——静默失效比静默放行安全得多。

### 4.5 轮换的双写窗口

`rotate_secret` 把当前值挪到 `previous_value_encrypted`，设 `previous_expires_at = now + overlap_days`（默认 7 天），再写新值。

解析时 `_pick_ciphertext` 的规则：

```
新值未过期           → 用新值
新值过期但旧值未过期  → 用旧值（服务端还没换，不至于中断）
两者都过期           → 用新值（会走到 expired 判定）
```

这解决了一个真实的运维难题：改密码时，服务端和你这边的生效时刻不可能完全同步。没有双写窗口，总有那么几分钟所有调用都在失败。

---

## 五、注入接线

`resolver` 只负责「判定 + 取值」，`injection` 负责「放在哪个字段上」。四条链路的行为**刻意不同**：

| 链路 | 函数 | 触发时机 | 取值优先级 | 取不到时 |
| :--- | :--- | :--- | :--- | :--- |
| API 工具 · Header | `apply_api_tool_bindings` | 请求头组装完 | **只补空位**，手填值不动 | 静默跳过，除非是 `${CREDENTIAL:...}` 占位符 |
| API 工具 · 参数 | `apply_api_tool_param_bindings` | **先于**路径替换 | 无条件覆盖 | 抛 `UnresolvedCredentialError` |
| MCP · Header | `apply_mcp_bindings` | 建 SSE 会话前 | **绑定优先，无条件覆盖** | 记录到 `denials`，继续 |
| MCP · 参数 | `apply_mcp_arg_bindings` | `tools/call` 前 | 无条件覆盖 | 记录到 `denials`，**调用方报错** |
| 脚本 · env | `apply_script_env_bindings` | 写环境变量文件前 | 无条件覆盖 | 记录到 `denials`，脚本照跑但变量为空 |

### 5.1 为什么 API Header 是「只补空位」而 MCP Header 是「覆盖」

这两个看起来矛盾，其实是不同语义。

**API 工具**的 Header 配置是管理员在工具上直接填的，用户能看见也能改。手填了真实值说明他要的就是那个值——平台再覆盖，等于用户改了没用。

**MCP 服务**的 `fixed_token` 和「其他 Header」只是「没人绑时的默认值」。用户既然显式绑定了，绑定就必须盖过它。旧实现用的是「只补空位」，结果遇到「值非空 + 名字不含 token/key/auth」的 Header 直接跳过，**连库都不查**——使用记录里一条都没有，用户只看到「绑了但没生效」。更糟的是前端绑定后把值输入框隐藏了，他没法自己清空旧值，等于死锁。

所以 MCP 那条链路的遍历对象是**绑定槽位**而不是 headers：绑定表才是「谁要用凭据」的唯一事实来源，配置里有没有这个 Header 与此无关（没有就补上）。

### 5.2 为什么 MCP 参数「取不到就报错」

Header 缺失是常态——绝大多数服务本来就不需要鉴权头。参数不一样：**工具已经声明了「这个参数的值由凭据提供」**，取不到就是缺了一个必需参数。带着空值发出去，远端只会回 `401`，用户看不懂缺的是哪个绑定。

拒绝原因也会带回去：

```
MCP 工具 xxx:query 的凭据参数缺失：arg:query:api_key（目标域名不在白名单里）。
请编辑这条凭据的「域名白名单」放行目标域名（填 * 表示不限）
```

注意最后那句是**按原因给的动作**，不是通用建议。「没绑定」和「域名不对」需要的操作在不同的页面上，用同一句提示等于没说。

### 5.3 MCP 参数级的 schema 剔除

```python
# mcp_factory.create_tool
_credential_args = credential_param_names(tool_record)
for _name in _credential_args:
    properties.pop(_name, None)
    required_fields.discard(_name)
```

声明过的参数从**模型可见**的 schema 里删掉。这一步是整个方案的地基：

因为模型看不见这个参数，它就不会去编值；平台随后注入也就没有「骗模型」的问题（模型确实没被要求填）。

声明接口 `PUT /api/portal/mcp/tools/{tool_id}/credential-params` 会校验：

- 参数名必须在 schema 的 `properties` 里（拼错一个就等于声明了一个永远填不上的参数）
- 类型必须是 `string`（凭据值永远是字符串；声明成整数/布尔/对象注入进去会破坏类型）

### 5.4 脚本环境变量的三道约束

```python
async def materialize_secret_env(self, stem, run_id) -> Optional[Path]:
```

1. **值不进命令行**。`NAME=value cmd` 在 `ps aux` 和容器进程列表里都看得见。改成写 0600 文件，命令行里只出现路径。
2. **单引号渲染**。shell 的 `. file` 会执行命令替换——凭据值里只要出现 `$(...)` 或反引号，就等于让提交凭据的人在别人的脚本里执行任意命令。

   ```python
   def shell_quote(value: str) -> str:
       return "'" + str(value).replace("'", "'\\''") + "'"
   ```
3. **执行完立即删除**。留在工作区等于给脚本（和任何能读工作区的工具）一个长期可读的凭据副本。

### 5.5 单次注入上限

`secret_policy_max_inject_per_call` 默认 3，夹在 1..50。

```
0  → 所有注入静默失效
无上限 → 等于关掉这道闸门
```

两种极端都不该由一次手滑决定，所以 `_as_limit` 把它夹住，非法值退回默认。

这个配置项曾经只写在系统参数页上、**没有任何代码读它**。界面写着「防止一次请求把全部凭据一起带走」，实际一条不限——配置项不生效比没有更危险，它给出的是虚假的安全感。

---

## 六、防泄漏体系

四层，各管一段，互相独立：

### 第一层：值不进模型（结构性）

凭据参数的 schema 剔除 + 值只在出站请求的局部变量里出现。这是唯一的**主防线**，后面三层都是它失效时的兜底。

### 第二层：`SensitiveValue` 让误用失败

```python
token = SensitiveValue("s3cr3t")
logger.info(f"token={token}")     # 抛 SecretLeakAttempt
json.dumps({"t": token})          # 抛 SecretLeakAttempt
print(token)                      # 抛 SecretLeakAttempt
http.post(url, headers={"A": token.reveal()})   # 唯一正确用法
```

刻意**不实现** `__eq__` / `__hash__`：一旦可以比较，就会有人在断言里写 `assert token == "s3cr3t"`，等于把明文漏回测试日志。

`resolve()` 的返回值里，明文只能通过 `.reveal()` 取出；`__repr__` 是 `SensitiveValue('***')`，即使被意外插进异常堆栈也不会带出值。

### 第三层：运行期回显脱敏（`redact.py`）

注入层在 `reveal()` 之后调 `register_injected(plain)`，把值登记到 `ContextVar`。工具结果回灌模型之前扫一遍，命中就掩码。

```python
_mask(value)  # 前 4 位 + *** + 后 4 位；长度 ≤ 12 时整段变 ***
```

参数：

| 常量 | 值 | 理由 |
| :--- | :--- | :--- |
| `MIN_LENGTH` | 8 | 太短的值（`"1"`、`"true"`）会误伤整个结果 |
| `MAX_VALUES` | 20 | 只扫本次注入的值，不做「扫全部凭据」——那要把所有凭据解密进内存 |

超过 20 条时**淘汰最旧**，而不是拒绝新的。旧值对应的调用早已结束，新值才是当前在用的。

覆盖范围：MCP 6 个返回点（`mcp_client` 的各分支 return）、API 工具 2 个、脚本 `render_result_for_model`（成功/失败/超时/JSON 分支全覆盖）。

### 第四层：落库前擦除（`scanner.py` + `registry.py` + `guard.py`）

这一层和第三层解决**不同的问题**，容易混淆：

| | 第三层 `redact` | 第四层 `scanner` |
| :--- | :--- | :--- |
| 时机 | 工具结果回灌模型前 | 写库前 |
| 范围 | **本次注入过**的值 | **平台已知的全部**凭据值 |
| 替换成 | `sk-a***cdef` | `${secret:config.mysql_ops_password}` |
| 防什么 | 模型上下文泄漏 | 审计表 / 执行历史泄漏 |

`scanner` 补的是键名规则失明的那一类：

```
{"error": "connect failed: Access denied for user 'ops' using 'P@ssw0rd!2024'"}
```

这里的密码既不在敏感键名下，也不带平台前缀，`app.utils.masking` 完全看不见。`SecretScanner` 拿平台已知的值做精确子串匹配。

替换成引用而不是 `******`，是为了让审计仍然有意义——运维看到 `${secret:config.mysql_ops_password}` 就知道「这里原本是一个数据库密码」，而不是看到一串星号去猜。

`registry` 收集已知值（`system_configs.is_secret=true` + `sys_mcp_servers.auth_headers`），60 秒进程内缓存，收集失败时沿用上一份快照。`guard` 是三个出口（trace / history / Redis 快照）的统一入口。

---

## 七、使用指南

### 7.1 个人用户

入口：**个人中心 → 我的凭据**（`SecretCenter.vue`）。

**建一条凭据**

1. 点「新建」，填名称、选择类型
2. `credential`（Token / 密码）：值建完就看不到，只能轮换——确认无误再保存
3. `variable`（租户号 / 接口地址）：可以随时点「查看明文」复制
4. 勾选**允许的使用场景**（不勾这条凭据永远不会生效）
5. 填**域名白名单**：要调外部服务必须填，`*` 表示不限

**用到自己的工具上**

在工具的配置界面点「绑定凭据」，选一条自己的凭据。绑定后：

- 个人 MCP / API 工具 → 只对自己生效
- 平台 MCP / 系统工具 → 需要管理员先授权给你，才能选到那条系统级凭据

**平台 MCP 想用自己的凭据**

「我的 MCP → 平台服务」→ 选服务 → 绑定凭据。这里绑的是**你个人**的凭据，只对你生效，不影响别人。

### 7.2 管理员

入口：**系统管理 → 凭据中心**（`AdminSecretCenter.vue`，权限 `menu:system:credentials`）。

三个 tab：**全部凭据 / 变更记录 / 使用记录**。

**建一条系统级凭据并发放**

1. 「新建」→ 类型选 `credential`
2. 勾场景、填域名白名单（`*` 表示不限）
3. 卡片上点「授权」→ 选用户 / 部门 / 全体用户
4. 用户现在能在选择器里看到它了

**给平台服务配一次，全平台生效**

在「MCP 服务注册」或「系统 API 工具」里绑凭据时，管理端会自动按**全局绑定**落库（`bound_by_user_id=''`）。

> 这是一个曾经的 P0 缺陷：那个路径从来没有被走通过——`bind_secret` 只在 `bound_by_user_id is None` 时走全局分支，而唯一的调用点永远传 `user_id`。结果管理员配的平台公共凭据全部落成他自己的私有绑定，普通用户调用全部「未绑定」，而管理员自己测一切正常。
>
> 现在前端 4 个管理端组件共 **14 处**写入都显式传 `is_global`，请求模型默认 `false`（漏传不会悄悄配出全局），端点要求管理员权限。`McpServerRegistry` 因为同时服务「平台 MCP」和「我的 MCP」两个入口，改为按 `scope` 决定。

**排查「配了但用不了」**

看「使用记录」→ 按结果筛选。`denied_by_*` 的条数本身就是信号：

| 结果 | 含义 | 下一步 |
| :--- | :--- | :--- |
| `success` | 正常 | — |
| `denied_by_binding` | 这个槽位没有绑定 | 去对应工具上绑 |
| `denied_by_grant` | 系统级凭据没授权给这个人 | 授权给他 / 他的部门 / 全体 |
| `denied_by_scenario` | 场景没勾 | 编辑凭据，勾上对应场景 |
| `denied_by_host` | 目标域名不在白名单 | 编辑白名单，或填 `*` |
| `denied_by_script_policy` | 脚本注入开关没开 | 平台策略 + 用户偏好两层都要开 |
| `disabled` | 凭据被停用 | 重新启用 |
| `expired` | 过期了 | 轮换或新建 |

上层报错信息里也会带这些翻译，见 `injection.DENIAL_REASONS` 与 `DENIAL_HINTS`。

### 7.3 MCP 工具参数级（进阶）

场景：某个 MCP 工具的入参需要密钥（查 12306 要 `api_key`、调 CRM 要 `tenant_token`），而这些值不该让模型编。

**声明**

管理端 → MCP 服务注册 → 服务 → 工具 → 参数设置 → 把参数标为「来自凭据」。

标记后这个参数会从模型可见的列表里消失，改由平台注入。槽位自动算成 `arg:<短工具名>:<参数名>`。

**供给**（两种模式，可并存）

| 模式 | 怎么做 | 效果 |
| :--- | :--- | :--- |
| 全局 | 管理员在参数设置里选一条**系统级**凭据 | 被授权用户零配置直接用 |
| 个人 | 每个用户在「我的 MCP → 平台服务」里绑自己的 | 只对自己生效，**优先**于全局 |

个人优先这条很重要：管理员配了一条公共的，某个用户想用自己的额度，绑一条就能覆盖。

**注意**：参数级绑定是**必须的**。声明了却没人绑定，调用会直接报错（不是静默跳过），因为这是缺必需参数。

---

## 八、API 参考

前缀 `/api/portal/secrets`。所有响应遵循 `{"code": 0, "data": ...}`。

### 8.1 凭据管理

| 方法 | 路径 | 权限 | 说明 |
| :--- | :--- | :--- | :--- |
| `GET` | `""` | 登录 | 我可见的凭据（自己的 + 被授权的系统级）。未授权的**连名字都不返回** |
| `POST` | `""` | 登录 | 创建个人凭据 |
| `GET` | `/{id}` | 登录 | 详情（不含明文）；非管理员只返回全局绑定和自己的绑定 |
| `PATCH` | `/{id}` | 登录 | 改元数据（名称 / 场景 / 白名单 / 分类 / 备注）。**改值请用轮换** |
| `DELETE` | `/{id}` | 登录 | 删除（同时清理绑定与授权，保留使用流水） |
| `POST` | `/{id}/rotate` | 登录 | 轮换值，旧值保留 `overlap_days`（默认 7 天） |
| `POST` | `/{id}/status` | 登录 | 停用 / 启用 |
| `GET` | `/{id}/value` | 登录 | **仅 `variable`**：读明文，每次记流水 |

### 8.2 系统级凭据（管理员）

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `POST` | `/admin` | 创建系统级凭据 |
| `GET` | `/admin` | 系统级凭据列表 |
| `POST` | `/admin/{id}/grants` | 授权（`grantee_type` + `grantee_id`，`all` 时 `grantee_id` 可省） |
| `DELETE` | `/admin/{id}/grants` | 撤销授权 |
| `POST` | `/admin/{id}/disable` | 强制停用 |
| `POST` | `/admin/{id}/status` | 停用 / 恢复 |

### 8.3 绑定

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/bindings` | 按消费者查 / 按凭据查。**别人的个人绑定不返回** |
| `POST` | `/bindings` | 绑定。入参 `secret_id` + `consumer_type` + `consumer_id` + （`slot` 或 `name`）+ `is_global` |
| `DELETE` | `/bindings` | 解绑。默认解自己的，`is_global=true` 需管理员 |
| `POST` | `/check-binding` | **只校验不出站**：这个槽位现在能不能取到值 |
| `GET` | `/platform-targets` | 我能给哪些平台资源绑自己的凭据 |
| `GET` | `/mcp-targets` | 我能给哪些平台 MCP 服务绑自己的凭据 |
| `GET` | `/picker` | 选择器数据（只回 id 与元数据，不回值） |

### 8.4 观测

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/usage` | 使用流水（`secret_id` / `days` 可选） |
| `GET` | `/usage/stats` | 按凭据 / 场景 / 结果 / 天聚合。**被拒绝的也算** |
| `GET` | `/audit` | 变更记录 |
| `GET` | `/policy` | 平台注入策略 |
| `PATCH` | `/policy` | 改平台注入策略（管理员） |
| `GET` | `/prefs` | 我的注入偏好 |
| `PATCH` | `/prefs` | 改我的注入偏好 |

### 8.5 MCP 工具参数声明

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `PUT` | `/api/portal/mcp/tools/{tool_id}/credential-params` | 标记哪些参数由凭据提供。需 `element:mcp_management:publish` |

---

## 九、配置项

| 键 | 默认 | 说明 |
| :--- | :--- | :--- |
| `secret_policy_allow_external_host` | `1` | 关掉后，所有对外部域名的注入一律 `denied_by_host` |
| `secret_policy_allow_script_env` | `0` | 脚本 env 注入总开关。**默认关** |
| `secret_policy_max_inject_per_call` | `3` | 单次最多注入几条，夹在 1..50 |

用户级偏好（`sys_secret_user_prefs.allow_script_env`）默认 `false`，与平台开关是**与**关系。

脚本注入需要两层都开，是刻意的：平台层面知道脚本能把值 `print` 出来，用户层面则是「我知道我在给脚本凭据」。

---

## 十、设计取舍与已知限制

### 10.1 有意为之的

**凭据类永远不可读，包括管理员。** 这不是权限设计失误。「能查看」和「能使用」是两种能力，给了前者就必然出现「截图发群里」这类事故。需要人工确认值时，用轮换换一个新的。

**`SecretPermissionError` 回 404 而不是 403。** 403 等于承认「这条凭据存在，只是你碰不了」，那就把 UUID 变成了可探测的存在性预言机。「看不见就是不存在」要在写路径上也成立。

**使用流水写失败不阻断业务。** `_log_usage` 里的异常只告警。审计的价值在于事后可查，不该让主功能为它陪葬。

**绑定表不设外键。** 凭据表要能单独导出再导入；外键会强制父表先存在，导入顺序一错就整批失败。级联清理由 `center.delete_secret` 显式做。

**落库扫描与运行期脱敏是两条独立的链。** 见第六章第四节。

### 10.2 已知限制

| 限制 | 影响 | 现状 |
| :--- | :--- | :--- |
| `agent` 场景是空壳 | 只有常量和槽位白名单，无注入代码 | 界面上标注「暂未接入」 |
| `scheduled_task` 是遗留常量 | 不参与判定 | 保留仅为兼容旧数据 |
| 脚本 env 注入的拒绝只记日志 | 脚本仍会跑，只是变量为空 | 原因写进 `_credential_denials`，随结果返回 |
| 前端契约以文本断言为主 | 逻辑错位类问题覆盖有限 | `test_frontend_route_exists.py` 只解决「接口不存在」 |
| 历史全局绑定不迁移 | 升级前落成 `admin_id` 的绑定仍在 | 功能正确（个人优先、值相同），重绑一次即可 |

### 10.3 排查时的顺序

遇到「凭据不生效」，按这个顺序查，能覆盖绝大多数情况：

1. **绑定存在吗** → `GET /bindings?consumer_type=...&consumer_id=...`
2. **绑定是给谁的吗** → 全局（`bound_by_user_id=''`）还是某个人的
3. **凭据本身能用吗** → `status` / `expires_at` / `allowed_scenarios`
4. **授权给了吗**（系统级） → `GET /admin` 看 grants
5. **域名对吗** → `allowed_hosts` 与目标 URL 的 host
6. **开关开了吗**（脚本） → 平台策略 × 用户偏好
7. **看流水** → `GET /usage?secret_id=...`，`result` 字段直接给出结论

第 7 步通常一步到位，因为每次解析（包括被拒的）都留了记录。

---

## 附：文件地图

| 关注点 | 文件 |
| :--- | :--- |
| 判定纯函数、`SensitiveValue` | `app/services/secrets/policy.py` |
| 解析主流程、流水写入 | `app/services/secrets/resolver.py` |
| 四条注入链路 | `app/services/secrets/injection.py` |
| CRUD / 轮换 / 授权 / 统计 | `app/services/secrets/center.py` |
| 运行期回显脱敏 | `app/services/secrets/redact.py` |
| 落库前擦除 | `app/services/secrets/{scanner,registry,guard,fingerprint}.py` |
| 表模型 | `app/models/secret.py` |
| HTTP 端点 | `app/api/portal/endpoints/secrets.py` |
| 迁移 | `db-prod/V209-*.sql` / `db-prod-pg/V109-*.sql` |
| 个人端页面 | `frontend/src/views/SecretCenter.vue` |
| 管理端页面 | `frontend/src/views/AdminSecretCenter.vue` |
| 绑定选择器 | `frontend/src/components/secrets/CredentialPicker.vue` |
| 使用统计组件 | `frontend/src/components/secrets/SecretUsageStats.vue` |
