# Langfuse 直调链路统一出口埋点（三期）设计

- 日期：2026-09-30
- 状态：待实施
- 关联：[一期设计](./2026-09-30-langfuse-llm-tracing-design.md)、[二期设计（端到端自检）](./2026-09-30-langfuse-selfcheck-design.md)

## 1. 背景

### 1.1 现状：直调链路完全没有追踪

一期接入后，**AgentScope 编排链路**（轮次 → agent → LLM → 工具）已完整可追踪，但
**直调链路**——不走 AgentScope 编排、直接使用平台 LLM 封装的代码——没有任何 span。

规模实测有两层：

- `app/core/llm/client.py` 的 `AgentScopeLLMHandle` 有 **24 处**调用点（`.ainvoke` / `.astream`）；
- **更多代码绕过 handle 直接使用 `chat_client_from_handle(...)`，共 26 处**，分布在
  `app/api/portal/endpoints`（models、ragflow）、`app/services/ai`（intent、router、
  summarizer、compactor、hallucination_evaluator、welcome_card…）、
  `app/services/ai/runners`（chatbi/clarification、data_agent_runner）、
  `app/services/ai/executors`、`app/services/ai/prompt_ops` 等处。

### 1.2 成因：架构性的，不是遗漏

这些调用最终都落到 `AgentScopeChatClient`（`app/services/ai/runtime/agentscope/chat.py`），
**完全不经过 AgentScope 的中间件链**，因此一期挂在 AgentScope runtime 上的
`TracingMiddleware` 与 `LangfuseContextMiddleware` 在结构上碰不到它。一期设计文档已把它
列为明确的二期内容：

> 二期：直调链路统一出口埋点（覆盖 20+ 调用点）。

### 1.3 唯一的真实出口是 `AgentScopeChatClient`，不是 handle

核实后确认，`AgentScopeChatClient` 才是最深且唯一的出口：

| 事实 | 依据 |
|---|---|
| handle 的两个方法都委托给它 | `AgentScopeLLMHandle.ainvoke/astream` 内部调用 `chat_client_from_handle(self)` |
| 另有 26 处直接使用它 | 全局 grep `chat_client_from_handle` 的调用点（已排除 import 与函数定义） |
| 四个方法互不嵌套 | `generate_structured_dict` / `generate_text` / `generate_message` / `stream_messages` 各自独立调用 `native_model` → 埋点不会重复计数 |
| 与一期链路不冲突 | **AgentScope runtime 不使用它**（runtime 直接用 `native_model` + 官方中间件），故无重复 span |
| 能看到原始终帧标记 | 它的流式循环里 `chunk.is_last` 可用；转成 `AIMessage` 后该标记丢失 |

因此埋点位置定在 client 层，而非原方案的 handle 层——**覆盖从 24 处扩到 50 处**（24 处经 handle + 26 处直接使用 client），且改动更集中。

### 1.4 契机：一期已预留全部地基

下列能力在一期已实现，其中两项**从未被调用**，显然就是为本次准备：

| 能力 | 位置 | 现状 |
|---|---|---|
| `nanzi.llm` scope 白名单 | `manager.ALLOWED_SCOPE_NAMES` | 已放行（二期自检也在用，通路已验证） |
| `generation_attributes()` | `span_attributes.py:146` | **无任何调用方** |
| `extract_usage()` | `span_attributes.py:118` | **无任何调用方** |
| `make_should_export_span()` | `manager.py:99` | 采样与白名单门，直接复用 |
| `_span_trace_id_hex()` | `manager.py:88` | trace 粒度采样依据，直接复用 |
| `TurnSpan` 形态 | `turn_span.py` | 安全空实现 / 幂等 end / 绝不抛异常的成熟模板 |

**因此本次不是"新起一摊"，而是把一期铺好的半条路接通。**

## 2. 目标与范围

**目标**：让全部直调调用点（handle 的 24 处 + 直接使用 chat client 的 26 处，合计 50 处）进入 Langfuse，
且业务代码零改动。

**做法**：只在 `AgentScopeChatClient` 的 **4 个方法**上埋点。

**成功标准**：

1. 任一直调调用点在 Langfuse 中可见，含模型名、输入输出、token 用量、耗时；
2. 追踪关闭或 Langfuse 不可用时，调用方行为与返回值完全不变——特别注意
   `generate_structured_dict` 是 **fail-open** 语义（异常被吞掉并返回 `None`），
   埋点后必须仍然如此；
3. 流式调用被提前中断（`break` / `aclose()`）时 span 仍正确收尾，不泄漏；
4. 轮次内的直调挂进该轮次 trace，且**不改变**该 trace 的名称。

## 3. 架构

### 3.1 埋点位置与 trace 归属

`AgentScopeChatClient` 的 4 个方法各包裹一个 generation span，归属完全交给 OTel 的自然语义，
**不写任何"是否在轮次内"的判断代码**：

| 调用场景 | OTel 行为 | Langfuse 效果 |
|---|---|---|
| 轮次内被调用（如 executor / 工具内部） | `start_span()` 自动以当前 span 为父 | 挂进该轮次 trace |
| 独立触发（ChatBI 澄清、报告摘要、意图识别） | 无活跃 span → 自成一条 trace | 每次调用一条独立 trace |

用 `tracer.start_span()`（而非 `start_as_current_span`）即可满足两种情形，因为父节点取自
当前 OTel context，无父时自建新 trace。

**必须避免的坑**：现成的 `span_attributes.trace_attributes()` 会写入 `langfuse.trace.name`
（无 agent_name 时为 `"对话"`）。直调 span 若在轮次内写它，**会把整条轮次 trace 改名**。
因此：

- 直调 span **只**复用其中的身份属性（`user.id` / `session.id`）；
- `langfuse.trace.name` **仅在自建 trace 时**写入：优先用 `source`，否则用 span 名。

判定"是否自建"用 OTel 原生方式，不引入额外状态：

```python
parent = otel_trace.get_current_span()
is_root = not parent.get_span_context().is_valid
```

### 3.2 组件划分

**新增** `app/services/ai/observability/generation_span.py`

单一职责：把"一次模型调用"变成 span。对外暴露一个异步上下文管理器与一个句柄：

```python
@asynccontextmanager
async def traced_generation(
    *, model: str | None, streaming: bool,
    source: str | None, input_messages: Any,
) -> AsyncIterator[GenerationSpan]: ...

class GenerationSpan:
    active: bool                                          # 追踪不可用时为 False
    def record_output(self, output: Any) -> None: ...     # 响应或单个 chunk
    def record_status(self, status: str) -> None: ...     # 结构化输出状态等
    def end(self, error: BaseException | None = None) -> None: ...  # 幂等、绝不抛
```

内部复用 `is_tracing_enabled()`（`turn_span.py:45` 的三重检查：开关 + 连接参数 + client 已建好）
与 `_get_tracer()`（`nanzi.llm` scope）。形态刻意对齐 `TurnSpan`：不可用时是**安全空实现**，
`end()` 幂等，所有 span 操作包 try/except。

**小重构** `span_attributes.py`：拆出 `identity_attributes()`（`user.id` / `session.id`，
可选 `extra` / `trace_id` 仍走 metadata 打平，供 `trace_attributes()` 使用），
`trace_attributes()` 内部改为调用它再加 `TRACE_NAME`。目的：让直调 span 能取身份属性
**而不取** trace name。`trace_attributes()` 的签名与行为保持不变，`turn_span` 零影响。

**改动** `app/services/ai/runtime/agentscope/chat.py`：

1. `AgentScopeChatClient.__init__` 增加 `model_name: str | None = None` 与 `source: str | None = None`；
2. `chat_client_from_handle()` 从传入的 handle 上取 `model_name` 与 `source` 交给 client
   （若传入的本身就是 native_model，则两者为 `None`）；
3. `generate_structured_dict` / `generate_text` / `generate_message` / `stream_messages`
   四个方法各自包裹 `traced_generation(...)`。

**改动** `app/core/llm/client.py`：`AgentScopeLLMHandle` 增加 `source: str | None = None` 字段，
**`bind_tools()` 必须透传它**（它返回新实例，漏传会让"绑定工具后的调用"丢标签）；
`LLMFactory.get_chat_model` / `get_llm` / `get_llm_async` 增加可选 `source` 参数并透传。

### 3.3 数据流

```
50 个业务调用点（零改动）
   ↓ get_llm(source=...) → AgentScopeLLMHandle         （24 处）
   ↓ 或直接 chat_client_from_handle(llm)                （26 处）
AgentScopeChatClient  ← 埋点：traced_generation(...)
   ↓ native_model(to_agentscope_messages(messages))
OpenAI 兼容模型
   ↓ 响应 / chunk（含 is_last 终帧标记）
GenerationSpan.record_output() → async with 自动收尾
   ↓ OTel span end
LangfuseSpanProcessor → should_export_span(白名单 + 采样) → OTLP → Langfuse
   ↓
export_health 记录导出成败（二期能力，自动生效）
```

### 3.4 span 属性映射

全部复用现成函数，不新造属性名：

| 属性 | 值 / 来源 |
|---|---|
| `gen_ai.operation.name` | `"chat"`（`generation_attributes`） |
| `langfuse.observation.type` | `"generation"`（同上） |
| `gen_ai.request.model`、`langfuse.observation.model.name` | `client.model_name` |
| `gen_ai.input.messages` | `serialize_payload(messages)` |
| `gen_ai.output.messages` | `serialize_payload(响应 / 累积结果)` |
| `gen_ai.usage.input_tokens` / `output_tokens` | `extract_usage()` → `generation_attributes(usage=)` |
| `langfuse.observation.usage_details` | 同上 |
| `user.id` / `session.id` | `agent_context` contextvar → `identity_attributes()` |
| `langfuse.trace.name` | **仅自建 trace**：`source` 或 `nanzi.llm.call` |
| `nanzi.llm.source` | 调用方传入的业务来源标签（如 `chatbi.sql`）；未传则不写 |
| `nanzi.llm.streaming` | 是否流式调用 |
| `nanzi.llm.first_chunk_ms` | 流式首 chunk 相对起点的毫秒数（TTFT） |
| `nanzi.llm.structured_status` | `generate_structured_dict` 的结果状态 |
| `error.type` | 异常路径（`type(exc).__name__`） |

span 名统一为 `nanzi.llm.call`。

**为什么 `source` 用自有属性名而不是 `langfuse.trace.metadata.*`**：后者是 trace 维度属性，
轮次内的直调写它会与 `langfuse.trace.name` 犯同一个毛病——同一条 trace 上多次不同来源的
直调会互相覆盖，反而丢信息。自有属性名既无污染，自建 trace 时又能顺带作为 trace name 显示。

身份取自 `app/core/context.py` 的 `agent_context` contextvar（`get_current_agent_context()`，
字段含 `user_id: int | None` / `conversation_id: str | None`），取值失败一律降级为不写。

### 3.5 流式与终帧处理

`stream_messages` 是异步生成器，其函数体**在首次迭代时才执行**，因此 span 在首次
`__anext__` 时创建——语义正确（生成器没被消费 = 调用没发生）。

**终帧陷阱**：AgentScope 的流式响应形如"增量块（`is_last=False`）+ 完整终帧
（`is_last=True`）"，终帧 content 是整段累积后的完整正文。若把逐块文本直接拼接，会得到
"增量 + 完整正文"的**重复内容**。因此：

- 累积时以 `is_last` 区分：终帧**覆盖**已累积文本，非终帧才追加（与既有 `generate_text` /
  `generate_message` 的处理保持一致）；
- 用量从消息的 `usage_metadata` 取（`extract_usage` 兼容 dict 的 `input_tokens` /
  `output_tokens`），流式下通常只在终帧出现；
- 记录首 chunk 时刻算 TTFT。

收尾用 `traced_generation` 的 `try / except BaseException / else` 保证三条路径都关闭 span：

- 正常结束 → `else` 分支收尾；
- 模型抛异常 → 记 `error.type` 后 **`raise` 原样抛出**（追踪绝不吞业务异常）；
- 调用方 `break` / `aclose()` → 抛 `GeneratorExit` → 走 `except` 收尾后重新抛出。

`generate_structured_dict` 是 **fail-open** 的（异常被吞、返回 `None`），埋点必须**不改变**
这一语义：不用异常判定失败，而是记录 `last_structured_output_status`。

**不 attach 到 OTel 上下文**：单层 span 没有子节点，且可省掉跨 `yield` 的 `detach` 失败坑
（`turn_span.py` 的模块注释已记录过这个坑）。

### 3.6 内容捕获

**不加新逻辑**：仍由一期的 `mask_otel_spans` 在导出期删除内容属性
（`CONTENT_ATTRIBUTES` 已包含 `gen_ai.input.messages` / `gen_ai.output.messages`）。
保持"一个开关、一套机制"，避免出现两处语义分裂。

## 4. 错误处理与降级

| 场景 | 行为 |
|---|---|
| 追踪未启用 / 未配置 / client 未就绪 | `start_generation_span` 返回 `active=False` 的空句柄，**不创建 span**（零开销） |
| span 创建/属性写入/结束抛异常 | 记 debug 日志后继续，调用方无感 |
| 业务调用本身抛异常 | 记 `error.type`，**异常原样抛出**，返回值语义不变 |
| Langfuse 不可达 | 导出失败由二期 `export_health` 记录，业务不受影响 |
| `agent_context` 取不到 | user/session 属性不写，其余照常 |

**硬约束**：本次改动不得改变 `ainvoke` / `astream` 的返回值、异常语义、流式 chunk 序列。

## 5. 明确不做

- **不做**父子两层 span（评审时已排除：当前没有"重试/多步推理"这类中间步骤要承载，
  真需要时再加父层成本很低）；
- **不设**直调链路独立开关（继承一期三层开关：`enabled` / `capture_content` / `sample_rate`）；
- **不改**任何业务调用点（24 处经 handle + 26 处直接使用 client，合计 50 处）；
- **不做**按 `source` 的采样或过滤白名单（YAGNI）；
- **不做**前端展示改动（平台内不做 Langfuse 阅读页，一期已定）。

## 6. 风险

| # | 风险 | 应对 |
|---|---|---|
| 1 | **`generation_attributes()` 的属性格式从未经真实验证**（一期写了但没接入，Langfuse 能否正确渲染输入输出未知） | 单元测试锁定属性名；真实验证时在控制台确认输入输出可见；若格式不被识别，回退为补写 `langfuse.observation.input/output` |
| 2 | 埋点下移到 client 层，覆盖面扩大到 50 个调用点，**行为回归风险高于 handle 层方案** | 每个方法都做"返回值/异常语义完全不变"的对照测试；重点覆盖 `generate_structured_dict` 的 fail-open 语义 |
| 3 | 流式终帧导致文本重复拼接 | 以 `is_last` 区分增量与终帧、终帧覆盖；专门测试断言文本不重复 |
| 4 | 流式提前中断导致 span 泄漏 | 专门测试 `break` / `aclose()`，断言 span 已结束 |
| 5 | 直调 span 污染轮次 trace 名称 | 仅自建 trace 时写 `langfuse.trace.name`；专门回归测试 |
| 6 | `bind_tools()` 丢 `source` | 专门测试"绑定工具后 source 仍保留" |
| 7 | 埋点开销影响高频直调 | 复用一期的测量方法做前后对比；追踪关闭时为零开销路径 |
| 8 | 直调内容更敏感（SQL、用户数据） | 继承 `capture_content` 与 `mask_otel_spans`，与一期同一套脱敏 |

## 7. 测试策略

**单元测试**（新增 `tests/services/ai/observability/test_generation_span.py`）
用假 tracer 注入，断言属性映射：

- 追踪关闭 / client 未就绪 → 零 span 创建（空句柄）；
- 正常结束 → 名为 `nanzi.llm.call`，含 model / `observation.type=generation` / 用量 / 身份属性；
- `is_root=True` 时写 `langfuse.trace.name`；`is_root=False` 时**不写**；
- 不传 `source` 时不写 source 属性；
- `end(error=...)` 写 `error.type` 且幂等；
- 流式累积：终帧覆盖增量，文本不重复；usage 取自终帧。

**出口埋点测试**（新增 `tests/services/ai/runtime/agentscope/test_chat_tracing.py`）
用假 native_model 驱动真实的 `AgentScopeChatClient` 四个方法：

- `generate_message` 正常路径产出 span 且返回值不变；
- `generate_message` 抛异常时 span 记录错误**且异常照常抛出**；
- `generate_structured_dict` 在 native 抛异常时仍**返回 `None` 不抛出**（fail-open 不变），
  且 span 记录 `structured_status`；
- `generate_text` 返回值不变（含流式累积）；
- `stream_messages` 完整消费 → span 结束、TTFT 有值、文本不重复；
- `stream_messages` 提前 `break` → span 已结束；
- `stream_messages` 中途异常 → span 记录错误且异常照常抛出。

**回归测试**：

- 轮次内直调挂进同一 trace（同一 trace_id）且**轮次 trace name 未被改写**；
- `chat_client_from_handle` 能从 handle 取到 `model_name` 与 `source`；
- 追踪关闭时四个方法行为与无埋点版本一致（返回值、chunk 序列、异常）；
- 既有 chat 相关用例零回归。

## 8. 实施顺序

1. `span_attributes` 拆出 `identity_attributes()`（保持 `trace_attributes` 行为不变，跑既有测试确认零回归）；
2. 新增 `generation_span.py` + 单元测试；
3. `chat.py` 的 client 字段与 `chat_client_from_handle` 透传、`client.py` 的 `source` 字段/透传；
4. 四个方法依次埋点（每个方法配对照测试）；
5. 回归测试与性能对比；
6. 更新 `tests/CHECKLIST.md`，并在真实 Langfuse 实例上验收（与二期自检一并进行）。
