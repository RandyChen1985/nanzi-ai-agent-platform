# 用户级凭据中心（Credential Center）设计稿

- 状态：已确认方向，待实现
- 日期：2026-11-09
- 原型：[`prototypes/2026-11-09-user-credential-center.html`](../../../prototypes/2026-11-09-user-credential-center.html)（7 个视图）
- 前置：P0 落库止血（`app/services/secrets/`）已上线

## 1. 要解决的问题

平台现在有**四套互不相通的「凭据」机制**：

| 机制 | 位置 | 缺点 |
|---|---|---|
| `system_configs.is_secret` | 系统配置 | 明文存库，全局唯一，无归属 |
| `{user.xxx}` 占位符 | 工具参数 | 只有身份字段（用户名/部门），**没有密码** |
| `${CREDENTIAL:header}` | 工具导出 | 只是「未填」标记，不是取值来源 |
| MCP `auth_headers` | MCP 连接 | 加密但只服务 MCP，导出/分享/展示各处要额外脱敏 |

同一条 token 要在三处各存一份，轮换必漏；工具调用的入参以前还会明文落进 `ai_agent_execution_traces`（P0 已止血，但「值从哪来」仍未解决）。

**这一次要做的是唯一的取值来源**：一人一套、显式绑定、模型永不可读、全链路脱敏。

## 2. 五条不可让步的结论

1. **凭据和消费者必须是显式绑定，默认零授权**。建完不等于能用，必须再勾「允许给哪个 MCP / 工具 / 脚本用」。没有这条，模型能诱导任意工具读走全部凭据。
2. **模型永远不能「读」凭据，只能触发已绑定的槽位**。引用语法 `${secret:key}` 只写在配置里，不写在对话里，所以 prompt injection 也取不到值。
3. **凭据建完即不可读，包括创建者和管理员**。只给「测试连通性」和「轮换」。变量类（租户号、接口地址）才可读可复制。
4. **指纹负责脱敏，不靠猜键名**（P0 已具备）。
5. **域名白名单把「自动注入」关进笼子**。只允许发往声明的域名，否则自动注入就是万能外泄通道。

## 3. 数据模型

四张表，MySQL `V209` / PostgreSQL `V109`。

### 3.1 `sys_secrets`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(36) PK | UUID |
| `owner_scope` | VARCHAR(16) | `user` / `system`（`dept` 预留，V1 不开放） |
| `owner_id` | VARCHAR(64) NULL | `user` 时为 `user_id`；`system` 时为 NULL |
| `secret_key` | VARCHAR(128) | 用户内的唯一标识，如 `jira.prod.token` |
| `name` | VARCHAR(128) | 展示名 |
| `secret_type` | VARCHAR(16) | `credential`（不可读）/ `variable`（可读） |
| `value_encrypted` | TEXT NULL | Fernet 密文；`variable` 与 `credential` 都加密存 |
| `value_fingerprint` | CHAR(16) | sha256 前 16 位，供落库扫描比对 |
| `value_preview` | VARCHAR(24) NULL | 仅 `variable` 存前 8 字符，供列表展示；`credential` 为 NULL |
| `allowed_hosts` | JSON | 域名白名单数组，`[]` = 只允许平台内部 |
| `allowed_scenarios` | JSON | `["mcp","api_tool","script_env","scheduled_task","agent"]` 的子集 |
| `status` | VARCHAR(16) | `active` / `disabled` / `expired` |
| `expires_at` | DATETIME NULL | NULL = 长期有效 |
| `previous_value_encrypted` | TEXT NULL | 轮换过渡期的旧值（双写 7 天） |
| `previous_expires_at` | DATETIME NULL | 旧值失效时间 |
| `category` | VARCHAR(64) NULL | 用户自定义分类（第三方 API / 数据库 / 平台内部…） |
| `remark` | VARCHAR(500) NULL | |
| `created_by` | VARCHAR(64) | |
| `created_at` / `updated_at` / `last_rotated_at` | DATETIME | |

唯一索引：`uk_owner_key (owner_scope, owner_id, secret_key)`。

> `owner_id` 为 NULL 时唯一约束在 MySQL 下不生效（NULL 互不相等），所以 `system` 级靠应用层校验唯一性，并在 service 里对 `owner_id` 写空串 `''` 而不是 NULL —— 与 `ai_agent_resource_permissions` 的既有做法一致，避免 NULL 语义带来的坑。

### 3.2 `sys_secret_grants`（系统级授权给谁）

| 字段 | 说明 |
|---|---|
| `secret_id` | FK → `sys_secrets.id` ON DELETE CASCADE |
| `grantee_type` | `user` / `dept`（V1 只这两种） |
| `grantee_id` | 用户 ID / 部门编码 |
| `granted_by` | |
| `granted_at` / `expires_at` | |

唯一索引 `(secret_id, grantee_type, grantee_id)`。

**可见性规则**：用户在选择器里看到某条系统凭据 ⟺ 存在有效 grant（`user` 直接授权，或 `dept` 命中该用户所属部门）。未授权连名称都不返回。

### 3.3 `sys_secret_bindings`（绑定到消费者）

| 字段 | 说明 |
|---|---|
| `secret_id` | FK → `sys_secrets.id` ON DELETE CASCADE |
| `consumer_type` | `mcp_server` / `api_tool` / `script_tool` / `agent` |
| `consumer_id` | |
| `slot` | `header:X-Api-Key` / `query:token` / `body:password` / `env:DB_PASSWORD` |
| `bound_by_user_id` | 个人绑定时填；管理员全局绑定为 NULL |
| `created_at` | |

唯一索引 `(consumer_type, consumer_id, slot, bound_by_user_id)`。

**这是最容易漏的一条**：如果 binding 只记 `secret_id + consumer`，A 把系统 token 绑到共享工具上，B 调用同一工具也会用到它——而 B 可能没被授权。所以解析时必须校验
`bound_by_user_id IS NULL OR bound_by_user_id = <执行主体>`。

### 3.4 `sys_secret_usage_logs`（审计）

| 字段 | 说明 |
|---|---|
| `secret_id` | **不设外键**（删凭据不删记录） |
| `secret_key_snapshot` | 凭据被删后仍可溯源 |
| `trace_id` / `conversation_id` / `user_id` | |
| `scene` | `mcp` / `api_tool` / `script_env` / `scheduled_task` / `agent` |
| `consumer_type` / `consumer_id` / `slot` | |
| `result` | `success` / `denied_by_binding` / `denied_by_grant` / `denied_by_host` / `denied_by_scenario` / `expired` / `disabled` |
| `target_host` | 本次注入的目标域名（可为空） |
| `created_at` |（索引） |

## 4. 三层开关（取交集）

| 层 | 谁控制 | 存储位置 | 默认 |
|---|---|---|---|
| ① 平台策略 | 管理员 | `system_configs`（`secret_policy_*`） | 脚本关，其余开 |
| ② 用户偏好 | 用户 | `user_preferences` 或 `sys_secret_user_prefs` | 脚本关 |
| ③ 单条凭据 | 用户/管理员 | `sys_secrets.allowed_scenarios` | 全不勾 |

生效：`① AND ② AND ③`，任一为假 → 拒绝 + 记 `denied_by_*`。**只能收紧不能放宽**。

V1 只落 ① 和 ③，② 用一个 `system_configs` 之外的轻量偏好表（`sys_secret_user_prefs`，2 列：`user_id`、`allow_script_env`）。

## 5. 注入链路

统一入口 `SecretResolver.resolve(...)`：

```
入参：user_id, scene, consumer_type, consumer_id, slot, target_host, trace_id
 1. 按 (consumer_type, consumer_id, slot) 查 binding，按 bound_by 可见性过滤
 2. 取 secret，校验 status / expires_at（含 previous 双写窗口）
 3. 校验 scene ∈ allowed_scenarios
 4. 校验 target_host ∈ allowed_hosts（空数组 → 只允许空 host）
 5. 校验平台策略 + 用户偏好
 6. 解密 → 包成 SensitiveValue
 7. 记 usage_log（异步、失败不影响主链路）
```

四个场景的调用点：

| 场景 | 位置 | 说明 |
|---|---|---|
| `mcp` | `mcp_auth_policy.resolve_mcp_auth_headers` 之后 | 命中 binding 时用解析值覆盖 |
| `api_tool` | `generic_api.py` 的 header/query/body 注入 | 已有 `x-nanzi-system-params` 通道，凭据走同一处 |
| `script_env` | `script_tool_runtime` 写子进程 env | 默认关；env 不进命令行参数 |
| `scheduled_task` | `scheduler_service` 构造上下文 | 主体 = `task.user_id`（创建者），不是当前登录用户 |

### `SensitiveValue` 包装类

```python
class SensitiveValue:
    __repr__ = lambda self: "SensitiveValue('***')"
    def __str__(self): raise SecretLeakAttempt(...)
    def __format__(self, spec): raise SecretLeakAttempt(...)
    def json(self): return "***"
    def reveal(self) -> str: ...   # 只有注入层调
```

防的是 `f"{secret}"`、`print(secret)`、`json.dumps({"x": secret})` 这类把值顺手带进日志的写法。

## 6. 与 P0 的关系

P0 的 `SecretRegistry` 收集明文值做扫描；P1 上线后改为**直接读 `value_fingerprint`**，不再需要明文进内存：

```python
# P0
pairs = [(label, plaintext), ...]
# P1
fingerprints = {fp: label for fp in expected_fingerprints}
```

`SecretScanner` 的对外接口不变（仍按值替换），只是内部比对从「明文相等」变成「候选 token 的指纹命中」。这一步在 P1 里做，P0 保持现状即可。

## 7. 接口

| 方法 | 路径 | 权限 |
|---|---|---|
| GET | `/api/portal/secrets` | 本人（含被授权的系统级） |
| POST | `/api/portal/secrets` | 本人 |
| POST | `/api/portal/secrets/{id}/rotate` | 创建者 / 管理员（系统级） |
| POST | `/api/portal/secrets/{id}/test` | 有绑定或授权的人 |
| PATCH | `/api/portal/secrets/{id}/scenarios` | 创建者 |
| DELETE | `/api/portal/secrets/{id}` | 创建者 |
| GET/POST/DELETE | `/api/portal/secrets/bindings` | 本人 |
| GET | `/api/portal/secrets/usage` | 本人（系统级可见全局） |
| GET | `/api/portal/secrets/policy` · PATCH | 读所有人 / 写管理员 |
| GET/POST/DELETE | `/api/portal/system/secrets/{id}/grants` | `element:secrets:admin` |

**管理端不提供任何返回明文的接口**，`GET` 一律返回 `value_masked` 与 `has_value`。

## 8. 前端入口

- 用户：个人中心顶部 tab 「我的凭据」（`menu:personal` 下，排「我的 MCP」后），权限码 `element:personal:secrets`
- 管理员：系统管理 → 凭据中心，`menu:system:credentials` + `element:secrets:admin`
- 三处直达入口：我的 MCP 认证字段、工具凭据槽位、对话失败卡片

## 9. 明确不做（V1）

- `dept` owner_scope（表结构预留，不开 UI）
- 临时授权 / 一次性放行（多一个能被诱导的口子）
- 凭据申请审批流（先做「申请」按钮，落到管理员待办，不建完整工作流）
- 跨平台凭据同步、外部 Vault / KMS 对接
- 明文恢复（管理员也不能读，换 KMS 才谈得上）

## 10. 迁移与兼容

- 新表全部 `CREATE TABLE IF NOT EXISTS`，不改动任何既有表
- 存量数据不迁移：`system_configs.is_secret` 与 MCP `auth_headers` 保持原样，凭据中心只做**新增路径**
- 「把存量 MCP token 导入凭据中心」作为一次性运维脚本，不放进迁移
- 代码可以先于迁移上线：`SecretResolver` 查表失败时按「无凭据」处理（fail-closed），MCP / 工具行为退回现状
