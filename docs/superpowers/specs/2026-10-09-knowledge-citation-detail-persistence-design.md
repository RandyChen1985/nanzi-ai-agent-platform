# 知识库引用详情持久化（Redis 旁路）设计

- **日期**：2026-10-09
- **状态**：已评审通过，待生成实现计划
- **作者**：AI 协作（用户确认方案）
- **关联模块**：知识库问答（RAG）、会话历史回放、引用弹层

## 1. 问题背景

知识库问答会在回答正文里写入 `[ID:n]` 引用标记，前端渲染为可点击徽章，点击弹出 `CitationPopover` 展示引用切片详情（文档名、匹配度、切片原文、查看原档）。

**刷新页面后，历史消息里的徽章点不开，下方引用列表也整块消失。**

根因：引用详情（`citations`）从未进入任何持久化路径，只活在实时 SSE 流与前端内存里。

| 检查点 | 结论 |
|---|---|
| `memory_service.add_message`（`app/services/ai/memory_service.py:681`） | 参数表与消息体均无 `citations` |
| `ai_agent_execution_history`（`app/models/audit.py:31`） | 无 `citations` 列 |
| `_persist_assistant_message_and_summary`（`app/services/ai/agent_service.py:126`） | 全文件无 `citations` 字样 |
| `process_timeline` 快照 | 无 `citation` 事件分支 |
| 前端历史构建（`frontend/src/views/EmbedChat.vue:7600`） | 不映射 `citations` |

因此刷新后：正文 `content`（含 `[ID:n]`）仍在 → 徽章照常渲染；但 `msg.citations` 为空 → `resolveCitation` 首行 `return null`（`EmbedChat.vue:8033`）→ 弹层静默不开；`v-if="msg.citations?.length"`（`EmbedChat.vue:1011`）为假 → 引用列表消失。

**关键矛盾**：`conversation_stream_journal` 的设计声明「事件日志只用于过程可视化，本轮最终内容一律以落库历史为准」（`app/services/ai/runtime/conversation_stream_journal.py:1-10`），但 `citations` 从未进入「落库历史」这条权威路径。在途轮次刷新靠 journal 重放尚能恢复，轮次定稿或 journal TTL（1800s）过期后即丢失。

## 2. 目标与非目标

### 目标

1. 引用详情在会话有效期内可恢复：刷新页面、切换会话、重进页面后，历史消息的引用徽章仍可点开弹层，引用列表正常展示。
2. 恢复失败时给出准确、分状态的用户提示，消除「点了没反应」。
3. 零 SQL 迁移、零 DB 表结构变更。
4. 不增加每轮 LLM 上下文重建的开销与污染风险。

### 非目标

- 不做 DB 落库（引用详情含切片全文，会让 `ai_agent_execution_history` 显著膨胀）。
- 不改会话消息体（避免被 `get_history` 逐条搬运进上下文重建）。
- 不改动 `memory_service` 现有 TTL（2592000s）与 `max_history_len`（100 条）配置。
- 不追求「永久保留」：超出 TTL 后引用详情按设计永久丢失，只提示已过期。

## 3. 方案选择

| 方案 | 说明 | 结论 |
|---|---|---|
| A. 落库 `ai_agent_execution_history.citations` JSON 列 | 永久可靠、翻页安全 | **否**：需两套迁移（`db-prod/`、`db-prod-pg/`），且每轮几十条切片全文入库，表膨胀明显 |
| B. 嵌入会话消息体（Redis） | 零新增 key | **否**：消息体会被 `get_history` 逐条读出解析，并作为 `server_history` 参与每轮上下文重建（`pipeline/steps/context_step.py:102`），搬运算力换不来收益，还有污染风险 |
| C. 独立 Redis key，按 `trace_id` 索引 | 精确取用、不进上下文、TTL 自动回收 | **采用** |
| D. 仅前端降级提示 | 改动最小 | 作为 C 的补充一并实施 |

方案 C 的 key 命名空间选择是刻意的：Redis 浏览器分组规则表（`frontend/src/views/SystemConfig.vue` 的 `REDIS_KEY_GROUP_RULES`）受 `tests/frontend/test_redis_browser_grouping_contract.py` 严格约束——45 条前缀必须按长度严格降序，且不得出现裸命名空间。

选用 `conversation:` 前缀（已映射到「会话记忆与上下文」组），**新增 key 无需改动规则表，零契约破坏**，运维在诊断页可直接看到。

## 4. 架构与数据流

```
工具 / RAG / 联邦查询 executor  yield {"type": "citation", "data": [...]}
        │
        ▼  pipeline/steps/execution_step.py 事件循环（累积，按 chunk_id 去重）
shared_state["knowledge_citations"]
        │
        ▼  pipeline/steps/finalize_step.py 的 if should_persist: 分支
SETEX conversation:citation_detail:{trace_id}  TTL = memory_service.ttl (30 天)
        │
        ▼  页面刷新 / 切换会话
GET /api/v1/chat/history                     （DB 主路径，EmbedChat）
GET /api/v1/chat/conversation/{id}/history   （Redis + 审计恢复路径，AgentDebug）
        │  按 trace_id 批量 MGET 合并进响应
        ▼
前端 msg.citations 恢复 → 弹层可点、引用列表可见
```

累积点与写入点分别对齐两条既有先例：

- 累积：`execution_step.py:189` 紧邻处已有 `reusable_result_status` 写入 `shared_state` 的同构模式。
- 写入：`finalize_step.py:151` 的 `if should_persist:` 与消息落库同一判定，天然保证「有历史消息才有引用详情」的对偶关系。

## 5. 详细设计

### 5.1 存储层（新增）

新增 `app/services/ai/knowledge_citation_store.py`：

```python
class KnowledgeCitationStore:
    KEY_TEMPLATE = "conversation:citation_detail:{trace_id}"
    MAX_ITEMS = 100           # 单轮最多保留的引用条数
    MAX_CONTENT_CHARS = 8000  # 单条切片正文截断上限
    MAX_PAYLOAD_BYTES = 1_048_576  # 序列化后总大小上限（1 MiB）

    async def save(self, trace_id: str, citations: list[dict]) -> bool
    async def load_many(self, trace_ids: list[str]) -> dict[str, list[dict]]
    async def delete_many(self, trace_ids: list[str]) -> int
    def sanitize(self, citations: list[dict]) -> list[dict]   # 纯函数，便于单测
```

约束：

- **TTL**：跟随会话历史，取 `memory_service.ttl`（默认 2592000s / 30 天）。沿用项目习惯在函数内 import，规避循环导入。
- **`sanitize` 为纯函数**：去重（优先 `chunk_id`，否则 `content` + `doc_name` 组合）、限条数、截断单条 `content`、超总大小再削，并只保留弹层展示与「查看原档」反查所需的字段（`id`/`chunk_id`/`doc_id`/`dataset_id`/`doc_name`/`content`/`similarity`/`page_no`/`source_type`/`link`）。抽成纯函数是为了让上限逻辑可脱离 Redis 单测。
- **错误处理**：所有读写包 `try/except`，失败仅 `logger.warning`，**绝不向上抛**——引用详情是旁路展示数据，任何情况下不得影响对话主流程与历史接口可用性。
- **`load_many` 用单次 `MGET`**，避免历史分页 20 条产生 20 次往返；单个 trace 反序列化失败只跳过该条。

### 5.2 累积层

`app/services/ai/pipeline/steps/execution_step.py` 事件循环内（现有 `reusable_result_status` 分支附近）新增：

```python
if chunk.get("type") == "citation" and isinstance(chunk.get("data"), list):
    # 与前端 mergeStreamCitations 同语义去重，避免多 executor 重复投递
    ...
    shared_state.setdefault("knowledge_citations", []).extend(deduped)
```

放在 `execution_step` 一处收口即可覆盖全部 executor 来源：知识库工具（`tools/knowledge_tool.py`）、RAG 执行器（`executors/rag_executor.py:137`）、联邦 ChatBI（`executors/federated_executor.py:1272-1273`）、ChatBI 自动引用（`chatbi_citation_utils.py:200`）。

### 5.3 写入层

`app/services/ai/pipeline/steps/finalize_step.py` 的 `if should_persist:` 分支内（与 `_persist_assistant_message_and_summary` 调用并列）落库引用详情。

- 与消息落库共用 `should_persist` 判定，保证对称：不落库的消息不产生引用详情，落库的消息其引用详情必然写入。
- 取消/异常收尾但已落库的轮次同样写入，不产生「有正文没引用」的不对称。
- 写入失败只 warning；此时历史回放退化为「提示已过期」，不影响对话。

### 5.4 读取层（两个历史接口）

**接口一：`GET /api/v1/chat/history`（DB 主路径，EmbedChat 使用）**

复用既有 `reusable_metadata_by_trace` 的合并模式（`app/api/v1/endpoints/chat.py:2166-2189` 采集、`:2211` / `:2237` 合并）：

- 新增 `citations_by_trace: Dict[str, list]`，对当前页所有 assistant `trace_id` 一次 `load_many`。
- 合并走 `item.model_copy(update={"citations": ...})`，与现有写法一致（分组与非分组两个分支都要改）。

**接口二：`GET /api/v1/chat/conversation/{id}/history`（Redis + 审计恢复路径，AgentDebug 使用）**

该函数有三条来源（Redis 正常 `:1039`、Redis+审计合并 `:1076`、DB fallback `:1166`）汇合于 `:1167`。在 `:1167` 之后、`return`（`:1168`）之前**统一补一次**：

```python
# 覆盖三种来源，避免在每个分支各补一遍
trace_ids = [m["trace_id"] for m in history if m.get("role") == "assistant" and m.get("trace_id")]
details = await knowledge_citation_store.load_many(trace_ids)
for message in history:
    if message.get("role") == "assistant":
        found = details.get(str(message.get("trace_id") or ""))
        if found:
            message["citations"] = found
```

**Schema**：`app/schemas/agent.py:177` 的 `AgentExecutionHistoryResponse` 新增 `citations: Optional[list] = None`。

接口二返回的是原始 dict，经 `ConversationHistoryResponse.messages`（`List[ConversationMessage]`）透传；`ConversationMessage` 已声明 `model_config = {"extra": "allow"}`（`app/api/v1/endpoints/chat.py:875`），因此新增的 `citations` 键无需改该模型即可原样下发。

### 5.5 清理

在 `delete_history`、`batch_delete_history`、`truncate_history` 中顺带 `delete_many` 对应 trace 的 key，避免删除会话后残留孤儿 key。

列入本次实施范围（不是可选项）：不做虽然也会由 TTL 回收，但删除会话后引用详情仍可被 trace 反查，与「删除即删除」的语义不符，且清理成本仅为一次批量 DEL。

### 5.6 前端

**（1）历史构建补 citations 并打历史标记**

| 位置 | 改动 |
|---|---|
| `EmbedChat.vue:7600`（主历史） | 补 `citations: item.citations ?? undefined`，补 `isHistory: true` |
| `EmbedChat.vue:9884`（可见性同步 update） | 补 `currentMsg.citations = latestServerItem.citations ?? currentMsg.citations` |
| `EmbedChat.vue:9903`（可见性同步 append） | 补 `citations: latestServerItem.citations ?? undefined` |
| `AgentDebug.vue` 历史构建（已有 `isHistory: true`） | 只需补 `citations: m.citations ?? undefined` |

`EmbedChat.vue` 的 `Message` 接口（约 `:2639`，已有 `citations?: any[]`）新增 `isHistory?: boolean`。

**（2）降级提示（区分两种状态）**

`handleShowCitation`（`EmbedChat.vue:8050` 与 `AgentDebug.vue:3004` 两处）在解析不到引用时不静默返回，改为：

- 先解析 anchor 元素，再解析 citation；
- `msg.isHistory` 为真 → `showToast("引用详情已过期，正文中的引用标记已无法展开", "warning")`；
- 否则（本轮流式进行中）→ `showToast("引用详情加载中，请稍候", "info")`。

**（3）规避 `Fig. n` 误报**

`MessageRenderer` 的徽章正则同时匹配 `[ID:n]`、`【ID:n】` 与 `Fig. n`（`MessageRenderer.vue:129`）。`Fig. 3` 这类正文并非知识库引用，若也弹「已过期」会误导。

处理：提示前用 anchor 元素的 `textContent` 校验，仅当形如 `[ID:n]` / `【ID:n】` 时才提示；`Fig. n` 维持现状静默。**无需改动 `MessageRenderer` 的 emit 签名**——anchor 元素的 `textContent` 即命中原文。

## 6. 边界与已知限制

1. **超过 TTL（30 天）后引用详情永久丢失**，仅提示已过期。这是本方案的唯一功能性缺口，也是有意的取舍（换取零迁移与零表膨胀）。
2. **`Fig. n` 误匹配面**保持现状：不提示，但徽章样式仍在（历史遗留兼容，不在本次范围）。
3. **admin 查看他人会话**：`/conversation/{id}/history` 使用 `_require_chat_user_id(user_info)` 取自己的 Redis 命名空间（既有行为，`reusable_result` 元数据同样如此）；但 `/history` 主路径按 `trace_id` 全局取，admin 仍能看到引用详情。该差异与现有行为一致，不额外引入。
4. **`sanitize` 截断**：极长的切片正文（>8000 字符）在弹层中显示为截断内容；单轮引用超过 100 条时只保留前 100 条。均为防御性上限，正常知识库问答不会触发。
5. **在途轮次刷新**不受本方案影响，继续走既有 stream journal 恢复；本方案只补「定稿后回放」。
6. **Redis 不可用**时：历史接口照常返回（无 citations），前端按「已过期」提示，对话主流程不受影响。

## 7. 测试策略

### 后端 pytest

- `tests/test_knowledge_citation_store.py`
  - `sanitize` 纯函数：去重（`chunk_id` 与 `content`+`doc_name` 两种键）、限条数、截断单条正文、超总大小削减；
  - `save` / `load_many` / `delete_many` 往返；TTL 取值等于 `memory_service.ttl`；
  - Redis 不可用 / 反序列化异常时**不抛异常**且返回空结果（旁路数据不得影响主流程）。
- `tests/test_knowledge_citation_history_merge.py`
  - `execution_step` 累积去重（多 executor 重复投递同一 `chunk_id` 只保留一条）；
  - `/history` 与 `/conversation/{id}/history` 在详情存在时合并、缺失时字段为空且响应结构不变；
  - `should_persist` 为假时不写引用详情（对称性）。

### 前端契约测试

新增 `tests/frontend/test_knowledge_citation_history_contract.py`（`pytest --confcutdir=tests/frontend`），钉住以下不变量：

- EmbedChat 主历史与可见性同步两处都映射 `citations`，且历史消息带 `isHistory`；
- `Message` 接口声明了 `isHistory`；
- 两个 `handleShowCitation` 均存在 `isHistory` 分支与两种提示文案，且**不再是无条件静默返回**；
- 提示前有 `[ID:n]` / `【ID:n】` 形态校验（防 `Fig. n` 误报）。

### 其他

- 更新 `tests/CHECKLIST.md`（项目硬性要求）。
- `vue-tsc --noEmit` 类型检查。
- 可在 `tests/frontend/test_redis_browser_grouping_contract.py` 的生产样本用例中补一条 `conversation:citation_detail:<trace>` → 「会话记忆与上下文」，锁住归组行为。
- **无 SQL 迁移**，无需 `db-prod/` 与 `db-prod-pg/` 变更。

## 8. 兼容性与回滚

- **兼容性**：纯新增字段与新增旁路写入。旧客户端收到多余 JSON 字段无副作用；新客户端在详情缺失时走降级提示，不依赖后端改动即可运行。历史响应新增字段为可选，不影响既有消费方。
- **回滚**：移除写入调用与前端映射即可回到当前行为，无需数据回滚；已写入的 Redis key 由 TTL 自动回收，不产生残留。

## 9. 涉及文件清单

**新增**

- `app/services/ai/knowledge_citation_store.py`
- `tests/test_knowledge_citation_store.py`
- `tests/test_knowledge_citation_history_merge.py`
- `tests/frontend/test_knowledge_citation_history_contract.py`

**修改（后端）**

- `app/services/ai/pipeline/steps/execution_step.py`（累积）
- `app/services/ai/pipeline/steps/finalize_step.py`（写入）
- `app/schemas/agent.py`（响应字段）
- `app/api/v1/endpoints/chat.py`（两个历史接口合并 + 清理）

**修改（前端）**

- `frontend/src/views/EmbedChat.vue`（历史映射、`isHistory`、降级提示）
- `frontend/src/views/AgentDebug.vue`（历史映射、降级提示）

**修改（测试与文档）**

- `tests/CHECKLIST.md`
- `tests/frontend/test_redis_browser_grouping_contract.py`（补样本，可选）
