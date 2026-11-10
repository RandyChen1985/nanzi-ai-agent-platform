# 凭据中心：上线与验证

面向「已经拿到代码、要自己跑一遍确认」的人。按顺序做，前面不通就别往后走。

## 一、先执行数据库变更

三件事，顺序不能换。

### 1. V209 已执行过的话，要单独去外键

凭据中心的五张表**没有外键**（要能单独导出再导入，外键会强制父表先存在，导入顺序一错就整批失败）。如果之前的 V209 已经建好表，那批表上是带外键的，重跑 `CREATE TABLE IF NOT EXISTS` 不会改已有表：

```sql
-- MySQL
ALTER TABLE sys_secret_grants    DROP FOREIGN KEY fk_grant_secret;
ALTER TABLE sys_secret_bindings  DROP FOREIGN KEY fk_binding_secret;

-- PostgreSQL
ALTER TABLE sys_secret_grants    DROP CONSTRAINT fk_grant_secret;
ALTER TABLE sys_secret_bindings  DROP CONSTRAINT fk_binding_secret;
```

确认干净（两个库都应为 0 行）：

```sql
SELECT table_name, constraint_name
FROM information_schema.table_constraints
WHERE constraint_type = 'FOREIGN KEY'
  AND table_name LIKE 'sys_secret%';
```

### 2. 没执行过的，按顺序跑

```
db-prod/V209-add-credential-center.sql        -- 五张表
db-prod/V210-add-credential-center-menu.sql   -- 系统管理 → 凭据中心菜单
```

PostgreSQL 对应 `V109` / `V110`。

### 3. 菜单种子已改动

`app/services/menu_seed.py` 里新增了 `menu:system:credentials` 与 `element:system:credentials_manage`。如果你们的部署流程依赖菜单种子自动同步，重启即可；如果是手工导入 `ai_agent_menus`，跑 V210。

---

## 二、影响面：哪些既有功能被动了

改动不只「新增功能」，有四条既有链路被碰过。**回归优先看这四条。**

| 链路 | 改了什么 | 风险 |
|---|---|---|
| **落库脱敏（P0）** | `AuditManager.save_trace_logs` / `save_history`、AgentScope `state_store.save`、MCP `outbound_audit` 三处出口在写库前扫描已知凭据值并替换成 `${secret:标签}` | 脱敏过度会把正常文本替换掉；脚本/审计写入性能 |
| **API 工具调用** | `generic_api._perform_request` 的 header 段多了一次绑定的解析 | 多一次 DB 查询；`${CREDENTIAL:}` 占位符处理顺序 |
| **MCP 出站调用** | `mcp_client` 在 `build_mcp_headers` 之后叠加绑定 | **绑定优先**，会覆盖 `fixed_token` |
| **脚本工具执行** | `script_tool_runtime.run` 的命令拼接多了 `set -a && . env文件 && set +a` 前缀 | 没绑定时前缀不出现，行为应与改动前完全一致 |

其余为纯新增：个人中心 tab、系统管理页面、菜单节点、凭据表。

---

## 三、回归：确认没弄坏既有功能

重启服务后挨个走。**这些项任何一条不对劲都比新功能不通更严重**，因为它们影响存量用户。

### 1. 脱敏还在工作

随便跑一轮对话，让智能体调用一次带密钥的工具（或用一个配了密钥的数据源），然后看审计：

- 对话历史的 `query` / `summary` 里**不应出现**任何配置密钥明文
- 工具入参/出参里**不应出现** `sk-` 开头的完整 key
- 出现 `${secret:config.xxx}` 这种标记是**正常的**——那就是脱敏后的形态

```sql
-- 抽查最近的历史记录，看有没有漏网的密钥形态
SELECT id, LEFT(query, 200) FROM ai_agent_chat_history ORDER BY created_at DESC LIMIT 5;
```

### 2. 四种工具照常能跑

| 工具类型 | 怎么做 | 应有的结果 |
|---|---|---|
| API 工具 | 找一个**没绑定凭据**的已有工具，试跑一次 | 和改动前一样成功；带 `${CREDENTIAL:}` 占位符的仍报「凭据还没填」 |
| MCP 工具 | 找一个配了 `fixed_token` 的服务，调用一次 | 成功，用的还是那个共享 Token |
| 脚本工具 | 跑一个已有脚本 | 成功；stdout 与改动前一致 |
| 定时任务 | 手动触发一次已有任务 | 成功；日志里执行用户是任务创建者 |

脚本工具这条要特别留意：`run()` 的代码路径变了（多了 `materialize_secret_env`）。没绑定时它返回 `None`，命令前缀为空，拼出来的命令应与改动前逐字节一致。如果脚本突然报 shell 语法错误，从这里查。

### 3. 后台日志没有新噪音

```bash
grep -c "SecretInject" <your log file>
```

预期是 **0**，或只在真的绑过凭据时出现。如果每次调用都刷一条 WARNING，说明绑定表查询在失败——那是迁移没执行完的信号。

（表不存在时会触发 60 秒熔断，不会刷屏，但第一次的 WARNING 会明确写「读取绑定失败」。）

### 4. 界面没塌

- 个人中心 10 个 tab 都能点开，「我的凭据」在「我的 MCP」和「我的任务」之间
- 工具编辑页 → 高级请求配置 → Headers 的「键值对」模式能增删行、能保存
- MCP 服务编辑页的认证区块正常，未绑定时不显示「已绑定」
- 系统管理侧栏出现「凭据中心」（管理员账号）

---

## 四、新功能：确认真的能用

### 建一条凭据

个人中心 → 我的凭据 → 新建。

- 类型选「凭证」，场景勾上「API 工具」，域名白名单填目标服务的域名（**留空等于只允许平台内部目标**，发往外部会被拒）
- 提交后列表出现，值显示为掩码，没有「查看」按钮——这是设计，不是 bug

### 绑到一个 API 工具

工具管理 → 编辑一个已有工具 → 高级请求配置 → Headers。

1. 点某个 header 行右侧的钥匙图标
2. 选刚建的凭据 → 保存绑定
3. 那一行的值应变成 `${CREDENTIAL:你的标识}`，图标变绿

新建工具时钥匙是灰的——绑定的消费者是工具 id，工具没入库就没有可绑的对象。

### 证明注入真的生效

**这是整条链路唯一可靠的黑盒验证方法**：把凭据的值故意填错，再调用工具。

| 现象 | 说明 |
|---|---|
| 目标服务返回 401 / 403 | ✅ 注入生效了，用的是凭据中心的新值 |
| 工具仍然成功 | ❌ 没走绑定，还在用工具配置里原来的值（或 `fixed_token`） |
| 报「凭据还没填」 | ❌ 绑定没写进去，或场景/域名被拦（看使用记录） |

验证完记得轮换成正确的值。

### 看使用记录

个人中心 → 我的凭据 → 使用记录。每条记录有场景、结果、目标域名。

`result` 的取值和含义：

| result | 含义 | 该查什么 |
|---|---|---|
| `success` | 取到了值 | — |
| `denied_by_binding` | 这个槽位没有绑定 | 绑定没保存成功，或绑到了别的工具/槽位 |
| `denied_by_grant` | 系统级凭据没授权给你 | 找管理员授权，或检查 `dept_code` 是否匹配 |
| `denied_by_scenario` | 凭据没开这个场景 | 编辑凭据，勾上对应场景 |
| `denied_by_host` | 目标域名不在白名单 | 编辑凭据补白名单；**空列表只允许内部目标** |
| `denied_by_script_policy` | 脚本 env 被平台策略关着 | 系统配置开 `secret_policy_allow_script_env` + 个人偏好两层都开 |

### MCP 服务的绑定

MCP 服务 → 编辑 → 认证区下方「用凭据中心的凭据」。

绑上之后**优先于上面的固定 Token**——验证方法同上（把一个错的 token 绑上去，看远端是否拒绝，而 `fixed_token` 本来是对的）。

### 脚本工具的环境变量

脚本工具 → 编辑 → 第四步「依赖与超时」→ 环境变量 → 「+ 绑定环境变量」。

弹窗里填**变量名**（要和脚本里 `os.environ["..."]` 完全一致），再选一条允许「脚本环境变量」场景的凭据。

验证方法：脚本里写

```python
import os, json, sys
print(json.dumps({"pw": os.environ.get("DB_PASSWORD", "<missing>")}))
```

把凭据的值填成 `abc123`，跑一次脚本，输出里应该是 `abc123`。看到 `<missing>` 说明没注入——这时按顺序查：凭据是否勾了「脚本环境变量」场景、平台策略 `secret_policy_allow_script_env` 是否打开、个人偏好是否打开、变量名是否拼错（区分大小写）。

值**不会出现在命令行里**（写的是 0600 文件再由 shell `.` 加载），执行完立即删除。

### 变量类凭据读明文

列表里只有「变量」类型的行有「查看值」按钮。点开能看到明文并复制——「凭证」类型没有这个按钮，接口也会 403，设计上就不存在这个能力。

每次读取都会记一条使用记录，弹窗里也写明了。

### 这条凭据用在哪

列表每行都有「绑定」按钮，点开是这条凭据的全部绑定：

- **全局**：管理员做的绑定，对所有能用该消费者的人生效
- **我绑的**：只影响你自己，并会覆盖全局

删掉消费者（工具 / MCP 服务 / 脚本）时绑定会一起清掉，不会留孤儿。

### 系统级凭据与授权

管理员在「系统管理 → 凭据中心」建一条系统级凭据，然后：

1. 授权给用户 A
2. A 登录 → 个人中心 → 我的凭据 → 系统共享 → 能看到
3. 用户 B 登录 → 看不到（列表里连名称都没有）
4. 撤销授权 → A 也看不到了

「未授权连名字都不出现」是刻意的：凭据名本身是情报（`sap.prod.admin_token` 这种名字一看就知道有什么）。

---

## 五、出问题时看哪里

| 症状 | 先看 |
|---|---|
| 凭据列表一直转圈 / 报错 | 迁移有没有跑完；`sys_secrets` 表在不在 |
| 绑定保存成功但调用没用上 | 使用记录的 `result`；日志 `[SecretInject]` 前缀 |
| 每次调用都刷 `SecretInject` WARNING | 绑定表查询失败，多半是迁移没落全（会熔断 60 秒） |
| 界面报 404 而不是 403 | 正常。未授权的凭据连「存在」都不该暴露 |
| 管理员的「凭据中心」不出现 | 权限码 `menu:system:credentials` 有没有写进 `ai_agent_menus` |
| 脚本报 shell 语法错误 | 脚本执行命令拼接；没绑定时前缀应为空 |

---

## 六、还没做的

写在这里避免误判成 bug：

- **脚本 env 还要单独开闸**：界面上能绑了，但注入要求平台策略 `secret_policy_allow_script_env` 与用户偏好（我的凭据 → 策略）**同时**打开，两层默认都是关。只绑不开，脚本拿不到变量。
- **存量 MCP token 导入**：决定不做——`fixed_token` 是服务级共享的，搬进凭据中心效果一样且变得更难维护。需要按用户区分时，让用户自己绑即可，绑定会自动盖过 `fixed_token`。
