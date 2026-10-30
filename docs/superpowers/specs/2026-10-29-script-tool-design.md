# 脚本工具（Script Tool）设计

- 日期：2026-10-29
- 状态：已评审待实施
- 相关：`sys_api_tools`、`GenericApiToolFactory`、`bind_configured_tools_to_workspace`、`CodeExecutionHandle`

## 1. 背景与问题

平台当前的自定义工具只有两种来源：HTTP API 工具（`sys_api_tools`）与 MCP 注册工具。模型想做「文件转 base64」「算文件哈希」「文本脱敏」这类**纯本地处理**时，只能临场写 Python 再 `exec_command` 跑一遍——每次重新生成、不可复用、不可审计，也没法交给业务线维护。

需要给平台增加一种与 API 工具并列的工具类型：**Python 脚本工具**。管理员（或平台自己）写一段脚本、声明参数 schema，注册后模型就能像调用其他工具一样调用它，脚本在**用户会话沙箱**内执行。

## 2. 目标与非目标

### 目标

1. 新增 `sys_script_tools` 表与 CRUD，`source` 区分 `builtin` / `custom`。
2. 脚本在**会话沙箱**内执行：docker / k8s / e2b / ssh 策略下进容器，`local` 策略下降级到用户私有工作区目录。
3. 与现有工具注册链路同构：registry 按 name 解析，智能体配置页可勾选，`forbidden_tools` 可禁用。
4. 提供 5 个开箱即用的内置脚本，以及一个「?」编写指南。
5. 页面上必须明确提示脚本的运行位置（沙箱内 / 本地工作区），不能含糊。

### 非目标（第一版不做）

- 脚本版本历史与回滚
- Python 之外的语言（表里留 `language` 字段，只允许 `python`）
- 流式输出回显、在线断点调试
- 定时调度触发
- 脚本间互相调用

## 3. 数据模型

新表 `sys_script_tools`。**不扩展 `sys_api_tools`**：`method` / `url_template` 是 NOT NULL，复用会产出大量语义为空的列，且两类工具的 schema 后续必然分叉。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 |
| `name` | VARCHAR(100) UNIQUE | 模型可见的工具名，`^[a-z][a-z0-9_]{2,63}$` |
| `display_name` | VARCHAR(255) NULL | 页面展示名，空则用 name |
| `description` | TEXT NULL | 给模型看的能力描述 |
| `source` | VARCHAR(16) NOT NULL | `builtin` / `custom` |
| `language` | VARCHAR(16) NOT NULL DEFAULT 'python' | 第一版只允许 python |
| `code` | MEDIUMTEXT NULL | **custom 必填；builtin 为 NULL**（源码在代码里，见 §4） |
| `parameter_schema` | TEXT NULL | 复用 API 工具的三种写法兼容 |
| `requirements` | TEXT NULL | JSON 数组，如 `["pypdf>=4.0"]` |
| `timeout_seconds` | INT NULL | 空则跟随平台「工具调用超时」 |
| `group_name` | VARCHAR(64) NULL | 业务分组，与 API 工具同语义 |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `created_at` / `updated_at` | DATETIME(6) NOT NULL | |

迁移：`db-prod/V200-add-script-tools.sql`（MySQL）、`db-prod-pg/V100-add-script-tools.sql`（PostgreSQL）。两套都必须有，且内容等价（PG 用 `BOOLEAN` / `TEXT` / `TIMESTAMP`）。

## 4. 内置脚本：代码里唯一定义

内置脚本的源码**不进数据库**，唯一源是 `app/services/ai/tools/script_tool_builtins.py`：

```python
@dataclass(frozen=True)
class BuiltinScriptTool:
    name: str
    description: str
    parameter_schema: dict
    group_name: str | None
    requirements: tuple[str, ...]
    code: str
    timeout_seconds: int | None = None

BUILTIN_SCRIPT_TOOLS: tuple[BuiltinScriptTool, ...] = (...)
```

理由：内置脚本属于平台代码资产。若把源码 seed 进库，每次改一行脚本都要写一条 `UPDATE` 迁移，且用户可能误改后升级被覆盖。

迁移里只 seed **元数据行**（`source='builtin'`、`code=NULL`、`is_active=1`、`group_name`、`description`、`parameter_schema`），下发方式与 `menu_seed` 一致：由 `scripts/gen_script_tool_seed_sql.py` 从代码定义生成 SQL，契约测试对账「代码定义 ↔ 迁移 SQL」不漂移。

运行时解析规则：

- `builtin` 行：`code` 取代码定义；`is_active` / `group_name` 取库里行（允许管理员停用或改分组）。
- 库里没有该 builtin 行（迁移未执行）：**仍然可用**，等价于默认启用，保证「代码先上线、迁移后执行」期间行为可预期。
- `custom` 行：`code` 取库里行。

## 5. 执行链路

```
registry._load_db_tool_with_source(name)
  1. MCP 工具缓存  → McpToolFactory
  2. sys_api_tools → GenericApiToolFactory       （顺序不变）
  3. sys_script_tools / 内置定义 → ScriptToolFactory   ← 新增
       └─ 产出 StructuredTool 壳（只有 name/description/args_schema，不执行）

runtime spec 的 source_type = "script_tool"

bind_configured_tools_to_workspace(workspace, specs)
  └─ 命中 source_type == "script_tool"
       └─ 包成 ScriptToolRunner(sandbox_ws, local_ws, row)
            ├─ sandbox_ws 可用 → 走沙箱 bash 通道（docker/k8s/e2b/ssh 全适配）
            └─ sandbox_ws 为空  → local 分支：run_shell_command_capture 跑用户工作区
```

**不新增执行原语**：沙箱内执行复用 `_sandbox_bash_tool_from_mcps` 拿到的 bash 工具（`await bash(command=..., cwd=...)`），与 AgentScope 的 Bash 工具完全同源，因此超时、取消、输出上限、命令策略全部自动继承。

### 5.1 一次调用的 5 步

1. **参数准备**：`parameter_schema` 归一化后补齐 `{user.xxx}` 占位符默认值。为此把 `generic_api.py` 里的两个私有函数 `_parse_parameter_schema` / `_apply_user_placeholders` 提取到新模块 `app/services/ai/tools/parameter_schema.py`，两边共用（直接跨模块 import 私有函数会把耦合藏进下划线）。`apply_system_params` 已经是公开模块 `system_params.py`，无需搬动。
2. **落盘**：写入会话工作区
   - `.script_tools/<name>.<code_sha8>.py`（用代码哈希命名，**避免并发调用与代码更新时互相覆盖**，也天然支持两个会话并行跑同一工具）
   - `.script_tools/<name>.<code_sha8>.params.json`（`chmod 0600`）
   - 有依赖时写 `<same>.requirements.txt`
3. **装依赖**（仅在 `requirements` 非空且 stamp 不存在时）：目标目录 `.pylibs`，成功写 `.script_tools/.pylibs/<name>.stamp`（内容为 requirements 的 sha256）。
4. **执行**
   - 沙箱：`cwd` = 当前会话目录相对沙箱根（`/workspace` 下），命令
     `PYTHONPATH=.pylibs python -u .script_tools/<file>.py < .script_tools/<file>.params.json`
     —— 脚本侧就是 `json.load(sys.stdin)`，而平台侧用 shell 重定向实现，不要求 bash 通道支持 stdin。
   - local：同一条命令交给 `run_shell_command_capture(command, cwd=<会话工作区绝对路径>, timeout_seconds=...)`。
5. **收结果**：见 §6。

### 5.2 路径与降级

- docker / k8s 的沙箱内根是 `/workspace`，宿主工作区通过既有 mount 映射进容器；命令一律用**相对 cwd**，不拼绝对路径，避免 `_map_docker_workspace_path` 之外的二次拼接。
- 沙箱初始化失败（`DockerSandboxUnavailableError` / `K8sSandboxUnavailableError`）：返回明确错误「当前会话沙箱不可用，脚本工具未执行」，**不静默降级到宿主机**。降级只发生在部署策略本身就是 `local` 的情况。
- 会话工作区无法解析：直接报错，不落到 `os.getcwd()`。

## 6. 运行契约

| 项 | 约定 |
|---|---|
| 入参 | stdin 一个 JSON 对象，键为 schema 里的参数名 |
| 出参 | stdout 文本；能 `json.loads` 则结构化返回给模型，否则原样文本 |
| 失败 | 退出码非 0 → 返回「脚本执行失败（退出码 N）」+ stderr 尾部 4KB |
| 超时 | `timeout_seconds` 或平台工具超时配置；超时返回「脚本执行超时（上限 N 秒）」，并终止进程组 |
| 输出上限 | 100 KB（沿用 `MAX_OUTPUT_BYTES`），超出截断并标注 |
| 依赖安装 | 独立 120s 预算，不占工具超时；失败返回「缺少/装不上哪个包 + 原始报错尾部」 |
| 产物文件 | stdout JSON 里含 `artifacts: ["<工作区相对路径>"]` 时，平台调用 `register_artifact` 登记，结果附下载信息 |
| 安全 | 参数文件 0600；脚本不写进用户内容目录；不落 stdout 到日志（可能含 base64 大块） |

## 7. 内置脚本清单（5 个，全部标准库）

| name | 参数 | 输出（JSON） |
|---|---|---|
| `file_to_base64` | `path`*、`max_bytes`=10MB、`with_data_uri`=false | `{path,size,mime,data}` |
| `base64_to_file` | `data`*、`filename`*、`overwrite`=false | `{path,size,artifacts:[path]}` |
| `file_digest` | `target`*、`algorithm`=sha256、`is_text`=false | `{algorithm,digest,size}` |
| `text_regex_extract` | `text`*、`pattern`*、`group`=0、`limit`=100 | `{count,matches}` |
| `text_redact` | `text`*、`targets`=全部 | `{text,redacted:{phone,id_card,email,bank_card}}` |

刻意只用标准库：沙箱镜像不用改就能验证整条链路，`pypdf` / `Pillow` 那批后续以 `custom` 脚本形式补。

## 8. 后端接口

`app/api/portal/endpoints/tools.py` 内新增（同一 router，同一权限口径）：

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/tools/script` | 登录 | 列表，含 `capabilities`（当前 `sandbox_policy`） |
| POST | `/tools/script` | `element:system:config_save` | 新建，跨表查重名 |
| PUT | `/tools/script/{id}` | 同上 | 更新；`builtin` 可改代码/参数/依赖，**改名返回 403** |
| DELETE | `/tools/script/{id}` | 同上 | 删除；`builtin` 返回 403（可停用，不可删） |
| POST | `/tools/script/{id}/reset-to-builtin` | 同上 | 清除覆盖，恢复代码库内置版本 |
| POST | `/tools/script/preview` | 同上 | 试运行：body 为 `{tool_id?, code?, parameter_schema?, requirements?, params}`，返回 `{exit_code, stdout, stderr, duration_ms, timed_out, sandbox_policy}` |
| GET | `/tools/script/capabilities` | 登录 | 运行环境说明（`sandbox_policy` / `sandbox_label` / `sandbox_enabled` / `notice`），前端据此如实显示执行位置 |

- 重名校验范围：`sys_api_tools` + `sys_script_tools` + `mcp_tool_cache` + 静态 registry（`ToolRegistry.known_static_tool_names()`），命中任一即拒绝，避免 registry 按 name 解析出现歧义。
- 试运行走**同一个 runner**，但落在 `_script_tool_preview/<user>/` 预览目录且不接沙箱：试跑时没有会话，为一次试跑拉起容器不划算。页面上必须写明「在平台后端工作区的预览目录中执行」，不能让人以为试跑等于沙箱内运行。
- 列表接口直接返回内置脚本源码（`code` 字段），「复制为新工具」不需要额外的 template 接口。
- 增删改后调用 `ToolRegistry.clear_db_tool_cache()`。

## 9. 前端

### 9.1 列表页（方案 A）

`frontend/src/components/system/ToolRegistry.vue`：

- 标题改为「工具注册表」，标题行下新增一等 tab：`API 工具 (N)` / `脚本工具 (N)`。
- tab 切换联动：右上按钮 `+ 添加 API 工具` ↔ `+ 添加脚本工具`；筛选行 `Method` ↔ `语言`+`来源`；表格列 `METHOD` ↔ `语言`+`来源`；未分组警告条仅 API 侧展示。
- 脚本侧列：名称（名称 + 描述 + `参数：` / `依赖：` 摘要）、语言、来源（内置/自定义徽标）、业务分组、状态、操作（试运行 / 编辑 / 复制 / 删除）。
- 内置行：**可编辑**（用户明确要求「允许直接编辑内置」）；改过 `code` 的行显示「已覆盖」徽标并提供「恢复内置默认」。删除仍禁止（可停用）。改名禁止——名字是内置脚本的身份，改了会导致「恢复默认」与 registry 兜底匹配失效。
- 现有 1215 行的分组筛选逻辑抽成 `composables/useToolGroupFilter.ts`，脚本列表放独立组件 `ScriptToolList.vue`，避免文件继续膨胀。

### 9.2 沙箱提示（必须如实）

脚本 tab 顶部常驻提示条，文案由接口返回的 `capabilities.sandbox_policy` 决定：

| 策略 | 文案 |
|---|---|
| docker / k8s / e2b / ssh | 脚本在**会话沙箱**内执行：只能读写 `/workspace`，无法访问平台后端、数据库或你的本机文件。 |
| local | 当前部署未启用容器沙箱（本地策略）：脚本在**后端工作区的用户隔离目录**内执行，请只运行可信脚本。 |
| 未知 | 脚本在会话工作区中执行，可读写 `/workspace`。 |

编辑弹窗、试运行抽屉、帮助弹窗均复用同一份文案函数 `scriptSandboxNotice(policy)`，不允许各写一份。

### 9.3 编辑与帮助

- `ScriptToolWizard.vue`：名称/描述 → 参数 schema（复用 `ToolParamEditor`）→ 代码（等宽编辑器 + 「插入示例模板」）→ 依赖（逐个添加）→ 超时。顶部显示沙箱提示。
- `ScriptToolHelpModal.vue`：照 `RequestSigningHelpModal.vue` 的形态，含最小模板、stdin/stdout 契约、文件读写约定、依赖声明、退出码、本地自测方式、5 个内置脚本索引。
- 「?」按钮固定在脚本 tab 筛选行，与总开关一起常驻。

### 9.4 智能体配置侧（容易漏）

`AgentManagement.vue` 的 `fetchTools()` 目前只拉 `/api/portal/tools`（API 工具）与 `/tools/mcp`，脚本工具必须在这里额外拉 `/api/portal/tools/script` 并入 `allAvailableTools`（`isSystem: false`、`group_name` 原样透传），否则工具注册好了却在智能体配置里选不到。

分组复用 `utils/toolGrouping.ts` 的 `groupToolsForDisplay`，脚本工具与 API 工具落在同一套「能力域 → 业务分组 → 兜底」结构里，不新造分组逻辑。

## 10. 权限与治理

- 读：任意登录用户（供智能体配置勾选）。
- 写：`element:system:config_save`，与 API 工具完全一致，不新造权限点。
- `forbidden-catalog` 新增 `category: "script"`，内置与自定义脚本均进入可禁用候选；`permission_service` 按 name 匹配即可，无需改表。
- 智能体侧「禁止代码执行」生效时，脚本工具一并不可用（复用现有开关，不新增配置）。

## 11. 错误处理（面向用户/模型可读）

| 场景 | 返回 |
|---|---|
| 脚本语法错误 | 「脚本执行失败（退出码 1）」+ traceback 尾部 |
| 缺依赖 | 「缺少依赖 X：<pip 报错尾部>；请在工具配置的依赖里补上」 |
| 超时 | 「脚本执行超时（上限 N 秒），已终止」 |
| 输出过大 | 截断 + 「…[输出已截断，上限 100KB]」 |
| 沙箱不可用 | 「当前会话沙箱不可用，脚本工具未执行」 |
| 参数不合法 | 由入口 schema 校验拦截，返回缺失字段名 |

## 12. 测试计划

| 文件 | 覆盖 |
|---|---|
| `tests/api/portal/test_script_tool_contract.py` | schema 校验、跨表重名（端点级）、builtin 禁改删、内置缺行兜底、DB 行 → 执行配置 |
| `tests/ai/tools/test_script_tool_runtime.py` | stdin JSON 往返、stdout JSON 解析、非 0 退出、超时、截断、缺依赖提示、并发不同步覆盖 |
| `tests/ai/runtime/test_script_tool_sandbox_binding.py` | docker/k8s 相对 cwd、local 降级、沙箱不可用报错、不落宿主机 |
| `tests/ai/tools/test_script_tool_builtins.py` | 5 个内置脚本各自行为 + 定义与迁移 SQL 对账 |
| `tests/frontend/test_script_tool_registry_contract.py` | tab 挂载、沙箱提示来自后端、指南入口、内置行禁用、智能体配置合并与分组透传 |

## 13. 交付顺序

1. 迁移 + ORM 模型 + schema + CRUD + 内置定义 + seed 生成脚本 + 契约测试
2. `parameter_schema.py` 提取重构 + `ScriptToolRunner` + registry 接入 + 沙箱绑定 + 单测
3. 前端 tab / 列表 / 编辑 / 帮助 / 试运行
4. 5 个内置脚本实现 + 端到端验证清单（docker 与 local 各跑一遍）

## 14. 风险

| 风险 | 缓解 |
|---|---|
| 内置定义与迁移 seed 漂移 | 契约测试对账两边 name/schema 集合，CI 拦截 |
| 沙箱首次执行要装依赖，冷启动慢 | stamp 缓存；页面提示「首次调用会安装依赖」 |
| 大文件 base64 撑爆上下文 | `max_bytes` 默认 10MB，超限直接报错并建议走 artifact 下载 |
| docker 路径映射写错导致脚本在宿主机跑 | 一律相对 cwd；沙箱不可用时报错不降级；专项单测 |
| 并发调用同一工具互相覆盖脚本文件 | 文件名含代码 sha8，天然隔离 |
| 脚本被当成绕过 `exec_command` 的后门 | 写权限与 API 工具同级；受 `forbidden_tools` 与「禁止代码执行」双重约束 |

## 15. 落地记录（实现与设计的差异）

实现过程中有几处按实际情况调整，均以「不隐瞒、不降级」为准：

1. **端点拆分到独立模块**：`app/api/portal/endpoints/script_tools.py`（仍挂在 `/tools` 前缀）。
   `tools.py` 已 600+ 行且混着 API 工具、MCP、禁用目录三件事，继续塞会让后续维护更糟。
2. **试运行不进沙箱**：落在 `_script_tool_preview/<user>/`，用 local 分支执行，页面文案明示位置。
   真实调用仍走会话沙箱；`capabilities.notice` 负责把两者的差别讲清楚。
3. **分组筛选没有抽成 composable**：第一版分组逻辑内聚在 `ScriptToolList.vue` 内，
   避免为了复用去重构 1200 行的 `ToolRegistry.vue`。等第三类工具出现时再抽。
4. **没有写 seed 生成脚本**：迁移 SQL 手写，靠 `test_script_tool_builtins.py` 的
   「代码定义 ↔ 迁移 SQL」对账测试保证两边一致（name 集合、内置行 code 必须为 NULL、
   语句拆分正确）。生成器只在「内置脚本会频繁增删」时才划算，第一版 5 个不需要。
5. **e2b / ssh 明确不支持**：这两种策略没有「宿主工作区 → 沙箱路径」映射，
   `capabilities.script_tool_supported=false`，页面禁用创建入口并说明原因。

另外两点实现细节值得记下：

- **命令信封**：沙箱 bash 通道只回一段文本，因此用 `__NANZI_EXIT__:` / `__NANZI_STDOUT_START__` /
  `__NANZI_STDERR_START__` 自描述。退出码只认 `STDOUT_MARK` 之前那一行，脚本伪造标记无效；
  `head -c` 之后补 `echo` 空行，否则标记会被拼进最后一行输出（第一版就栽在这）。
- **依赖安装 stamp**：`.pylibs/.stamps/<name>.<reqs_sha8>`，安装成功才落 stamp；
  失败时把 pip 输出尾部 1.5KB 回给用户，而不是让脚本以一个看不懂的 ImportError 失败。

### 15.6 「可编辑内置」的语义与代价（2026-10-29 追加）

初版把内置脚本设成只读（改需求走「复制为新工具」）。用户确认希望**直接编辑内置**，因此改为：

| 字段 | 内置行是否可改 |
|---|---|
| `code` / `parameter_schema` / `requirements` / `description` / `display_name` / `timeout_seconds` / `group_name` / `is_active` | 可改 |
| `name` | 禁止（403）——名字是内置身份，改名会让「恢复默认」与 registry 兜底匹配失效 |
| 删除 | 禁止（可停用） |

优先级：`row.code` 非空时**以库里的为准**（覆盖），为空则回落到代码库定义（`config_from_row`）。
页面对 `source='builtin' and code is not null` 的行显示「已覆盖」徽标。

代价必须显式告知，且已在向导与列表中标明：**平台后续对该内置脚本的修复不再影响这台部署**。
安全阀是 `POST /tools/script/{id}/reset-to-builtin`：把 `code` 置回 NULL、参数/依赖/超时/描述恢复成代码库定义。

迁移层面无需变化：seed 用 `INSERT IGNORE`，升级不会覆盖用户改过的行（保留覆盖），也不会把 `code` 写回 NULL。

### 15.7 外部 review 修复（2026-10-29 追加）

一次独立 review 推翻了初版最重要的假设，记录如下（全部已核实并修复）：

**① 沙箱 Bash 返回的是 JSON，不是文本（P0）。** `workspace_container_mcp.bash()` 返回
`{"ok", "exit_code", "stdout", "stderr"}`，而 `_run_sandbox` 把整段当信封文本解析，标记永远找不到 →
docker/k8s 下每次调用都报「退出码未知」，脚本其实跑成功了。单测替身直接返回裸信封文本，
把错误假设写进了测试。修法：先按 JSON 取 `stdout`/`stderr`/`exit_code`，信封缺失时才用通道退出码；
替身改成真实形状并补成功/失败/超时三种形态。

**② 信封标记会被通道截断吃掉（同一 P0 的另一半）。** 通道只保留 stdout 尾部 100KB，
而 `head -c` 单段上限是 200KB —— 输出一大就先丢头部的退出码标记。修法：单段上限压到 40KB
（`STDOUT_CAPTURE_BYTES`），并加断言 `stdout+stderr 上限 < 100KB`。

**③ 依赖安装 fail-closed。** 原来 `exit_code in (0, None)` 都算成功，解析失败（None）会写 stamp，
后续调用永不重试。改为 `timed_out or exit_code != 0` 即失败。

**④ 降级时脚本不得在宿主机执行（P0）。** 沙箱起不来时 runner 只摘掉 Bash spec，
脚本工具照样绑定 → `sandbox_ws is None` 被当成 local，用户的脚本在平台后端进程里跑。
修法两层：runner 降级分支一并摘掉 `source_type == "script_tool"` 的 spec；
`_bind_script_tool_spec` 增加显式 `sandbox_degraded` 标志并抛错（不再靠 `sandbox_ws is None` 推断部署形态）。

**⑤ 失效引用判定漏了脚本工具（P1，会诱导删配置）。** `resolve_unresolved_tool_names` 只减静态/API/MCP，
引用脚本工具的智能体一律显示「工具已不存在」，用户点「移除失效引用」会把有效配置删掉。
修法：新增 `_active_script_tool_names`（启用行 + 内置兜底，停用优先），表不存在时不抛。

**⑥ 更新接口无法清空字段（P1）。** 逐字段 `if payload.x is not None` 使「清空分组/超时/描述」静默失效。
改用 `model_fields_set` 区分「未传」与「显式 null」。逻辑抽成 `apply_update_fields` 以便单测。

**⑦ 读取上限与结果上限矛盾（P1）。** `file_to_base64` 默认 10MB，而 stdout 上限 100KB →
得到被截断的非法 JSON。默认压到 48KB 并写进说明。

**⑧ 安全文案与实际权限不符（P1）。** local 策略下脚本是后端进程权限（可读环境变量/数据库连接），
原文案只说「可读该目录文件」；docker 下工作区挂的是用户根目录（同账号其他会话可读），
原文案说「只能读写当前会话」。两处都改为如实描述。

**⑨ 前端门控与错误可见性（P1）。** 只读用户能看到会 403 的按钮；保存失败只写页面级红条，
被向导弹窗盖住表现为「点保存没反应」；内置删除按钮只是变灰仍可点。改为 `canSave` 门控、
`reportError` 同时弹 toast、内置删除 `disabled` + 点击守卫；列表与 capabilities 改用
`allSettled`，避免 capabilities 失败拖垮整页。

**⑩ 参数编辑器口径（P1）。** 脚本参数走 stdin、且运行时不注入「系统级参数」，
复用 `ToolParamEditor` 却显示「查询参数」与系统参数编辑区，配了会静默失效。
新增 `scope="script"`：落位标签显示 `stdin`，隐藏系统级参数。

**⑪ MCP 示例代码（P0，同批未提交内容）。** `streamable_http_app()` 未传 `transport_security`，
非 localhost 的 Host 会 421；`MCP_FIXED_TOKEN` 未配置时退化成 `"Bearer "`，空 Bearer 即通过。
改为显式 `TransportSecuritySettings` + 启动期 fail-fast + 比较前判空。

**未修（记录在案）**：超时 3600s 与沙箱内 600s 硬上限的关系（UI 已注明，未改语义）；
`resolve_sandbox_bash_tool` 绕过 `LazySandboxBashNativeTool` 的心跳/注册校验（长脚本可能被 reaper 回收）；
`GET /script` 对任意登录用户返回源码（与 API 工具返回 headers 同口径）；
迁移未执行时 `tools.py` 禁用目录仍会 500。
