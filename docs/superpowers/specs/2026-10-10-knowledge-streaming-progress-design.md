# 知识库问答流式进度反馈设计

## 1. 问题背景

用户在知识库助手里提问后观察到：思考卡片停在最后一项「工具完成: search_knowledge_base」，之后几秒到十几秒没有任何进展信号，页面只有底部一句「AI 正在努力生成回复中，请稍候。」；随后答案**整段一次性出现**，没有逐字输出。用户的原话是「检索已完成，AI 还没输出之前这段时间在干嘛」。

### 根因

`app/services/ai/runners/knowledge_agent_runner.py` 的知识库问答在调用模型时套了一个「事实一致性网关」重试循环（`:912`-`:920`）：

- `:934-942` 的 `async for` 循环**只往 `chunks_buffer` 里 append，没有任何 `yield`**；
- `:1051-1068` 才在网关判定结束后把整个缓冲区**一次性 flush** 给前端。

而「工具完成: search_knowledge_base」是在**预检索阶段**直接 `yield` 的（`:445-457`），不受缓冲影响。于是它成了前端收到的最后一个事件，卡片自然停在那里。

之所以这样设计，是因为网关需要**完整回答文本**才能判定幻觉（`:971` 的 `HallucinationEvaluator.evaluate(response=full_text)`）；若正文边生成边显示，判定不合格时就已经被用户看到、收不回来。

### 由此产生的三个后果

1. **模型生成全程前端零事件**：卡片停在工具项，用户无法判断是在正常等待还是卡死；
2. **生成结束还要多等一次 LLM 调用**（幻觉评估本身要调模型），等待被二次拉长；
3. **正文没有流式**：答案在网关通过后一次性推送。

本设计解决第 1 点（并顺带改善过程可见性），第 2 点属于网关固有成本，第 3 点见「非目标」。

## 2. 目标与非目标

### 目标

- 知识库问答在**模型生成期间**持续向 SSE 推送**过程性事件**（模型调用、阶段日志、思考等），使思考卡片不再停在工具完成项；
- 预检索完成后立即推送一个**知识库语义化状态项**（「检索完成，正在组织回答」），并在模型开始产出正文时收尾；
- **保持网关的安全语义不变**：正文仍在网关通过后才对用户可见；
- 落库的 `process_timeline` 不出现残留的「进行中」项。

### 非目标

- **不做真流式正文**（方案 B：正文也实时推送 + 网关失败用 `retraction` 撤回）。改动面与风险显著更大（缓冲与撤回的交互、计费口径、历史落库取哪版文本、撤回后前端重放），且用户可能已瞥见被判定为幻觉的内容，本设计不做，留待后续单独评估；
- **不改动幻觉网关的判定逻辑、阈值与重试次数**；
- **不改动检索链路**（RAGFlow 调用、引用台账、检索指标埋点）；
- **不新增 SSE 事件类型**，复用现有 `log` 与 `model_call` 事件；
- 不处理非知识库 runner 的同类缓冲问题（本次只改 `KnowledgeAgentRunner`）。

## 3. 方案选择

| 方案 | 做法 | 结论 |
| --- | --- | --- |
| **A（选用）** | 循环内把**过程性 chunk** 实时 `yield`，**只缓冲正文 chunk** | 精准消除"卡片停住"，不动安全语义，改动集中在一处循环 |
| B | 正文也实时推送，网关失败用 `retraction` 撤回 | 体验最好但改动与风险最大；本次不做（见非目标） |
| C | 只改前端兜底提示 | 治标不治本，正文依旧不流式，前端只能猜后端在做什么 |

在方案 A 之上追加**知识库专属文案**：通用的「模型调用: \<模型名\>」对业务用户偏技术化，补一个「检索完成，正在组织回答」让语义更清楚。

### 为什么不复用已有的「模型调用」项就够了

后端 `runtime/agentscope/event_stream.py:380-413` 早已产出 `{"type": "model_call", phase: "start"|"end"}`，前端 `utils/agentscopeSseHandlers.ts:429-465` 的 `handleModelCallEvent` 也会把它渲染成 `status: "pending"` 的「模型调用: \<模型名\>」项，EmbedChat 也已经引入了分发器（`EmbedChat.vue:2473`、调用点 `:8808/:9507/:9605`）。

也就是说：**这项能力本来是通的，只是被 `:934-942` 的缓冲整体挡住了**。方案 A 让过程事件重新流动之后，「模型调用」项自然会出现——这是本设计最直接的收益。专属状态项是在此之上的语义增强，用于覆盖「预检索刚结束、模型调用事件尚未到达」那段空档。

## 4. 架构与数据流

改动集中在两处：`KnowledgeAgentRunner` 的模型执行循环，以及落库前的 timeline 收尾。

```
预检索完成 ──yield──▶ log「工具完成: search_knowledge_base」        （既有，直接转发）
           ──yield──▶ log「检索完成，正在组织回答」pending category=knowledge   ◀── 新增
                     │
                     ▼
         ┌───────────────── 网关重试循环（每轮调用一次模型）─────────────────┐
         │  AgentScope 事件流                                                │
         │    MODEL_CALL_START ─▶ model_call:start ──yield（实时转发）        │  ◀── 改动点
         │    THINKING/NARRATION/LOG …            ──yield（实时转发）        │  ◀── 改动点
         │    TEXT_DELTA（含 content）            ──▶ chunks_buffer          │  ◀── 仍缓冲
         │  首个正文 chunk 到达 ──yield──▶ log「检索完成，正在组织回答」success│  ◀── 新增
         └───────────────────────────────────────────────────────────────────┘
                     │
                     ▼
         幻觉评估（额外一次 LLM 调用）── 通过 ─▶ flush chunks_buffer（正文）──▶ 前端渲染答案
                                      └ 不通过 ─▶ yield 拦截 log + 重试
```

关键点：**正文（含 content 的 chunk）与非正文（过程事件）互斥分流**，因此 flush 时不存在重复推送。

## 5. 详细设计

### 5.1 后端：按 content 分流（`knowledge_agent_runner.py:934-942`）

```python
saw_error = False          # 新增：替代原先"扫 buffer 找 error"
synthesis_closed = False   # 新增：状态项只关闭一次

async for chunk in self._execute_with_agentscope_native_agent(**execute_kwargs):
    if "content" in chunk:
        content = str(chunk.get("content") or "")
        if self._valid_citation_ids:
            content = filter_invalid_citation_markers(content, self._valid_citation_ids)
        full_text += content
        chunk = dict(chunk)
        chunk["content"] = content
        chunks_buffer.append(chunk)
        if not synthesis_closed:
            synthesis_closed = True
            yield self._knowledge_synthesis_event(status="success")
    else:
        if chunk.get("type") == "error":
            saw_error = True
        yield chunk
```

**切分依据是 `"content" in chunk`，而不是 type 白名单**，理由：

- 需要缓冲的是「会呈现给用户的答案正文」，其判据就是携带 `content`。`process_narration` 承载的同样是模型文本增量，天然被归入缓冲，符合"正文不提前暴露"；
- 其余事件（`model_call`、`log`、`reasoning_content`、`meta`、`context_*` 等）不携带 `content`，都是过程信号，可以安全实时转发；
- 白名单需要随上游事件类型演进不断维护，容易漏；按 content 判定是稳定的结构性判据。

### 5.2 缓冲区语义收窄与 error 标志位

原来 `chunks_buffer` 装**全部** chunk。一旦过程事件改为实时转发，若 buffer 仍装全部，flush 时就会**重复推送**。因此：**buffer 只装含 `content` 的正文 chunk**，与实时转发的那部分互斥不重叠，`:1051` 的 flush 逻辑本身不需要改动。

连带必须改一处：`:944` 原本是 `if any(chunk.get("type") == "error" for chunk in chunks_buffer)`，而 error chunk 不带 `content`、现在会走实时转发分支、不再进 buffer，**这个判断会失效**。失效的后果很严重——代码注释（`:945-946`）明确写了：错误事件是终止信号，若进入事实评估/反思循环，会把安全拦截误判成幻觉并覆盖原始错误。所以必须改为循环内的 `saw_error` 标志位。

### 5.3 知识库专属状态项

**发出位置**：预检索完成、确定有无引用之后（`:754` 的 `knowledge_service_unavailable` 检查通过之后，`prefetch_had_citations` 已于 `:750` 确定），紧跟工具完成事件——正是前端收到的下一件事。

**事件内容**：

```python
{
    "type": "log",
    "id": "knowledge_synthesis",
    "title": "检索完成，正在组织回答",
    "details": (
        f"已获取 {len(self._valid_citation_ids)} 条资料，正在生成回答…"
        if prefetch_had_citations
        else "未检索到相关资料，正在组织回复…"     # 0 条时不得谎报"已获取 0 条"
    ),
    "status": "pending",
    "category": "knowledge",
}
```

单独抽出 `_knowledge_synthesis_event(status)` 辅助方法，保证发出与收尾用的是**同一个 id 与 title**（前端按 id 归并，title 不一致会错乱）。

**收尾时机**：循环内**首个正文 chunk 到达时**（见 5.1），发同 id、`status: "success"` 的事件。

不选"等 flush 之后再收尾"的原因：flush 发生在网关判定结束之后，那时用户仍没看到任何正文；如果状态项拖到那时才收尾，中间会出现「状态项已完成 + 模型调用已完成 + 正文尚未到达」的空档，卡片会再次停住。而在首个正文 chunk 时收尾，`model_call` 的 pending 项已经实时转发出来，卡片无缝接上「模型调用: \<模型名\> · 进行中」。

**为什么用 `category: "knowledge"`**：该类别已被前端识别——`addEmbedLogFromStream` 会把含「检索/知识/引用」的条目归入 `knowledge`；`utils/agentscopeSseHandlers.ts:388` 的 `STALE_PENDING_CATEGORIES` 已包含 `"knowledge"`（具备超时保护）；而 `utils/processTimeline.ts:604-607` 的 `NON_LIVE_TIMER_CATEGORIES` 只有 `permission` / `external`，因此本项**会显示实时秒表**，用户能看到已经等了多久，正合诉求。

### 5.4 落库收尾扩展（`process_timeline_snapshot.py`）

`finalize_process_timeline` 目前只把 `category == "model"` 的 pending 项收尾（`process_timeline_snapshot.py:610-615`）。新增的 `knowledge` 状态项若因异常中断仍是 pending，刷新后的历史回放会**永远显示「进行中」**，因此必须一并收尾。改动就落在该分支的条件上：

```python
if copied.get("status") == "pending" and copied.get("category") in ("model", "knowledge"):
    copied["status"] = "success"
```

**明确不采用"收尾所有 pending log"**：`permission` / `external` 是"等待用户操作、机器没在跑"的类别（对应前端 `NON_LIVE_TIMER_CATEGORIES`），历史回放时应当保持待处理状态，一律收尾会破坏其语义。本次只按需扩展 `knowledge`；其余类别（`agent` / `tool` / `sql` 等）是否存在同类残留问题不在本次范围，如发现另开。

### 5.5 前端

**无需改动**。理由：

| 关注点 | 现状依据 |
| --- | --- |
| `model_call` 事件处理 | `EmbedChat.vue:2473` 已引入 `dispatchAgentscopeStreamEvent`，调用点 `:8808/:9507/:9605`，其 `case "model_call"`（`agentscopeSseHandlers.ts:909`）调用 `handleModelCallEvent` |
| `log` 事件处理 | `addEmbedLogFromStream`（`EmbedChat.vue:8640`）按 `category` 写入 `msg.logs`，不过滤 `knowledge` |
| 卡片头部当前步骤 | `ChatExecutionTimeline.vue:746-749` → `resolveTimelineCurrentStep`（`processTimeline.ts:222-226`）**反向优先取 pending 项**，返回 `xxx · 进行中` |
| 流结束收尾 | `EmbedChat.vue:9647` 调 `finalizeAllPendingStreamLogs` |

图标方面，时间线没有 `knowledge` 专属图标，会走通用分支；本次不新增图标（如后续需要可单独做）。

## 6. 边界与已知限制

| 场景 | 行为 |
| --- | --- |
| `error` chunk | 实时转发 + `saw_error` 置位 → 立即 flush 并终止，不进幻觉评估；相对现状，错误**更早**可见 |
| 网关重试 | 第一轮过程事件已实时发出（用户能看到"第一次尝试"）；重试的 `kb_reflection_*` 拦截 log 照常；新一轮 `model_call` 到达时 `handleModelCallEvent` 会先收尾上一条 `model` pending，不残留两个转圈 |
| 状态项跨重试 | `synthesis_closed` 在循环外声明，第二轮正文到达时已置位，不会重复关闭 |
| 无引用（0 条） | 文案切为「未检索到相关资料，正在组织回复…」；此时允许模型补充检索，若再调工具会产生新的工具项 |
| 网关最终失败 | flush 正文 + 追加风险提示，逻辑不变（`:1051-1072`） |
| 轮内多次检索 | 状态项仍只发出一次；后续检索的工具项按既有逻辑追加 |

### 已知行为变化：token 计数

每轮重试开始时 `chunks_buffer = []`（`:921`）会丢弃上一轮 chunk，因此**现状下重试轮的 `model_call` end 被丢弃、token 不计入**。改为实时转发后，重试轮的 token 会照常累加，页面上的 token 数会比过去**偏大**。

这是更准确的口径（重试确实消耗了 token），但确实改变了显示数字，属于**有意接受的行为变化**，需要在提交说明与测试清单中注明。

### 其他限制

- 正文依旧不是逐字流式，用户在网关通过前看到的仍是"等待"状态；这是本设计的边界，真流式属方案 B；
- 幻觉评估本身是一次额外 LLM 调用，等待时长中包含它，本设计不改变这部分成本。

## 7. 测试策略

### 后端契约测试（新增 `tests/test_knowledge_streaming_contract.py`）

沿用项目既有的源码契约风格（`tests/test_knowledge_citation_history_contract.py` 同款），逐条锁定：

1. 循环内**过程事件立即 yield**：断言 `yield chunk` 位于 `else` 分支；
2. `chunks_buffer.append` **只出现在 content 分支内**（防止退回"全部缓冲"）；
3. error 走**标志位**：断言 `saw_error` 存在且 `:944` 的判断不再遍历 buffer 找 error；
4. 预检索后 yield `knowledge_synthesis` pending 项：断言 id / title / `category: "knowledge"` / `status: "pending"`；
5. 首个正文 chunk **关闭状态项**：断言 `synthesis_closed` 标志与 `status="success"` 的发出；
6. 落库收尾覆盖 `knowledge` 类别：断言收尾条件为 `("model", "knowledge")`，且**未**把 `permission` / `external` 纳入收尾。

### 变异验证

每条契约至少一个变异并确认被捕获（项目事实交付门槛）：

- 把实时转发改回纯缓冲（过程事件也 append 进 buffer）；
- 把 error 判定改回扫 buffer；
- 去掉状态项发出；
- 去掉状态项关闭逻辑；
- 把关闭时机从"首个正文 chunk"改到"flush 之后"；
- 把落库收尾条件改回只认 `model`。

### 前端契约测试

无功能改动，补一条断言锁住 `knowledge` 类别可显示 pending（防止回退），加入现有前端契约文件。

### 人工验证

1. 知识库提问 → 检索后立刻出现「检索完成，正在组织回答 · 进行中 Ns」；
2. 紧接着出现「模型调用: \<模型名\> · 进行中」（证明过程事件已实时流动）；
3. 答案出现后两项均已收尾，无残留转圈；
4. 提一个知识库无相关资料的问题 → 状态项文案为「未检索到相关资料，正在组织回复…」；
5. 刷新页面看历史回放 → 上述条目都在，且**没有任何一项停在「进行中」**。

## 8. 兼容性与回滚

- **无表结构变更**、**无迁移脚本**、**无新增依赖**、**无配置变更**；
- **不新增 SSE 事件类型**，新增的 `log` 事件对旧前端是已支持类型，向后兼容；
- 回滚方式：还原 `knowledge_agent_runner.py` 与 `process_timeline_snapshot.py` 两个文件即可，无数据残留；
- 影响面仅限知识库问答路径（`KnowledgeAgentRunner`），其他 runner 行为不变。

## 9. 涉及文件清单

| 文件 | 变更 |
| --- | --- |
| `app/services/ai/runners/knowledge_agent_runner.py` | chunk 分流实时转发、buffer 语义收窄、`saw_error` 标志位、状态项发出与收尾 |
| `app/services/ai/runtime/agentscope/process_timeline_snapshot.py` | 落库收尾覆盖 `knowledge` 类别的 pending 项 |
| `tests/test_knowledge_streaming_contract.py`（新增） | 上述 6 条后端契约 |
| `tests/frontend/test_knowledge_streaming_contract.py`（新增） | `knowledge` 类别 pending 可显示的防回退契约 |
| `tests/CHECKLIST.md` | 新增本次交付记录 |
