# Langfuse 链路自检与导出健康度：设计文档

- 状态：待实施
- 创建时间：2026-09-30
- 前置：一期《Langfuse LLM 链路追踪接入》已交付（提交 `71011496`）
- 相关文档：`docs/superpowers/specs/2026-09-30-langfuse-llm-tracing-design.md`

## 1. 背景与目标

一期把 Agent 整轮对话上报到了 Langfuse，但**全部验证都是单元/契约级的、网络被 mock**。以下假设至今没有真机证据：

| 未验证项 | 风险等级 |
|---|---|
| span 是否真的送达 Langfuse | 最高——若不通，一期等于没生效 |
| `input`/`output` 是否可见 | 高——一期设计已将其标为「阻断项」（Langfuse v3/v4 对 GenAI 语义映射不同） |
| 层级是否正确（`chat.turn` → 模型/工具 span） | 中——依赖 OTel 上下文传递 |
| 导出失败时平台是否知情 | 高——**当前完全静默**（一期因 SDK 无导出回调而砍掉了 `langfuse_last_export`） |

**目标**：在系统设置页提供「发送测试 trace」自检，并对真实业务 span 的导出做持续观测，把上述不确定性变成可当场判定的结论。

## 2. 范围

### 做

1. **导出健康度**：观测真实业务 span 的导出成败，常驻显示在状态卡片。
2. **链路自检**：一条走**真实 OTel 管线**的测试 trace，当场返回导出结果（成功时通常数百毫秒内；导出失败时最坏等一个 `timeout_seconds`），并可确认是否已在 Langfuse 可查。
3. **顺带清理**：移除一期遗留的死配置 `trace_url_template`（详见第 8 节）。

### 不做（已决策）

| 项 | 理由 |
|---|---|
| 对话页「在 Langfuse 查看」跳转入口 | **已决定取消**——Langfuse 仅管理员可登录，平台普通用户点过去只会看到无权访问，跳转无意义。连带取消了「记录 Langfuse 32 位 trace id 并落库」的前置工作与潜在迁移 |
| 直调链路统一出口埋点 | 属另一期工作，待链路验证稳定后另立设计 |
| 自动关闭总开关 | 导出失败时只提示、不自动降级，避免误判导致追踪静默停止 |
| 健康度持久化 | 进程内状态即可；多实例部署下各实例本就独立，无需落库 |
| 删除自检产生的 trace | 保留在 Langfuse 中便于人工核对（名字固定为 `nanzi.selfcheck`） |
| 用未保存的草稿值自检 | 自检必须基于真实生效的 client；「保存前验证」由「测试连接」承担 |

## 3. 架构

新增三个模块，各自单一职责、可独立测试：

```
app/services/ai/observability/
├── export_health.py   导出健康度状态（线程安全，进程内）
├── exporter.py        包装 OTLP exporter，把每次导出结果写入健康度
└── selfcheck.py       走真实 OTel 管线发出自检 trace
```

### 3.1 `export_health.py`

进程内的健康度状态。**写入方是 OTel 导出线程，读取方是请求线程**，因此必须线程安全。

```python
@dataclass(frozen=True)
class ExportHealthSnapshot:
    last_success_at: Optional[datetime]
    last_failure_at: Optional[datetime]
    last_error: Optional[str]        # 最近一次失败原因（截断到 500 字符）
    success_count: int
    failure_count: int
    consecutive_failures: int

def record_success() -> None: ...                    # 导出成功：清零连续失败
def record_failure(error: BaseException) -> None: ... # 导出失败：累加连续失败
def snapshot() -> ExportHealthSnapshot: ...
def reset_for_tests() -> None: ...
```

约束：`record_*` 必须**永不抛异常**（它跑在导出线程上，抛异常会污染导出）。

### 3.2 `exporter.py`

```python
class ObservableSpanExporter(SpanExporter):
    """包装内层 exporter，记录每次导出的结果。"""
    def export(self, spans) -> SpanExportResult:
        # 调内层；SUCCESS → record_success()；FAILURE/异常 → record_failure() 后
        # 返回 FAILURE（绝不把异常抛给 OTel 导出线程）
    def shutdown(self) -> None: ...   # 委托内层，同样吞异常

def build_span_exporter(snapshot) -> SpanExporter:
    """构造与 Langfuse SDK 默认参数一致的 OTLP exporter，并包装。"""
```

**必须与 SDK 默认构造完全一致**。经核对 `langfuse==4.15.6` 的 `LangfuseSpanProcessor.__init__`，其默认构造为：

```python
basic_auth_header = "Basic " + base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
headers = {
    "Authorization": basic_auth_header,
    "x-langfuse-sdk-name": "python",
    "x-langfuse-sdk-version": <langfuse 版本>,
    "x-langfuse-public-key": public_key,
}
endpoint = f"{host}/api/public/otel/v1/traces"   # 可被 LANGFUSE_OTEL_TRACES_EXPORT_PATH 覆盖
OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=timeout_seconds)
```

我们把该 exporter 作为 SDK 的公开参数 `span_exporter` 传入。SDK 随后会再包一层 `LangfuseTransformingSpanExporter`，因此我们的 `export()` 收到的是**已完成 Langfuse 转换、即将发出的 span**——正是要观测的对象。

> **风险**：这里复刻了 SDK 内部逻辑，存在版本漂移风险。缓解见第 5 节「防漂移」。

### 3.3 `selfcheck.py`

```python
@dataclass(frozen=True)
class SelfCheckResult:
    ok: bool
    trace_id: Optional[str]      # 32 位十六进制，供层 2 反查
    export_confirmed: bool       # 层 1：flush 期间是否观察到成功导出
    message: str

async def run_selfcheck() -> SelfCheckResult: ...
```

执行流程：

1. **前置检查**：`is_tracing_enabled()` 为假 → 直接返回失败，提示「请先保存并启用追踪」。
2. **记录基线**：读 `export_health.snapshot()` 的成功/失败计数。
3. **建 span**：用与业务相同的全局 tracer（`nanzi.llm`）建根 span `nanzi.selfcheck`，并建**一个子 span**（顺带验证父子层级/上下文传递）、结束子 span。
4. **打标记**：根 span 带 `langfuse.trace.name = "nanzi.selfcheck"`、`langfuse.trace.metadata.nanzi_selfcheck = true`，以及平台版本、实例标识与触发时间；这些元数据同时用于在 Langfuse 里辨识该 trace。
5. **结束根 span**，读取其 `trace_id` 并转成 32 位十六进制。
6. **等待导出**：`await asyncio.to_thread(client.flush)`。
7. **判定层 1**：对比步骤 2 的基线——成功计数增加 → `export_confirmed=True`；仅失败计数增加 → 取 `last_error` 作为失败原因；两者都没变 → 视为「未观察到导出」（可能被采样丢弃或队列异常）。
8. 返回结果。

**必须绕过采样**：若 `sample_rate < 1`，自检 trace 有概率被采样闸拦下，表现为「自检失败」而链路其实正常。做法是在 `should_export_span` 中识别第 4 步的标记并直接放行（白名单那关本就能过，因为用的是同一个 tracer）。

**`flush()` 必须线程化**：它是同步的 `force_flush()`，会让出最长达一个 `timeout_seconds`。直接 `await` 调用会阻塞事件循环——这正是 `90eb309a` 修复过的同类问题，绝不能在新增代码里重演。

### 3.4 数据流

**真实业务 span 的健康度（持续）**

```
任一 span.end()
  → should_export_span（白名单 + 采样）
  → OTel 队列
  → 导出线程 → ObservableSpanExporter.export()
      ├ SUCCESS → export_health.record_success()
      └ FAILURE → export_health.record_failure(err)
  → 状态卡片读取 export_health.snapshot()
```

**自检（按需触发）**

```
管理员点「发送测试 trace」
  → POST /system/langfuse/selfcheck
      → run_selfcheck()：建 span → flush → 对比基线 → 层 1 结论
      → 返回 { trace_id, export_confirmed, message }
  → 前端每 3 秒轮询 GET /system/langfuse/selfcheck/{trace_id}（最多 60 秒）
      → client.async_api.trace.get(trace_id)
          ├ 查到 → 「✅ 已到达 Langfuse」
          ├ NotFoundError → 继续轮询
          └ 其它异常 → 「查询失败」并停止轮询
  → 超时 → 「已导出，但 Langfuse 尚未可查（摄取延迟），可稍后手动刷新」
```

层 2 采用轮询而非固定等待，符合 SDK 官方对该行为的建议（其 `api` 属性文档明确写着：摄取异步、读取可能抛 `NotFoundError`，应「retry with a deadline」而不是固定 sleep）。

### 3.5 接口（`app/api/portal/endpoints/langfuse.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/system/langfuse/selfcheck` | 触发自检，返回层 1 结果与 `trace_id` |
| `GET` | `/system/langfuse/selfcheck/{trace_id}` | 层 2 反查；返回 `{found, message}`，区分「未查到」与「查询失败」 |
| `GET` | `/system/langfuse` | 响应中新增 `export_health` 字段 |

- 权限：**两个 `selfcheck` 端点**复用 `element:system:langfuse_save`（与「保存配置」同属对 Langfuse 的写操作）；`GET ""` 仍沿用原读权限 `menu:system:config`。**不需要新权限元素、不需要迁移**。
- 自检端点必须校验 `trace_id` 形如 32 位十六进制，避免把任意字符串拼进外部 API 调用。

### 3.6 前端（`LangfuseConfig.vue`）

- 状态卡片新增「导出健康度」区块：最近成功时间、连续失败次数、最近错误；无数据时显示「尚未观察到导出」。区块标注**「进程内」**，因为多实例部署下各实例独立。
- 新增「发送测试 trace」按钮，仅当追踪已启用且账号有 `element:system:langfuse_save` 时显示。
- 点击后展示层 1 结论，并自动开始层 2 轮询（每 3 秒、最多 60 秒）；**组件卸载时必须清除定时器**，避免离开页面后继续打接口。
- 轮询结束（查到/失败/超时）后停止并给出明确文案。

## 4. 错误处理

| 情形 | 行为 |
|---|---|
| 追踪未启用 | 自检按钮不可用；接口层再兜一层，返回明确提示 |
| `flush()` 抛异常 | 捕获 → 层 1 记为失败并附带异常摘要，不影响接口返回 |
| 导出失败 | `record_failure` 累加连续失败；状态卡片显示最近错误 |
| 查询 API 抛 `NotFoundError` | 视为「尚未可查」，继续轮询 |
| 查询 API 抛其它异常（鉴权/网络） | 视为「查询失败」，**立即停止轮询**并显示原因（与「未查到」严格区分） |
| 层 1 未观察到导出 | 明确提示可能被采样丢弃或队列异常 |
| 自检过程中任何异常 | 都不影响对话（沿袭一期原则：追踪是旁路） |

## 5. 关键技术点与风险

1. **exporter 参数漂移（最高风险）**。我们复刻了 SDK 内部的 endpoint/headers 构造。若升级 `langfuse` 后 SDK 改了这些参数，我们传入的 exporter 可能与预期不一致，甚至破坏现有导出。
   - 缓解一：`requirements.txt` 已锁 `langfuse==4.15.6`，并在注释中追加「升级前还需核对 OTLP exporter 的 endpoint/headers 构造」。
   - 缓解二（防漂移测试）：用 monkeypatch 替换 `langfuse._client.span_processor.OTLPSpanExporter` 捕获参数，构造一个**不传** `span_exporter` 的 `Langfuse(...)`，取出 SDK 自己会用的 endpoint/headers，断言与 `build_span_exporter` 生成的一致。SDK 一旦改动该测试立刻变红。
2. **自检被采样误杀**。已在 3.3 用标记放行解决，并有测试锁定。
3. **`flush()` / 同步查询阻塞事件循环**。`flush()` 用 `asyncio.to_thread`；层 2 优先用 SDK 的异步变体 `client.async_api.trace.get()`，若该变体不可用则退回 `asyncio.to_thread` 包同步 API。
4. **层 1 的并发噪声**。层 1 通过「成功计数是否增加」判定，高并发下可能被其它 span 的导出干扰。方向仍然正确（成功计数增加即证明导出通路可用），属于可接受的近似，文档与界面措辞不得宣称它对自检 trace 做了精确归因。
5. **自检 trace 会留在 Langfuse**。这是刻意行为，名字固定 `nanzi.selfcheck` 便于辨识与清理。
6. **健康度是进程内的**。重启即清零；多实例各自独立，界面已标注。

## 6. 测试策略

**单元测试**

- `export_health`：成功/失败计数、连续失败清零、错误截断、快照不可变、并发写入不丢计数、`record_*` 不抛异常。
- `ObservableSpanExporter`：SUCCESS/FAILURE 转发、内层抛异常时返回 FAILURE 且不向上抛、`shutdown` 委托、健康度被正确记录。
- `build_span_exporter`：endpoint 与 headers（含 4 个头）与 SDK 默认一致（即第 5 节第 1 条的防漂移测试）。
- `selfcheck`：未启用时拒绝、span 结构与标记正确、**绕过采样**、flush 期间成功/失败/无变化的三种层 1 判定、`flush` 通过线程执行（断言不阻塞事件循环）、异常降级。
- `manager`：`_build_client` 确实传入了 `span_exporter`；`should_export_span` 对自检标记放行（即使 `sample_rate=0`）。

**接口测试**：三个端点的成功/未启用/权限/非法 `trace_id` 分支；`GET ""` 含 `export_health`。

**前端契约测试**：按钮显隐条件、轮询参数（3 秒/60 秒上限）、卸载时清除定时器、健康度区块与「进程内」标注、层 2 三种结局文案。

**真实 HTTP 实测**（延续上次不 mock 网络的验证方式）：本地起一个假 Langfuse HTTP 服务，接收 OTLP 请求并返回可配置状态码，验证——自检成功时层 1 判定正确、服务端返回 5xx 时层 1 判定为失败并记录错误、真实业务 span 的导出结果进入健康度。

## 7. 实施顺序（建议）

1. `export_health` + `ObservableSpanExporter` + 防漂移测试（先把观测点做扎实）。
2. `manager` 接入 `span_exporter` 并给 `should_export_span` 加自检放行。
3. `run_selfcheck` + 自检相关测试。
4. 三个接口 + 接口测试。
5. 前端按钮、轮询与健康度展示 + 契约测试。
6. 真实 HTTP 实测与文档更新。

## 8. 顺带清理：移除 `trace_url_template`

该字段是一期为「对话页跳转入口」准备的 URL 模板。跳转入口已取消，它成为**永不生效的死配置**，留在表单里会误导管理员以为配了就能跳转。

清理范围：
- 前端 `LangfuseConfig.vue`：移除输入框、占位提示与提交字段。
- 后端 `config_store.py`：从 `DEFAULTS`、`_STR_MAX_LENGTH`、`_SELECT_COLUMNS`、`normalize_payload`、`to_public_view` 与表定义中移除该列。
- `settings.py`：从 `LangfuseSnapshot` 与快照构造中去掉该字段。
- `app/api/portal/endpoints/langfuse.py`：从请求模型与响应视图去掉。
- **数据库列保留**：`db-prod/V161` 与 `db-prod-pg/V62` 已执行，历史迁移不可改；多余一列无副作用（也不新增删列迁移）。
- 同步更新受影响的测试（`test_config_store*`、接口测试、前端契约、迁移契约中涉及该字段的断言）。

**注意**：一期设计文档与 `tests/CHECKLIST.md` 中把「对话页跳转入口」列为二期待办的表述，需一并更正为「已决定不做」并写明理由。
