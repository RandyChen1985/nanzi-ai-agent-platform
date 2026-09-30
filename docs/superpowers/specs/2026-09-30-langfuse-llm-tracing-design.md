# Langfuse LLM 链路追踪接入设计

## 背景

平台当前没有任何 Langfuse 或 OpenTelemetry 采集代码。排障时的可观测能力是自建的三套：

1. **执行轨迹落库**：`ai_agent_execution_traces`（步骤级，含 `span_id`/`parent_span_id`/`meta_info`）
   与 `ai_agent_execution_history`（轮次级），由 `AuditManager.save_trace_logs()` /
   `save_history()` 写入，触发点 `FinalizeStep`（`app/services/ai/pipeline/steps/finalize_step.py:207`）。
2. **自研 span 机制**：`TraceSpanContext`（`app/services/ai/runtime/agentscope/trace_context.py:15`），
   基于 ContextVar 维护父子栈，是前端「执行过程时间线」的数据源。
3. **单次 LLM 调用明细**：`ModelCallStatsMiddleware`（`app/services/ai/runtime/agentscope/middleware.py:422`）
   在 `on_model_call` 采集 token / 缓存命中 / 工具调用 / 回复文本 / 耗时，写入 Redis List
   `nanzi:{uid}:{conv_id}:model_call_stats`，经 `GET /api/v1/chat/conversation/{id}/model_calls` 读取。

这套自建能力的缺口是明确的：**读不到完整的 LLM 输入**（system prompt + 历史消息全文），
没有跨会话/跨节点的检索与对比，没有模型延迟分布，也没有上游报错的原始报文。

同时有一个尚未启用的现成能力：**AgentScope 2.0.9 自带基于 OpenTelemetry 的
`TracingMiddleware`**（`.venv/.../agentscope/middleware/_tracing/_trace.py:124`），覆盖
`on_reply` / `on_model_call` / `on_acting` 三条生命周期，按 GenAI 语义约定
（`gen_ai.*`）产出 span；其依赖 `opentelemetry-sdk`、`opentelemetry-exporter-otlp` 已随
`agentscope==2.0.9` 安装到环境中。项目内 `grep opentelemetry app/` 零命中，即**能力已在位，只差接入**。

## 目标与边界

目标：

1. 让完整 LLM 链路可追踪：轮次 → agent → LLM 调用 → 工具调用，四层 span 可在 Langfuse 中查阅。
2. Agent 链路与直调链路都要覆盖；直调链路的埋点必须落在**唯一出口**，不改 20+ 个业务调用点。
3. trace 携带平台身份（用户、会话、trace_id、智能体），便于在 Langfuse 中定位到具体用户与会话。
4. 提供全局开关、内容开关与采样率，三者可热改；多节点部署下配置可收敛。
5. 追踪必须严格旁路：不改变任何业务返回值与事件流，不写平台库与 Redis，异常不上抛。
6. Langfuse 不可用、配置非法、SDK 异常时整体降级为 no-op，不影响对话。

明确不做：

- **不做** Embedding 调用追踪（`app/services/ai/embedding_client.py:35` 直连 httpx，语义是向量化而非生成）。
- **不做** Langfuse 的 prompt 管理、人工评分、数据集与实验功能。
- **不做** 平台内 Langfuse 数据阅读页（即不在平台里代理 Langfuse REST API 渲染 trace 详情）；
  平台既有轨迹视图保持不变。**跳转入口已明确不做**（2026-09-30 决定）：Langfuse 只有管理员能登录，
  给普通用户加「在 Langfuse 查看」跳转没有实际意义。
- **不替换** 平台自研时间线：`trace_buffer` / `AgentExecutionStep` / `ai_agent_execution_traces`
  表结构与写入路径一律不动。
- **不落库** 完整 prompt 到平台数据库（避免敏感数据二份落库与保留期管理）。
- **不使用环境变量**：Langfuse 的全部配置只从数据库读（独立表 `langfuse_config`）。
  `app/core/config.py` 不新增任何 `LANGFUSE_*` 字段，根 `env.example` 与
  `docker/env.example` 均不改动。
- **不动 `system_configs` 与「参数配置」Tab**：Langfuse 配置单独建表、单独接口、单独 Tab，
  与 `ConfigService`（`system_configs` + Redis 缓存 + `system_config_history`）完全解耦。
- **不做** 按智能体、按用户粒度的开关（本期只做全局开关）。
- **不修改** `sandbox/`、`k8s_deploy/` 的沙箱镜像相关逻辑。

## 关键决策

| 决策 | 选择 | 依据 |
|---|---|---|
| 接入方式 | 用 Langfuse Python SDK 作为 OTel 后端 | SDK 直接读 `.venv` 中的 wheel 核实：`resource_manager.py:677` 在 provider 为空时调用 `set_tracer_provider`，因此 AgentScope 的 span 会自动流入 Langfuse；协议映射交给官方，避免自建 OTLP 的版本试错 |
| Agent 链路埋点 | 挂 AgentScope 官方 `TracingMiddleware` | 官方已覆盖 reply/model_call/acting，且处理过流式跨 asyncio context 的 detach 问题（其注释引用 issue #2076） |
| 直调链路埋点 | 包装 `AgentScopeLLMHandle.ainvoke/astream` | `app/core/llm/client.py:48/59` 是全部直调调用点的唯一出口 |
| 导出过滤 | **必须**自定义 `should_export_span` | `langfuse/_client/span_filter.py` 的默认过滤是 `is_langfuse_span or is_genai_span or is_known_llm_instrumentor`；AgentScope 的 tracer 名 `agentscope` 不在 `KNOWN_LLM_INSTRUMENTATION_SCOPE_PREFIXES` 白名单内，靠 `gen_ai.*` 属性才被放行，而平台自建根 span 不带 gen_ai 属性会被静默丢弃 |
| 开关/采样/脱敏 | 走导出阶段钩子读实时配置 | `should_export_span` 按 span 调用、`mask_otel_spans` 在导出工作线程按批调用（`_client/span_exporter.py:265`），二者均可读同步快照，实现热改 |
| 依赖 | `langfuse` 锁精确版本 | 照 `requirements.txt` 中 `agentscope==2.0.9` 的先例（该文件已注明禁止无上限以规避版本漂移） |
| 配置存储 | 独立单行表 `langfuse_config` + 独立 Tab | 按产品要求与 `system_configs` 解耦：字段类型化、逐字段校验、独立权限；代价是不复用 `system_config_history` 与配置 Redis 缓存 |
| 密钥存储 | Fernet 加密 + `langfusekey:v1:` 前缀 | 与 `ai_models.api_key` 同一套约定（`app/utils/model_credentials.py:19`），避免明文密钥落库 |

## 方案

### 模块结构

新增 `app/services/ai/observability/`，四个文件，职责单一：

| 文件 | 职责 | 对外接口 |
|---|---|---|
| `config_store.py` | `langfuse_config` 单行表的读写与字段校验（含密钥加密） | `load_config()`、`save_config(payload, changed_by)` |
| `settings.py` | 读取配置并维护进程内**同步快照**（导出钩子在工作线程中运行，不能 await），含密钥解密 | `get_snapshot() -> LangfuseSnapshot`、`refresh_snapshot()`、`start_refresh_task()` |
| `gate.py` | 开关与采样决策；同一 trace 决策恒定 | `should_trace(trace_id: str) -> bool` |
| `span_attributes.py` | 纯函数：构造 trace 级与 generation 级属性；从 AgentScope 响应提取 usage | `trace_attributes(...)`、`generation_attributes(...)`、`extract_usage(...)` |
| `credentials.py` | `langfuse_secret_key` 的加密/解密（版本前缀 + 明文兼容），与 `model_credentials.py` 同构 | `encrypt_langfuse_secret(...)`、`decrypt_langfuse_secret(...)` |
| `manager.py` | 进程级单例：初始化 SDK、注册钩子、重建 client、优雅 flush、整体 no-op 降级 | `init_observability()`、`shutdown_observability()`、`current_client()`、`test_connection()` |

配置快照为不可变 dataclass，字段：`enabled`、`host`、`public_key`、`secret_key`、
`sample_rate`、`capture_content`、`environment`、`release`、`timeout_seconds`、`client_fingerprint`
（由 host/keys/timeout 派生，用于判断是否需要重建 client）。

### 初始化与生命周期

`app/main.py` 的 lifespan 中，在 `AuditService.start_worker()` 之后调用
`init_observability()`；shutdown 阶段调用 `shutdown_observability()`（先 `flush(10s)` 再 shutdown）。

初始化规则：

1. 读快照；`enabled=false` 或 host/keys 任一缺失时**不初始化 SDK**（但 30s 周期的配置刷新
   任务照常启动，否则从 UI 打开的开关永远不会生效）。此时全局 OTel Provider 仍是
   `ProxyTracerProvider`，AgentScope 的 `_check_tracing_enabled()`（`_trace.py:57`）判定为假，
   中间件在 `next_handler` 处短路，连 span 对象都不创建——这是「冷启动即关闭 = 零开销」的依据。
2. **启动期读配置失败不得阻塞启动**（配置现在来自数据库，冷启动时主库可能尚未就绪）：
   初始化读快照失败时记一次 warning 并置为 no-op，由 30s 周期的后台刷新任务在读到有效
   配置后**自动完成初始化**，无需重启。这一条是「配置全部落库」带来的新增要求。
3. 初始化失败（SDK 未装、参数非法、构造抛错）时捕获异常，记一次 error 日志，
   并将 manager 置为 no-op 状态，后续所有入口直接返回。
3. 构造客户端时传入自定义 `should_export_span` 与 `mask_otel_spans`（见下），
   并把 `environment` / `release` 交由 SDK 写入 resource attributes。

### Agent 链路接入

在 `build_runtime_middlewares()`（`app/services/ai/runtime/agentscope/agent_runtime.py:63`）
返回的中间件列表**末尾**追加两个：

```
[ToolPermissionMiddleware, BashSandboxParentLinkMiddleware, ModelCallStatsMiddleware,
 TracingMiddleware(), LangfuseContextMiddleware(user_id, conversation_id, agent_name, trace_id)]
```

顺序依据：AgentScope 的 `execute_chain`（`agentscope/agent/_agent.py:921-950`）从索引 0 开始
向外层递归，即 `middlewares[0]` 最外层。`TracingMiddleware` 放在末尾使其成为内层，因此它
创建的 span 在更内层可见；`LangfuseContextMiddleware` 再放在其后，即可在 `on_reply` 中通过
`otel_trace.get_current_span()` 拿到该 span 并补充平台身份属性。

`LangfuseContextMiddleware` 同时是**父子关系断裂时的兜底**：即使 SSE producer/consumer
的 task 边界导致 span 挂不到根 span 下，agent span 自身仍带 `user.id` / `session.id` /
`langfuse.trace.metadata`，不会丢失身份。

### 轮次根 span

在 `AgentService.chat_completion_stream`（`app/services/ai/agent_service.py:1085` 生成
`trace_id` 处）创建根 span：

```
[span] chat.turn
  name                      : "<agent_name> 对话"
  user.id                   : 平台 user_id
  session.id                : conversation_id
  langfuse.trace.name       : "<agent_name> 对话"
  langfuse.trace.metadata.* : trace_id / agent_id / conversation_id / engine_type
  gen_ai.conversation.id    : conversation_id
```

创建纪律：仅当 `should_trace(trace_id)` 为真时创建；span 的 attach/detach 沿用 AgentScope
官方中间件的做法（在每次 `anext` 前后 attach/detach），避免流式 async generator 被其他
task 关闭时 token 失效。

HITL 授权恢复与外部队列恢复（`app/api/v1/endpoints/chat.py:2027` /
`resume_agentscope_external_execution_stream`）复用同一段恢复执行链，因此根 span 的创建
收敛到一个 helper（如 `observability/turn_span(...)` 上下文管理器），在两个入口各调用一次，
不重复实现。

未采样时通过 contextvar 标记「本轮不追踪」，使 `LangfuseContextMiddleware` 与二期新增的
直调埋点一并跳过，避免出现「根 span 被采样掉、子 span 自成半个 trace」的情况。

### 直调链路统一出口（二期）

在 `AgentScopeLLMHandle.ainvoke` / `astream`（`app/core/llm/client.py:48/59`）外层包装：

- `ainvoke`：`try/finally` 包裹，正常返回时写 output 属性，异常时记录 `record_exception`
  并设置 error 状态后**原样抛出**。
- `astream`：包装 async generator，逐 chunk 原样 `yield`；累积末个 chunk 的 usage 与文本，
  在生成器结束时写入 output 属性；异常原样透传。

覆盖范围（一次改动全部生效）：`router_service` 路由分类、`intent_service` 意图识别、
`conversation_summarizer` 摘要、`hallucination_evaluator` 评估、`memory_index_service`、
`prompt_ops/prompt_service`、`saved_report_analysis_service`、`saved_report_digest_service`、
`openclaw_executor`、`ragflow` 端点、`portal/models` 端点、`multi_agent_orchestrator` 合成。

### span 层级与属性契约

自建 span 统一使用 tracer 名 `nanzi.llm`（AgentScope 用的是 `agentscope`），两者都在导出
白名单内。

```
[span] chat.turn                          user.id / session.id / langfuse.trace.*
 ├─ [agent]      invoke_agent <name>       ← TracingMiddleware.on_reply
 │   ├─ [generation] chat <model>          ← on_model_call（gen_ai.usage.* / input.messages / output.messages）
 │   └─ [tool]       execute_tool <name>   ← on_acting（gen_ai.tool.call.arguments / result）
 ├─ [generation] chat <model>              ← 直调出口（router / intent / summary / eval / synthesis …）
 └─ [span]       sub_agent_call <name>     ← 委派子智能体
```

- 多智能体并发：`multi_agent_orchestrator.py:204` 用 `asyncio.create_task` 派生，
  contextvar 在任务创建时复制，因此各专家 executor 的 span 会挂到同一个 `chat.turn` 下成为兄弟节点。
- 跨轮次、跨节点的会话串联依赖 `session.id = conversation_id`，在 Langfuse 侧按 session 聚合。
- 直调链路使用与 AgentScope 相同的 `gen_ai.*` 属性名，保证被同一套映射识别为 generation。

### 采样与开关

采样在 `should_export_span` 与根 span 创建处统一使用 `gate.should_trace(trace_id)`：

```
sha256(f"{trace_id}:{sample_rate}") 的前 8 字节折算为 [0,1) 的稳定值 < sample_rate
```

同一 `trace_id` 决策恒定，因此不会出现「同一 trace 只采到一半 span」。注意不叠加 SDK 的
`sample_rate` 参数（那是 provider 级 sampler，与本钩子口径重复）；采样只走本钩子一处。

### 内容脱敏

`mask_otel_spans` 在导出阶段按批执行；`capture_content=false` 时删除下列内容属性、
保留结构与元数据（模型、token、耗时、工具名、错误）：

- `gen_ai.input.messages`
- `gen_ai.output.messages`
- `gen_ai.tool.call.arguments`
- `gen_ai.tool.call.result`
- `langfuse.observation.input` / `langfuse.observation.output`

保留 `gen_ai.tool.name` / `gen_ai.tool.definitions`（工具清单与 schema 属结构信息，不含业务数据）。

### 导出过滤

自定义 `should_export_span(span)` 必须**同时**满足：

1. 快照 `enabled=true`；
2. `span.instrumentation_scope.name` 在允许集合内（`{"agentscope", "nanzi.llm"}`），
   或该 span 带 `gen_ai.*` 属性；
3. `gate.should_trace(span 的 trace_id)` 为真。

这样既覆盖 AgentScope 与自建 span，又避免同进程内其他库的 OTel instrumentation
（若将来启用）被顺带收集。

## 配置

配置**不复用 `system_configs`**，也**不进「参数配置」Tab**：独立建表、独立接口、独立 Tab。
理由与代价：

- 好处：字段类型化（布尔/小数/超时是真正的类型，不是字符串）、单行配置读写简单、
  密钥可只在这一处加密、不必把 Langfuse 的语义塞进通用键值组的排序与白名单里。
- 代价：不复用 `system_config_history` 的变更历史，改为记录 `updated_by` / `updated_at`
  并依赖 HTTP 层已有的审计日志；也不复用 `ConfigService` 的 Redis 缓存（每节点 30s 读一行，
  无压力，且避免缓存过期导致的状态不一致）。

### 数据表

新增 `langfuse_config`，**单行配置表**（固定 `id = 1`，`INSERT ... ON CONFLICT/ON DUPLICATE`
保证只有一行）：

| 列 | MySQL 类型 | PG 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | `INT` | `INTEGER` | 1 | 主键，固定 1 |
| `enabled` | `TINYINT(1)` | `BOOLEAN` | 0 / `FALSE` | 总开关 |
| `host` | `VARCHAR(512)` | `VARCHAR(512)` | NULL | 如 `http://langfuse:3000` |
| `public_key` | `VARCHAR(255)` | `VARCHAR(255)` | NULL | 明文（非机密） |
| `secret_key` | `TEXT` | `TEXT` | NULL | **Fernet 密文**，前缀 `langfusekey:v1:` |
| `sample_rate` | `DECIMAL(4,3)` | `NUMERIC(4,3)` | 1.000 | 0.000~1.000 |
| `capture_content` | `TINYINT(1)` | `BOOLEAN` | 1 / `TRUE` | 内容开关 |
| `environment` | `VARCHAR(64)` | `VARCHAR(64)` | NULL | 空则不写该属性 |
| `release` | `VARCHAR(64)` | `VARCHAR(64)` | NULL | 版本对比用 |
| `timeout_seconds` | `INT` | `INTEGER` | 5 | 导出超时 |
| `trace_url_template` | `VARCHAR(512)` | `VARCHAR(512)` | NULL | **已废弃**：跳转入口取消，代码不再读写该列（迁移不可变，故列保留） |
| `updated_by` | `VARCHAR(64)` | `VARCHAR(64)` | NULL | 最后修改人 |
| `created_at` | `DATETIME` | `TIMESTAMP` | 当前时间 | |
| `updated_at` | `DATETIME` | `TIMESTAMP` | 当前时间（MySQL 自动更新） | |

选单行类型化表而非「键值对 + `config_json`」，是因为这些字段需要**逐字段校验与掩码**，
单行表在写路径上直接做类型校验比解析 JSON 更可靠。

### 访问层

在 observability 包内新增 `config_store.py` 负责这张表的读写（不与 `ConfigService` 耦合）：

- `load_config() -> LangfuseConfigRow`：读 `id=1` 行，缺行时返回内置默认；
- `save_config(payload, changed_by)`：字段校验 → 密钥加密 → upsert 单行 → 返回新配置；
- 密钥加解密委托给 `credentials.py`；本模块不参与 span 生命周期。

`settings.py` 的职责收敛为：调用 `config_store.load_config()` → 解密 → 构造不可变快照
`LangfuseSnapshot`。因此**完全不修改 `app/services/config_service.py`**，也**没有循环依赖**——
这也是独立表带来的直接好处。

### 取值与校验

- 唯一来源是 `langfuse_config` 表；缺行或字段为空时用内置默认。
- 写入校验（在 `config_store.save_config()` 内，非法值直接拒绝并返回明确错误）：
  `sample_rate ∈ [0, 1]`；`host` 为空或以 `http://` / `https://` 开头；
  `timeout_seconds` 为正整数；`environment` / `release` 长度上限 64。
- 运行期在解析出有效值后再判一次：`enabled=true` 但 host / public_key / secret_key
  任一为空（或密钥无法解密）时**不初始化 SDK**，状态置 `not_configured` / `degraded`，
  记一次 warning，并在 Tab 内的状态卡片上暴露原因（避免「开关是开的但没有任何数据」的黑箱）。

### 密钥存储

`secret_key` **加密后存库**，沿用 `ai_models.api_key` 的既有约定
（`app/utils/model_credentials.py:19`：Fernet 加密 + 版本前缀 + 读取时兼容历史明文）：

- 写入：`config_store.save_config()` 内用 `get_api_key_manager().encrypt_api_key()` 加密，
  加前缀 `langfusekey:v1:`；提交空字符串表示「清空」，提交掩码占位表示「不修改」（见下）。
- 读取：`settings.py` 解密，前缀缺失时按明文返回（兼容手工写库）。
- 解密失败：明确报错（与 `ModelCredentialError` 同思路），状态置 `degraded` 并在 UI 提示
  「密钥密文无法解密，请检查 ENCRYPTION_KEY 或重新录入」，不静默降级为无效配置。

接口不回传密钥明文，只回传 `has_secret_key: bool` 与掩码状态；表单提交时若该字段未被改动
则不回传该字段，后端保持原值不动——避免把掩码字符串写成真密钥。

### API 端点

新增 `app/api/portal/endpoints/langfuse.py`，在 `app/api/portal/api.py` 注册为
`/system/langfuse`（放在「7. 系统配置」附近）：

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/portal/system/langfuse` | `menu:system:config` | 读取配置（不含密钥明文）+ 运行期状态 |
| PUT | `/api/portal/system/langfuse` | `element:system:langfuse_save` | 保存配置（`updated_by` 取当前用户） |
| POST | `/api/portal/system/langfuse/test` | `element:system:langfuse_save` | 用当前有效配置做真实连通性探测 |

不复用 `element:system:config_save`，而是新增 `element:system:langfuse_save`，遵守
「`menu:*` 控菜单、`element:*` 控功能」的既有约定（`AGENTS.md`）。新增权限需要两处登记：
迁移里向 `ai_agent_resource_permissions` 插入一行 `('element', 'element:system:langfuse_save', ...)`
（仿 `db-prod/V30-register_ui_resources.sql:45`），以及前端权限目录
`frontend/src/constants/permissions.ts:145` 附近加一行标签。注意：非 admin 角色需要显式授权
才会看到保存按钮（admin 走既有 bypass 逻辑）。

### 热改与多节点收敛

| 变更项 | 生效方式 | 收敛窗口 |
|---|---|---|
| `enabled` / `sample_rate` / `capture_content` | 导出钩子与根 span 创建处读实时快照 | ≈30s |
| `environment` / `release` / URL 模板 | 同上（span 属性 / 前端链接） | ≈30s |
| `host` / `public_key` / `secret_key` / `timeout` | 检测 `client_fingerprint` 变化 → `shutdown()` 旧 client → 构造新 client | ≈30s，失败则退化为重启生效 |

快照刷新：manager 启动一个后台协程，每 30s 调 `config_store.load_config()` 刷新快照，
写入由 `threading.Lock` 保护的引用。初始化为关闭态时该任务同样启动（否则 UI 打开的开关不生效）。

热重建可行性依据：`BatchSpanProcessor` 的 `emit()` 在 `_shutdown` 为真时直接忽略
（`opentelemetry/sdk/_shared_internal/__init__.py:198-201`），因此旧 client `shutdown()`
后不会重复上报。实现阶段需实测确认无重复写入与线程泄漏；若不可靠，则降级为
「连接参数变更需重启」并在 UI 上标注。

### 前端 UI：独立 Tab

在 `frontend/src/views/SystemConfig.vue` 的 Tab 栏新增一个 Tab（位置放在「日志管理」之后），
内容按现有约定委托给子组件（与 `ModelRegistry` / `ToolRegistry` 一致）：

- `activeTab` 联合类型加 `'langfuse'`（:53），Tab 按钮加在「日志管理」按钮之后（:3032 附近），
  图标用 `@heroicons/vue` 的 `ChartBarSquareIcon`（已确认存在于
  `node_modules/@heroicons/vue/24/outline/ChartBarSquareIcon.js`）；
- 新增 `frontend/src/components/system/LangfuseConfig.vue`，结构照 `ModelRegistry.vue`：
  开关、host、public_key、secret_key（掩码输入 + 「已配置」提示）、采样率、内容开关、
  environment、release、超时、URL 模板，加保存按钮（按 `element:system:langfuse_save` 控制可用性）；
- 顶部状态卡片展示运行期状态：`not_configured` / `disabled` / `enabled` / `degraded`、
  上次导出成功时间与失败原因摘要、当前进程是否已注册 TracerProvider；
- 「测试连接」按钮调用 `POST /api/portal/system/langfuse/test`，把 DNS / 认证失败 / 超时 /
  HTTP 状态码等具体原因显示出来。

Tab 的可见性与「参数配置」一致（同属 `menu:system:config` 页面），不做额外菜单权限。

**跳转入口已取消**（2026-09-30）：原计划在对话「执行过程」面板加「在 Langfuse 查看」并由
`trace_url_template` 渲染 URL，但 Langfuse 仅管理员可登录，普通用户拿到链接也进不去，故不做；
对应配置项 `trace_url_template` 随之废弃（DB 列保留，代码不再读写）。

替代能力：链路自检（见 `2026-09-30-langfuse-selfcheck-design.md`）——管理员在配置页一键发测试
trace 并确认是否真的到达 Langfuse。

## 错误处理与降级

| 场景 | 行为 |
|---|---|
| SDK 未安装 / 初始化抛错 | 整体 no-op，error 日志只记一次，对话不受影响 |
| 运行期读到非法配置 | 视为 disabled，记 warning |
| Langfuse 不可达 / 导出超时 | warning（同一错误限流，避免日志风暴），span 丢弃 |
| 导出队列满（默认 2048） | OTel 直接丢 span，不反压业务 |
| 快照刷新失败 | 保留上一次快照，不中断刷新任务 |
| client 热重建失败 | 回退为「重启生效」，UI 状态显示 `degraded` |
| 优雅停机 flush 超时 | 记 warning 后继续退出 |

## 性能

- 关闭且冷启动：AgentScope 中间件每 hook 一次 `isinstance` 检查，开销可忽略。
- 开启：每轮新增 1 个根 span；每次 agent 轮新增 1 个 agent span；每次 LLM 调用新增
  1 个 generation span；每次工具执行新增 1 个 tool span。主要开销是属性提取时的
  消息 JSON 序列化，与 `ModelCallStatsMiddleware` 现有的 `response_text` / `tool_calls` /
  上下文拆分提取同量级。
- 导出走 `BatchSpanProcessor` 后台线程（默认 512 条 / 5s，队列 2048，
  见 `opentelemetry/sdk/trace/export/__init__.py:41-44`），不在请求关键路径；
  过载时丢 span 而不阻塞业务。
- **验收口径**：用平台现成的 `ExecutionPerformanceTracker`
  （`app/services/ai/runtime/execution_observability.py:51`）做开/关对照，要求 TTFT 与
  总耗时增量 < 1%；不达标则不进入默认开启状态。

## 多节点与部署

- SDK 进程内无状态、无 leader 选举；一轮对话（含多智能体与子 agent 委派）在单进程内
  产生完整 trace，不存在跨节点半截 trace。
- 跨轮次、跨 pod 的会话串联依赖 `session.id`，在 Langfuse 侧按 session 聚合。
- 配置存储共享（`langfuse_config` 单行表在平台主库中，各节点读同一行），热改项约 30s 内收敛。
- 优雅停机必须 flush，避免丢最后一批 span；K8s
  `terminationGracePeriodSeconds` 建议 ≥ 10s。
- 容量：N 个节点 = 对 Langfuse 的写入量 ×N，采样率是总量闸门；自建 Langfuse 的容量
  与数据保留策略由部署侧确认。

## 数据库迁移

新增两个文件，双库成对、幂等：

- `db-prod/V161-create-langfuse-config.sql`（MySQL）
- `db-prod-pg/V62-create-langfuse-config.sql`（PG；`enabled` / `capture_content` 用
  `BOOLEAN` + `FALSE`/`TRUE`，数值列用 `NUMERIC`）

每个文件包含三部分：

1. `CREATE TABLE IF NOT EXISTS langfuse_config (...)`，列定义见「数据表」；
2. `INSERT` 一行默认配置（`id=1`，MySQL `INSERT IGNORE` / `ON DUPLICATE KEY UPDATE`，
   PG `ON CONFLICT (id) DO NOTHING`）——开关默认关闭，其余留空；
3. 注册新权限元素：向 `ai_agent_resource_permissions` 插入
   `('element', 'element:system:langfuse_save', ...)`（仿 `db-prod/V30-register_ui_resources.sql:45`，
   MySQL `INSERT IGNORE` / PG `ON CONFLICT DO NOTHING`）。

文件头部按既有规范写明变更原因、需求背景、创建时间、创建人，并注明
「该脚本仅做生成，不由 Agent 自动执行，由用户手动导入」。由用户在控制台执行。

## 测试

后端单测（`tests/services/ai/observability/`）：

1. `gate.should_trace`：采样率 0 / 1 / 0.5 下的 trace 粒度稳定性（同一 trace_id 恒定，
   不同 trace_id 分布合理）。
2. `mask_otel_spans`：`capture_content=false` 时内容属性被删、结构与 token 属性保留；
   `true` 时返回 `None`（不改动）。
3. `should_export_span`：放行 `agentscope` 与自建 scope，拒绝未知 scope；`enabled=false` 全拒。
4. 属性构造：`user.id` / `session.id` / `langfuse.trace.name` / metadata 正确。
5. 降级：SDK 构造抛错时 `init_observability()` 不抛异常，且后续入口全部 no-op。
6. **直调出口透传（二期）**：包装后的 `ainvoke` 与 `astream` 返回值/异常与包装前完全一致
   （chunk 序列相同、异常类型相同），这是最关键的回归保护。
7. 配置校验：`config_store.save_config()` 拒绝非法 `sample_rate`（>1 或 <0）/ 非法 host /
   非正整数超时，且拒绝时不写库。
8. 密钥加密往返：写入后库中为 `langfusekey:v1:` 前缀密文；读取可还原明文；
   无前缀的历史明文按明文兼容；密文损坏（换 `ENCRYPTION_KEY`）时抛明确错误并使状态置
   `degraded`，而不是被当成空密钥静默跳过；不提交密钥字段时原值不变（不会被写成掩码串）。
9. 接口契约：GET 不返回密钥明文而返回 `has_secret_key`；PUT 由 `element:system:langfuse_save`
   守卫（无权限时 403）；`/test` 在配置不完整时返回可读的失败原因而不是 500。

契约与迁移测试：

- `tests/test_langfuse_migration_contract.py`：双库迁移均包含建表、默认行、权限行三部分，
  且 PG 侧布尔列用 `FALSE`/`TRUE`、数值列用 `NUMERIC`（对齐既有 PG 迁移守卫测试的口径）。
- `tests/frontend/test_langfuse_config_contract.py`：源码断言 `SystemConfig.vue` 含
  `'langfuse'` Tab（联合类型、按钮、面板）且面板委托给
  `components/system/LangfuseConfig.vue`，并断言 `permissions.ts` 已登记
  `element:system:langfuse_save`。

手工验收：开启后跑一轮真实对话，在 Langfuse 侧确认
`chat.turn → invoke_agent → chat <model> → execute_tool` 层级、`session.id` 与 `user.id`
正确、内容开关关闭后输入输出为空而结构与 token 仍在。

测试完成后必须同步更新 `tests/CHECKLIST.md`。

## 分期与回滚

一期：observability 模块、启动初始化、Agent 链路接入、轮次根 span、独立表与接口、独立 Tab
（表单 + 状态卡片 + 测试连接）、全套测试。一期单独出一份实施计划。

二期：直调链路统一出口埋点（覆盖 20+ 调用点）。**对话页 Langfuse 跳转入口已明确不做**
（2026-09-30 决定）。二期在一期验收通过后另出一份实施计划。

补充实施（2026-09-30）：链路端到端自检（导出健康度 + 走真实 OTel 管线的自检 + 反查），
见 `2026-09-30-langfuse-selfcheck-design.md`。

回滚：把总开关（`langfuse_config.enabled`）置 `false` 即刻停止导出（根 span 也不再创建）；
彻底回滚只需回退代码，数据库侧只多了一张 `langfuse_config` 表与一行默认配置，
不删也无副作用。

## 风险与待验证项

1. **Langfuse 深链格式**：具体路径随部署版本不同，且本次调研环境无法访问 Langfuse 文档站
   （web 抓取被沙箱拦截）。因此 URL 采用可配模板而非写死，实施时按实际部署版本确认。
2. **跨任务父子关系**：SSE producer task 与 pipeline 内的 `asyncio.create_task` 之间，
   OTel 上下文能否稳定传递需以真实 trace 验证；若断裂，则在任务创建处显式传
   `otel_context`，`LangfuseContextMiddleware` 作为身份兜底。
3. **client 热重建**：旧 processor `shutdown()` 后不再接收 span 已由源码确认。
   **实测补充**：重建本身约 6ms，线程与内存无异常。但实测同时发现一个更严重的隐患——
   `flush()` 是同步的 `force_flush()`，而重建链路跑在事件循环线程里，会阻塞整个
   事件循环，详见第 9 条（已修复）。
4. **trace 级属性来源**：`user.id` / `session.id` 由 SDK 从根 span（app root）推导，
   非根 span 上的同名属性是否会被合并需实测确认。
5. **Langfuse 版本漂移**：`requirements.txt` 锁精确版本并在注释中写明升级前需核对
   本设计引用的属性名与钩子行为（`_client/attributes.py`、`span_filter.py`、`span_exporter.py`）。
6. **上游 OTLP 行为差异**：Langfuse v3 与 v4 对 GenAI 语义约定的映射不同（社区已出现
   「input/output 为空」的反馈）。一期验收必须包含「输入输出可见」这一条，作为阻断项。
7. **密钥与 `ENCRYPTION_KEY` 绑定**：`secret_key` 密文依赖平台 `ENCRYPTION_KEY`，
   与 `ai_models.api_key` 同命运——主库备份或迁移时若丢失 `ENCRYPTION_KEY`，需在独立 Tab 里
   重新录入 Langfuse 密钥。
8. **独立表没有配置变更历史**：不复用 `system_config_history`，变更追溯依赖
   `langfuse_config.updated_by` / `updated_at` 与 HTTP 层已有的访问审计日志
   （`AccessLogMiddleware` 会记录 PUT 的请求体与响应）。若后续要求完整字段级历史，
   再补一张 `langfuse_config_history` 表即可，属增量改动。
9. **重建 client 时的同步 flush 会阻塞事件循环（已修复）**：`client.flush()` 内部是同步的
   `tracer_provider.force_flush()`，会等导出线程把队列发完；Langfuse 不可达时最长等一个
   `timeout_seconds`（默认 5s）。而重建链路「配置刷新 → 快照监听器 → `_ensure_client`」
   整条跑在**事件循环线程**里，所以「Langfuse 挂掉 + 有人改配置」（或 30s 刷新循环发现
   指纹变化）会让事件循环停顿，**连带卡住正在流式输出的对话**——这违背「追踪只是旁路」。
   修复：`_teardown_client_in_background` 用 daemon 线程关闭被替换的 client。取舍是进程被
   强杀时可能丢最后一批 span，但正常关闭路径（`shutdown_observability` 的 `to_thread`）
   仍会 `await` 完成，不丢数据。回归测试以「模拟 flush 慢 1s，断言重建 <0.5s 返回、
   且旧 client 确实被关闭」锁定该约束。
10. **不可达场景实测数据（供容量与部署参考）**：host 指向必定连不上的地址
   （`timeout_seconds=2`）时——关闭追踪 300 轮「建根 span + 结束」共 0.1ms；开启追踪
   300 轮共 7.9ms，即**每轮对话额外约 26.5 微秒**；配置变更触发重建 5.9ms；进程关闭时
   flush 约 1.0s（在等导出超时，属预期，K8s 需留足 grace period）。此外 `should_export_span`
   经 `inspect` 确认由 SDK 在 `on_end` 中**于请求线程上同步调用**，因此它必须无 IO——
   现实现只读内存快照 + sha256 采样，其开销已包含在上述 26.5 微秒内。
