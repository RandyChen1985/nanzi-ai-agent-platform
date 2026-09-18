# AI 上下文管理指南

> 本文档描述平台在**多轮对话上下文连续性**上的设计与实现，供后续维护、排障与扩展参考。
> 覆盖：信息流、溢出压缩（compaction）、语义摘要（LLM digest）、跨轮摘录持久化（digest）及对应配置项。
> **当前实现校准（2026-08-27）**：未指定专家时直接进入默认 Main，不再调用外层 `RouterService.route_query()`；下文旧版 `early_context` 路由复用仅供历史调用兼容参考。

---

## 1. 背景与目标

### 1.1 问题

对话轮次增多后，给大模型的上下文窗口（token 预算由**动态水位线**决定，见 §1.3；`agent_max_context_messages` 为条数兜底上限，默认 **60** 条，仅作极端情况下的绝对兜底）会自动截断最早的历史消息。截断本身是必要的（控制 token 成本与延迟），但它存在三类信息丢失风险：

1. **窗口内不可逆丢失**：被丢弃的旧消息若无任何兜底，后续轮次的指代（「我刚才说的那个」「第三轮那个人」）与事实（已确认决策、未完成事项）会断档。
2. **跨轮滑动丢失**：即使每轮都做局部保留，随着窗口持续滑动，最早的事实仍会被最终挤出，长会话尾部仍可能遗忘真正早期的前提。
3. **委派上下文丢失**：Main 或子代理执行时若看不到更早轮次要点，可能无法正确理解用户的连续任务或指代。

### 1.2 目标

- 在**不显著增加延迟 / token 成本**的前提下，尽量保住被截断消息中的关键事实与指代。
- 摘要与展示分离：压缩摘录只进模型上下文，**不污染**用户可见的完整对话历史。
- 任何增强项（LLM 摘要、digest 读写）失败都必须**优雅降级**，不影响主链路。

### 1.3 动态截断水位线（runtime budget）

截断窗口的 token 预算并**非固定值**，而是分两个阶段运行时解析，语义如下：

1. **默认 Main 入口预算**：未指定专家时直接读取默认 Main 的模型与上下文配置，不增加一轮外层路由模型调用。
2. **最终模型窗口优先**：确定 Main 或指定专家后，优先采纳最终模型所注册的 `context_size`（即 `current_model.context_size`，共用源），解析顺序：
   - `debug_options.model`（输入框切换所选模型）→ 经 debug 通道解析；
   - 否则最终发布 agent 的 `model_name` → 经注册表解析。
3. **配置兜底**：最终模型不可解析、或模型是 `system_default`（系统默认回落，无显式指定）时，回落 `agent_context_max_tokens`（默认 **65536**）。
4. **采纳条件**：仅当模型来源为显式指定（`runtime_override` / `debug_override` / `agent_config`，**排除 `system_default`**）且注册表解析出有效 `context_size > 0` 时才采纳模型窗口，否则一律回落配置兜底值。历史消息预算还会预留 `agent_context_overhead_headroom_tokens`（默认 8192）给系统提示、工具 schema 和本轮用户消息；若配置的主模型与合成模型窗口不同，取两者历史预算的较小值。

> 设计目的：避免模型实际窗口较大（如 1M）而兜底配置仅 64k 时，出现**提前截断（过早 compat）**，也避免用错 agent 的模型窗口。任何 DB / 注册表异常均静默回落兜底，不影响主流程。最终模型确定后，实际发送上下文会按该模型重新构建；runner、模型调用统计、压缩卡片与 `session_status` 共享 `physical_window` / `history_budget` 这组最终运行时口径。

---

## 2. 信息流总览

一次智能体（agent）对话轮次的上下文组装链路如下：

```
get_history()                      # ① MemoryService 读取最近 N 条持久化历史（原始 dict、含所有角色）
    │
    ▼
_history_messages_for_context()    # ② 白名单过滤（只留 system/user/assistant，可为多模态结构）
    │
    ▼
    Main / 指定专家入口                 # ③ 确定父智能体；未指定时直接使用默认 Main
    │
    ▼
最终 agent/model 确定                 # ④ 完成父智能体与模型解析
    │
    ▼
按最终模型重建窗口                  # ⑤ 用目标模型 context_size 重新截取；溢出时本轮确定性摘录，后台生成语义摘要
    │
    ▼
messages = context_history + [本轮用户消息]
    │
    ▼
convert_history_to_messages() / normalize_messages_for_llm()   # ⑥ 归一化为 LLM 可消费消息（合并 system 区）
    │
    ▼
注入 LLM
```

> 关键点：摘录以 `role="system"` 消息注入窗口**最前**。`normalize_messages_for_llm` 会把多条 system 合并到系统区，因此不会破坏 user/assistant 的相对顺序，用户在界面看到的完整历史也不受影响。

### 2.1 默认 Main 路径 vs 指定专家路径

- **默认 Main**（`agent_service._run_chat_turn_stream`）：直接进入 Main，走 `_maybe_compact_overflow` 完整链路。
- **指定专家**：直接加载目标专家，同样走 `_maybe_compact_overflow` 完整链路；专家是否委派子代理由其自身执行过程决定。

两条路径共用同一份持久化 digest key，因此业务感知一致。

---

## 3. 溢出压缩（确定性 compaction）

### 3.1 触发条件

`_maybe_compact_overflow(full_history, window, *, user_id, conversation_id, agent_id, agent_name, version_id)`

1. **早退**：`len(full_history) <= len(window)` 时**直接返回 window**，无溢出不做事。
2. 读配置 `agent_context_compaction_enabled`（默认 `"true"`）；非开启值（`1/true/yes/on` 之外）直接返回 window。
3. 读配置 `agent_context_compaction_max_chars`（默认 `1200`），下限保护 `max(200, int(...))`，非法值回落 `1200`。
4. 若有 `user_id` + `conversation_id`，调用 `MemoryService().get_digest(...)` 读取上一轮持久化摘录作为 `prev_digest`（读取失败捕获为 `None`）。
5. 调用 `apply_context_compaction(full_history, window, max_chars=..., prev_digest=...)`。

### 3.2 确定性摘录生成

`apply_context_compaction`（`app/services/ai/context_compaction.py`）：

```python
if not full_history or len(full_history) <= len(window):
    # 无溢出：若存在跨轮 prev_digest，仍把它作为锚点注入
    return [{"role":"system","content":prev_digest}] + window   # prev_digest 存在时
    return window                                               # 否则
dropped = full_history[: len(full_history) - len(window)]        # 本轮被丢弃的旧消息
digest  = build_overflow_digest(dropped, max_chars=..., prev_digest=...)  # 确定性拼装
return [digest] + window
```

`build_overflow_digest(...)` 纯文本处理、**零 LLM 调用**：

- 逐条把丢弃消息折叠为 `- 角色：内容`，单条默认上限 `120` 字符（截断加 `…`）。
- 支持的 `_ROLE_LABELS`：`user→用户`、`assistant→助手`、`system→系统`；其他 role 跳过。
- `prev_digest`（跨轮锚点）经 `_extract_digest_body` 剥离 marker 与固定说明行后，叠加到新摘录最前。
- 从最新往回累加直到 `max_chars`，保证优先保留**离当前最近**的旧消息，再恢复时间顺序。
- 无可写成内容时返回 `None`（调用方保持原 window）。

### 3.3 返回格式

摘录正文带固定前缀：

```
[早前对话摘录]
以下是更早轮次对话的要点（已压缩，仅供理解上下文与指代，不要逐条复述）：
- 用户：……
- 助手：……
```

`COMPACTION_MARKER = "[早前对话摘录]"` 用于 `set_digest` 前的判空识别（只有 content 内含 marker 才会持久化，避免把非摘录的 system 消息误当 digest）。

---

## 4. 语义摘要（LLM digest，E 项增强）

真实溢出后，`_maybe_compact_overflow` **本轮立即**用确定性摘录（`apply_context_compaction`）注入窗口，**零阻塞首 token**；同时把更高语义质量的 LLM 摘要降级为**后台异步任务**生成，完成后写回 Redis digest，**下一轮**真实溢出时经 `prev_digest` 自然合入（滞后一轮生效），任何失败都回退并保持本轮确定性结果。

### 4.1 触发与降级链

`_spawn_llm_digest_task(full_history, window, *, max_chars, prev_digest, user_id, conversation_id, agent_id, agent_name, version_id) -> Optional[asyncio.Task]`

- 仅当 `user_id` 与 `conversation_id` 均非空时才起后台任务（否则无 digest 落点，直接返回 `None`）。
- 内部 `asyncio.get_running_loop().create_task(_run())` 包 `_try_llm_overflow_digest(...)`；持有并返回 task 引用防 GC；协程内部全量 `try/except` 捕获异常，避免 `Task exception was never retrieved`。
- 成功：取出 `content`（含 `COMPACTION_MARKER`）→ 按来源 `seq` 与会话 branch revision 条件写入 digest，供下一轮读取；历史已更新或已截断时丢弃旧任务结果。
- 失败 / 无可用 client：日志告警后静默返回，**不覆盖**本轮 already 写入的确定性摘录。

最终语义摘要本体由 `_try_llm_overflow_digest(full_history, window, *, max_chars, prev_digest, agent_id, agent_name, version_id) -> Optional[Dict]` 生成：

- 读 `agent_context_llm_summary_enabled`（默认 `"true"`）→ 关闭则直接 `None`。
- 把丢弃片段 + 更早摘录要点拼成 transcript（`_flatten_content` 归一多模态；`_extract_digest_body` 剥离旧 marker）。
- **模型解析优先级**：
  1. 任一 agent 身份非空（`agent_id`/`agent_name`/`version_id`）→ `AgentContextManager.resolve_agent_config(...)` 取 ChatConfig → `AgentConfigProvider.get_configured_llm(streaming=False, config=...)`。
  2. 否则 → `AgentConfigProvider.get_fallback_llm(streaming=False)`（系统默认 `llm_model_name`）。
  3. 均不可得 → `None`（确定性兜底）。
- 拼接 system 提示词（要求覆盖关键事实/决策/未完成事项/核心对象术语，正文控制在 `max_chars` 内）+ user transcript。
- `asyncio.timeout(15)` 包裹 `ConversationSummarizer._generate_with_retry(chat_client, msgs, max_retries=2)`（指数退避 `min(8.0, 1.0*2^attempt)`，调用 `chat_client.generate_text`）。
- 成功：输出加 `COMPACTION_MARKER` 前缀 → 返回 `{"role":"system","content":...}`。
- 任一异常 / 超时 / 空输出：捕获并 log 后返回 `None` → 使用确定性摘录。

> 设计铁律：语义摘要是**增强项**，绝不阻塞主链路。它是**后台/异步**生成的：本轮先用确定性摘录即时注入窗口（零首 token 延迟），LLM 摘要下一轮才生效；模型复用当前会话（不新增独立摘要模型），失败不改变本轮结果、不增加用户可感知的延迟成本。

---

## 5. 跨轮摘录持久化（digest，B 项）

### 5.1 存储

digest 存于 **独立 Redis key**，与 history（LIST 展示路径）分开：

```
conversation:{user_id}:{conversation_id}:digest
```

`user_id` 取 `str(user_id)`，为空时为 `"anonymous"`。TTL 沿用会话默认 **30 天**（`MemoryService.ttl`，2592000s）。

接口（均为 async）：
- `await MemoryService().get_digest(user_id, conversation_id) -> Optional[str]`：无摘录 / redis 不可用 / 读取异常均返回 `None`（调用方降级为确定性压缩）。
- `await MemoryService().set_digest(user_id, conversation_id, content)`：无并发条件时写入/删除 key。
- `await MemoryService().set_digest_if_current(..., source_seq, source_revision, quality)`：带 seq/revision/质量门控的条件写入；确定性摘录为 `quality=0`，后台 LLM 摘要为 `quality=1`，旧分支或旧摘要不能覆盖新结果。上下文压缩可允许同一分支在生成期间追加消息（`allow_newer_seq=True`），编辑重发的 revision 变化仍会拒绝旧任务。

### 5.2 写入时机

在 `_maybe_compact_overflow` 生成 `compacted` 后：若 `compacted[0]` 是 system 且 content 含 `COMPACTION_MARKER`，则持久化该摘录正文；否则写入空串（删除）。写入失败仅 log，不阻断主链路。

### 5.3 跨轮复用

下一轮调用 `_maybe_compact_overflow` 时：
- 有溢出 → 把 `prev_digest` 作为「更早锚点」叠加进新摘录（`_extract_digest_body` 剥离 marker/说明行，避免 marker 重复累积）。
- **无溢出** → `apply_context_compaction` 仍会把 `prev_digest` 原样作为 system 摘录注入，保证即使窗口没有滑动、早期事实依然在线。

### 5.4 与展示层隔离

digest 用独立 key，`get_conversation_history` 的展示路径读取的是 history LIST，**不会**把摘录混进用户可见历史；摘录只存在于最终送给 LLM 的上下文消息里。

---

## 6. 旧路由上下文兼容（early_context，历史 D 项）

当前默认 Main 和指定专家路径不调用 `RouterService.route_query()`，因此不会为外层专家转向额外构建 `early_context`。历史代码或外部旧调用若仍直接使用 `route_query(..., early_context=...)`，可以继续复用持久化 digest；该兼容参数不改变当前 Main 的入口语义。

---

## 7. 配置项一览

所有开关经 `ConfigService.get(name, default)` 读取，字符串布尔值，`1/true/yes/on`（不区分大小写）视为开启。

这些配置项在系统设置中被归类为单独的 **「上下文管理」** 分组（后端 `category = 'agent_context'`，前端有独立面板排序与说明）；`agent_max_context_messages` 也从原「智能体」分组归入本组。

| 配置项 | 默认 | 作用 |
| --- | --- | --- |
| `agent_context_max_tokens` | `65536` | **上下文 Token 预算兜底上限（默认 64k）**。作为动态水位线（§1.3）的回退值：当显式模型未注册有效 `context_size` 或来源为 `system_default` 时使用。从历史尾部倒序累计估算 token，直到预算或条数上限任一达限即截断最早历史。 |
| `agent_context_overhead_headroom_tokens` | `8192`（**代码常量**，非系统配置项） | 为系统提示、工具 schema、技能提示、路由注入内容及本轮用户消息预留的物理窗口空间。**有输出预留时** `history_budget = physical_window - max_output_tokens - overhead`；**无输出预留时** `history_budget = max(physical_window - overhead, physical_window // 3)`。 |
| `agent_max_context_messages` | `60` | 窗口条数绝对兜底上限（token 预算优先，条数仅在极端情况防无限膨胀时触发）。 |
| `agent_context_compaction_enabled` | `true` | 是否启用溢出压缩。关闭则溢出旧消息直接丢弃。 |
| `agent_context_compaction_max_chars` | `1200` | 摘录最大字符数（下限 `200`）。 |
| `agent_context_llm_summary_enabled` | `true` | 是否启用语义摘要（E 项，**后台异步**生成、下一轮生效）。关闭则只用确定性压缩。 |

> **不作为系统配置项的两个内部常量**（刻意不做成可配置项：管理员无法据经验估出合理值，做成开关只会多一个填错的旋钮；需要按环境覆盖时改代码常量或其上游字段）：
> - `CONTEXT_OVERHEAD_RESERVATION_TOKENS = 8192`（`app/services/ai/context_usage.py`）——历史之外需预留的物理窗口空间。预算计算与用量展示**共用同一个常量**，避免两处漂移出不同水位线。绑定了大量 MCP 工具时真实开销会超过它，症状是 `ModelCallStatsMiddleware` 打出 `Input exceeds model context window` 告警。
> - `LLM_DIGEST_TRANSCRIPT_MAX_CHARS = 24000`（`app/services/ai/context/compactor.py`）——LLM 语义摘要请求中「被丢弃历史」的字符上限（下限 `2000`，单条上限为其 1/8）。超出时优先保留离当前最近的部分并插入省略说明，更早内容由上一版摘录兜底；不设上限会让这个「用来解决超窗」的请求自己超窗并静默降级为确定性摘录。

> `agent_context_max_tokens` 默认值由代码兜底（`ConfigService.get(..., "65536")`），配置表无种子或缺失时回落为 64k；当显式模型解析出有效窗口时，它仅作为**兜底（fallback）**而不参与最终水位线（§1.3）。版本迁移 `V126`（MySQL）/`V26`（PG）把上下文配置写入并归入 `agent_context` 分组。

> **Token 估算口径**：平台侧的裁剪水位线、上下文占用展示、以及 AgentScope 运行时的内部判定，**必须使用同一口径**，即 `estimate_text_tokens` 采用的「UTF-8 字节数 ÷ 4」（与 AgentScope `count_tokens` 一致）。历史上曾按 `cjk * 1.5 + other / 4` 加权，对中文高估约 2 倍，导致中文会话在模型窗口只用到约三分之一时就被判定超预算并压缩（并对英文低估、可能超窗）。**请勿改回按字符类型加权的写法。**

> **第三道水位线（AgentScope 自带压缩）**：除平台的两阶段水位线外，AgentScope 运行时会按 `ContextConfig(trigger_ratio=0.8, reserve_ratio=0.1)`（配置项 `agentscope_context_trigger_ratio` / `agentscope_context_reserve_ratio`）自行压缩其内部 context——64k 模型上约在 51.2k 触发。它使用自己的 `count_tokens` 判定，因此可能出现「没到平台水位线却已被压缩」，且**不会**产生平台的 `context_summarized` 记录（只有 `context_compression` 观测）。排障时需把这条线一并纳入判断。

对应 Boolean 能力项（A–F）：
- **A** 工具结果纳入历史（`full_history` 含 tool 相关角色，见 §2 白名单）。
- **B** 溢出摘录 ↔ `LongTermMemory`（digest 跨轮持久化，§5）。
- **C** 以 token 预算替代条数窗口（此处以 `max_chars` 控制摘录 token 上限）。
- **D** 旧 RouterService 路由层复用压缩摘录（`early_context`，§6；当前默认 Main 不使用）。
- **E** 摘录跨轮持久化 + 可选 LLM 语义摘要（当前会话模型，§4）。
- **F** 窗口内保留 agent / 工具元数据。

---

## 8. 真实溢出与无溢出的行为矩阵

| 场景 | 智能体直连路径 `_maybe_compact_overflow` | 路由路径 |
| --- | --- | --- |
| 无溢出，无跨轮 digest | 直接返回 window | `early_context` 无 → 不注入 |
| 无溢出，有跨轮 digest | 注入 prev_digest 作为锚点 | 注入 early_context（persisted digest） |
| 溢出，LLM 摘要开启且成功 | 本轮确定性摘录 + window；后台语义摘要下一轮生效 | （不分叉，走各自持久化 digest） |
| 溢出，摘要关闭/失败 | 确定性摘录 + window | 同上 |
| redis / 摘要异常 | 捕获降级，日志告警，不阻断 | read-digest 失败 → 无 early_context |

---

## 9. 常见排障

- **摘录未生效**：先看 `agent_context_compaction_enabled` 是否被置为关闭；再看 `len(full_history) > len(window)`（`agent_max_context_messages`）是否真的溢出。
- **提前 compat / 过早截断（模型明明很大却提前压缩）**：确认当前模型是否被显式指定（输入框切换或发布版本配置）且注册表解析出 `context_size`；若模型来源落到 `system_default`（未显式指定），动态水位线不会采纳其窗口而回落 `agent_context_max_tokens`（默认 64k），需在 agent 配置中显式选择模型并确认其注册 `context_size > 0`。日志关键字 `Failed to resolve published model config for runtime context budget`。
- **弹框 / session_status 预算口径不一致**：中间件弹框 `context_budget`、`session_status` 上下文用量估算与截断水位线共用同一动态值（§1.3），若用户侧只看到其中一处变动，优先排查模型来源是否被识别为显式指定。
- **LV 摘要没生效而是确定性**：查日志 `[Compaction][LLM digest] ... fallback to deterministic`，对应模型解析失败 / 无可用 client / 15s 超时 / LLM 报错等。注意 LLM 摘要是**异步后台**生成的（§4）：本轮必然先注入确定性摘录，语义摘要**下一轮溢出**才经 `prev_digest` 生效，属预期时序；若多轮后仍始终为确定性且日志可见 `[Compaction] Async LLM digest task failed`（或根本无 `Async LLM digest persisted`），再沿上述项排查。
- **跨轮 digest 不累积**：确认 `user_id` 与 `conversation_id` 均非空（`handoff` 等未传 cid 的路径会跳过）；确认 Redis key `conversation:{uid}:{cid}:digest` 存在且未过期（7 天 TTL）。
- **digest 出现在用户可见历史**：不应发生。digest 走独立 key，展示路径只读 history LIST；若出现，多半是误把 digest 写进了 history 而非 digest key。
- **会话摘要（`merge_session_summary`）在历史列表满窗口后不再收录新消息**：原因是历史 LIST 被 `ltrim(..., -max_history_len, -1)` 恒定截到 100 条，`len(history)` 不再增长，旧版基于位置 `synced_len` 的增量窗口会永久为空。修复为**单调 seq 游标**（每条新消息分配全局递增 `seq`，摘要消费 `synced_seq`），即使窗口长度不增长，新消息的 `seq` 仍持续增大，增量窗口得以捕获；编辑重发会先重置派生摘要状态，并通过 branch revision 使旧的后台摘要结果失效。详见 §11。存量无 `seq` 字段的旧消息由摘要侧按 `isinstance(m.get("seq"), int)` 排除，不补 seq，`synced_seq==0` 时回退位置增量。
- **动态水位线因注册表异常阻断聊天（提前 compat）**：`_resolve_runtime_context_budget` 已把 `resolve_runtime_model_info` 包进 `try/except`，`ModelRegistryError` 或任意解析异常回落 `agent_context_max_tokens`，不再上抛阻断主流程。日志关键字 `Failed to resolve runtime model info for context budget`（注意区分于已存在的 `Failed to resolve published model config ...`）。

---

## 10. 相关文档

- `docs/md/api_integration_guide.md` —— API 集成指南（§ 上下文组装「最近约 6 轮历史 + 上一轮智能体」）。
- `docs/md/ai_agent_gating_contract.md` —— 智能体开关/准入契约。
- `docs/md/embed_integration_guide.md`、`docs/md/code_canvas_and_workspace_guide.md`、`docs/md/chatbi_v1_http_api.md` —— 其它功能/集成指南。
- `docs/release/1.0.3/release_log.md` —— 1.0.3「上下文压缩与会话 followup 队列」特性发布记录。
- `architech/design/AGENT_ROUTING_DESIGN.md` —— 智能委派与专家直选设计；旧 `route_query` / `early_context` 仅作兼容背景。
- `docs/html/B01-long-term-memory.html`、`docs/html/C03-prompt-context-loop.html` —— 长期记忆与提示词上下文循环产品文档。

---

## 11. 会话摘要游标（seq 机制）

> 本节记录 `SessionSummaryService.merge_session_summary`（会话摘要）的增量消费游标如何避免「历史列表满载后停摆」。与 §4/§5 的**溢出压缩 digest** 是两套独立能力，勿混淆。

### 11.1 问题背景

`MemoryService.add_message` 用 `ltrim(key, -max_history_len, -1)` 把 history LIST 恒定截到 `max_history_len`（默认 `max_history_turns*2 = 100` 条）。旧版摘要用 `len(history) > old_synced_len` 判定增量、取 `history[old_synced_len:]` 为窗口，并把 `synced_len = len(history)` 写回 debounce 状态。一旦窗口装满 100 条，`len(history)` 不再增长，增量窗口永久为空 → 摘要走 recency 刷新分支（`changed=False`），**新消息从此不再进摘要**。

### 11.2 方案：单调 seq + synced_seq 游标

- `MemoryService.add_message` 在消息入列前经独立 per-conversation 计数器 key
`conversation:{uid}:{cid}:seq_counter` 调用 `INCR` 取得**全局单调 `seq`** 写入消息 dict。seq 与 list 索引解耦，`ltrim` 压缩索引不影响 seq 的单调性；计数器 TTL 随历史 key 同步（`self.ttl`，默认 7 天）。另有独立的 `context_revision`：历史截断/清空时递增，用于让已经在后台运行的旧分支摘要失效。
- `merge_session_summary` 的增量窗口改为：
  `above = [m for m in history if isinstance(m.get("seq"), int) and m["seq"] > old_synced_seq]`，取 `last_seq = max(seqs)`，写回 `synced_seq = last_seq`（保留 `synced_len = len(history)` 兼容旧 reader）。
- 效果：
  - **满载不停摆**：历史恒满 100 条时，新消息 seq 持续增大 → `above` 非空 → 增量捕获，摘要随新消息持续更新。
- **编辑重发可重算**：`truncate_history` 保留前缀后会清理 debounce、跨轮 digest 和结构化会话摘要，并递增 branch revision，但保留 seq counter；追加的新 user 消息仍取得更大的 seq，摘要随后基于当前分支重新建立。

### 11.3 兼容降级

- **存量无 seq 消息**：不补 seq，由摘要侧按 `isinstance(m.get("seq"), int)` 判定是否计入。
- **升级瞬间 `synced_seq==0` 且无 seq 消息**：走位置增量回退分支（`len(history) > old_synced_len` 时 `history[old_synced_len:]`），后续新增消息带 seq 后切回 seq 游标。
- **`last_seq < old_synced_seq`**（存量标度低或截断删除了高 seq 消息）：`above` 为空 → 走 recency 刷新并回落写回，安全，不会死循环。
- debounce key `memory:debounce:{uid}:{cid}` 状态含 `last_run/pending_turns/synced_len/synced_seq`，TTL 600s；任何异常均被外层捕获记录，不阻断聊天主链路。
