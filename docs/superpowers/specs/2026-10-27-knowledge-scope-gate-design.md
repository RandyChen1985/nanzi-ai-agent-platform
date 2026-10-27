# 知识库检索范围门禁（Scope Gate）设计

- 日期：2026-10-27
- 状态：待评审
- 涉及链路：AgentService → AgentDispatcher → KnowledgeExecutor → KnowledgeAgentRunner → search_knowledge_base
- 前置讨论：知识库智能体的检索范围来源与 100 库场景下的召回稀释问题

## 1. 背景

### 1.1 问题

知识库轮次在用户没有显式选择知识库时，检索范围是 `merge(智能体绑定的 dataset_ids, 用户可访问的知识库)`
（`app/services/ai/context_manager.py` 的 `setup_context`）。用户可访问的知识库越多，一次性送给
RAGFlow 的 `dataset_ids` 越长：

```python
# app/services/ai/ragflow_client.py
payload = {
    "dataset_ids": dataset_ids,   # 100 个可访问库 → 100 个 ID
    "top_k": top_k,               # 默认 5，全局，不是每库 5
    "page_size": top_k * 3,       # 候选池 15 条，全局共享
}
```

由此产生两个后果：

1. **延迟**：单次多库联合检索的耗时随库数增长，具体幅度需实测（`POST /api/portal/ragflow/retrieval-test`，
   权限 `element:knowledge:test_retrieval`）。
2. **召回稀释**：`top_k` 是全局的，100 个库竞争 5 个名额，单一专库的最佳片段可能被其他库的片段挤出；
   且跨库相似度分数不可直接比较，`similarity_threshold`（默认 0.45）在多库下失去精确含义。

第 2 点通常比第 1 点更影响体验：典型症状是「答案来自不相关的库」，而不是「等得久」。

### 1.2 现状能力盘点

| 能力 | 位置 | 现状 |
|---|---|---|
| 显式选库通道 | `body.knowledge_dataset_ids` → `request_dataset_ids` → 硬范围 | 已存在，前端 EmbedChat / AgentDebug 在用 |
| 库级相关性匹配 | `knowledge_catalog.match_knowledge_catalog` | 已存在，但只返回单个 best_id，且仅用于外层路由判断，未用于收窄检索 |
| 会话级资源范围 | `ConversationResourceService`（Redis，TTL 30 天，含 `knowledge_bases` 字段） | 已存在，但**未参与检索范围计算** |
| 向用户提问的卡片 | `ask_user_question` 工具 + 前端 `userQuestion.ts` | 已存在，支持 2-12 选项、多选、补充输入、取消 |
| 会话表 | —— | **不存在**，对话历史在 AgentScope 状态与前端，会话资源在 Redis |

## 2. 目标与非目标

### 目标

- 当检索范围确实过大且用户未表态时，用一次轻量交互让用户确认范围，避免无差别全量检索。
- 多数情况下用户一次点击即可结束，不需要翻找知识库列表。
- 该门禁对所有入口一致生效（EmbedChat、AgentDebug、API、分享页）。
- 门禁任何环节失败都不得阻塞用户提问。

### 非目标

- 不做知识库正文的语义路由（库画像向量化），本设计只依赖库级元数据匹配。
- 不改变 `search_knowledge_base` 工具本身的检索、引用编号、权限过滤语义。
- 不改变智能体绑定知识库的配置方式。

## 3. 方案

### 3.1 生效条件（四道与门）

四条件**同时**成立才弹卡：

```
① 是知识库轮次（capabilities 含 knowledge_base，走 KnowledgeAgentRunner）
② 本轮没有显式选库（无知识库附件、无 knowledge_dataset_ids 参数）
③ 会话内尚未确认过范围（ConversationResourceService 无 knowledge_scope）
④ effective_dataset_ids 数量 > 阈值（配置 knowledge_scope_gate_threshold，默认 5）
```

条件 ④ 必须用 `effective_dataset_ids`（合并后的实际检索范围），不能用「用户可访问库总数」：
否则「智能体只绑 2 个库、用户可访问 100 个」会每次提问都触发弹卡，而正确答案本来就是搜那 2 个。

以下轮次一律跳过门禁：目录类提问（问「我能看哪些库」）、追问类（复用上一轮 `[ID:n]`）、
结果分析/格式调整类、RAGFLOW 引擎直连轮次。

### 3.2 卡片内容

用现有 `ask_user_question` 卡片渲染，选项四个：

| 序号 | 选项 label | option.id | 说明 |
|---|---|---|---|
| ① | 在 3 个最相关库中检索 | `__scope_ranked__` | 推荐项，列出库名；由库级匹配 top 3 生成 |
| ② | 全部 N 个库 | `__scope_all__` | 描述标注「联合检索，较慢，可能召回不相关片段」 |
| ③ | 仅智能体默认（M 个） | `__scope_bound__` | 智能体未绑库时改为「仅系统默认库（K 个）」；两者都为 0 时置灰不可选 |
| ④ | 指定其他库… | `__scope_pick__` | 点击打开库选择器（唯一需要前端的交互） |

卡片同时开启 `is_multi_select=false`（范围是单选语义）、`allow_custom_input=true`（允许用户补充库名）。

选项①是体验关键：它让多数用户一次点击结束，不必进入 100 个库的列表。

### 3.3 「指定其他库」的实现约束

`ask_user_question` 的选项上限是 12 个，100 个库无法放进选项列表。因此第 ④ 项不能靠选项实现，
必须在前端把该选项渲染成一个入口，点击后打开库选择器（复用现有知识库附件选择通道，需带搜索与分组）。

这是本方案中**唯一必须改动前端**的地方。若要求前端零改动，则只能去掉第 ④ 项，退化为
「三选一 + 补充输入写库名」，100 库场景下靠文字猜库名命中率低，不推荐。

### 3.4 会话范围存储

平台没有会话表（对话历史在 AgentScope 状态与前端，会话级资源在 Redis），因此不新增表字段，
复用 `ConversationResourceService`（`app/services/conversation_resource_service.py`）。

在该服务的 scope 中新增两个独立子结构，不动现有 `knowledge_bases`（避免改变「会话挂载 N 个
知识库」的既有展示语义）：

```json
{
  "project_name": "",
  "datasets": [],
  "knowledge_bases": [],
  "skills": [],
  "mcp_tools": [],
  "knowledge_scope": {
    "mode": "all | bound | ranked | picked",
    "dataset_ids": ["ds_a", "ds_b"],
    "confirmed_at": "2026-10-27T11:20:00+08:00"
  },
  "pending_scope_gate": {
    "gate_id": "sg_ab12cd34",
    "ranked_ids": ["ds_a", "ds_b", "ds_c"],
    "bound_ids": ["ds_d"],
    "all_ids": ["..."]
  }
}
```

- `knowledge_scope` 是**已确认**的范围；`pending_scope_gate` 是**待回答**的候选快照。
- `pending_scope_gate` 的存在有两个作用：让服务端在用户回答后能独立还原出具体 ID（见 3.5），
  以及在卡片重复投递时用 `gate_id` 去重。
- `pending_scope_gate` 的失效条件：用户回答了（转为 `knowledge_scope`）、用户取消了（清除）、
  或用户带着新问题重新提问（旧快照作废并按新问题重新生成，因为 `ranked_ids` 已经不同）。
- `_empty_scope()` 与 `_decode_scope()` 需同时扩展，保证旧键值反序列化后这两个字段为 `None`。
- 写入沿用 `ConversationResourceService.replace()`。注意它是整体覆盖，写入前必须先 `get()`
  取当前 scope 再合并写回，否则会丢掉 `datasets` / `skills` / `mcp_tools`。
- Redis 不可用时按 `get()` 的既有降级返回空 scope，此时门禁按「未确认」处理并正常弹卡；
  若连弹卡也无法完成，按下文 3.7 退让规则放行。

### 3.5 生效链路

卡片选项的 `option.id` 是标记（`__scope_ranked__` 等），**不是 dataset ID**，所以选中结果到
具体 ID 的映射必须由边提供，有两条通道：

**通道 A（主，前端）**：发卡时前端从 `pending_scope_gate` 快照（随卡片事件下发或由接口读取）
取出对应 `ranked_ids` / `bound_ids` / `all_ids`，用户点选后把它们写入下一轮的
`body.knowledge_dataset_ids`。这条通道对用户可见、可解释。

**通道 B（兜底，服务端）**：用户回答的下一轮，若 `knowledge_dataset_ids` 为空但会话里存在
`pending_scope_gate`，则从回执解析 `selected_option_ids` 中的 `__scope_*` 标记，用快照还原出
具体 ID，注入本轮范围。这条通道保证第三方客户端（不实现通道 A 的）也能正常工作，
且不依赖前端改动。

两条通道的结果最终都汇到同一处：

```
setup_context 命中 request_dataset_ids 或 ScopeGate 注入 → 硬范围
  → resolve_knowledge_dataset_ids 直接使用
  → 把 {mode, dataset_ids, confirmed_at} 写入 knowledge_scope，并清除 pending_scope_gate
```

后续轮次读到会话范围后：
- `mode=picked/ranked` → 直接用 `dataset_ids` 作为检索范围，跳过门禁；
- `mode=all` → 使用当前全量逻辑，跳过门禁；
- `mode=bound` → 使用智能体绑定库，跳过门禁。

输入框上方显示徽标「范围：财务制度库 等 3 个 ▾」，点击徽标可重新选择（清空 `knowledge_scope`
后重新判定）。

### 3.6 库级匹配需要新函数

现有 `match_knowledge_catalog` 只返回单个 `best_id`，无法支撑「3 个最相关库」。需要新增：

```python
def rank_knowledge_catalog(
    query: str,
    catalog: AuthorizedKnowledgeCatalog,
    *,
    limit: int = 3,
) -> list[tuple[str, float]]:
    """按现有打分逻辑返回 top-N (dataset_id, score)，分数低于下限的不返回。"""
```

复用现有 `_signals` / `_item_signals` 与打分公式（`0.68 * query_coverage + 0.22 * metadata_coverage
+ min(name_overlap,3) * 0.1`），仅把「取 best」改为「排序取前 N 并过滤低分」。

`match_knowledge_catalog` 保持原样不动，避免影响现有外层路由调用方。

### 3.7 异常与退让

| 情况 | 行为 |
|---|---|
| 用户取消（回执 `cancelled: true`） | 本轮不检索，回复「已取消」，清除 `pending_scope_gate`。同一问题的后续轮次不再追问；只有用户**重新发起一个新问题**且范围仍未确认时，才会再次触发门禁 |
| 用户补充输入写了库名 | 走目录匹配，命中则用命中的库，未命中则按「全部」执行并在回答里说明一句 |
| 卡片构造失败 / 目录不可用 / Redis 不可用 | **直接放行**，退回当前全量行为，不阻塞提问 |
| 用户选「全部」 | 接受，检索日志记 `scope_mode=all` |
| 用户选的库在 RAGFlow 侧已失联 | 沿用现有存活过滤与「所选知识库已失联」终止语义 |

统一原则：门禁是优化项，任何一环失败都降级到现有行为，不允许比现在更差。

## 4. 组件设计

### 4.1 新增 pipeline 步骤

在 `app/services/ai/pipeline/steps/` 新增 `scope_gate_step.py`，插入位置在 `ContextStep` **之后**、
`RouteStep` 之前：

```
PipelineRunner: Preflight → Context → ScopeGate → Route → Assemble → Execution → Finalize
```

**为什么必须在 Context 之后**：条件 ④ 依赖 `effective_dataset_ids`，而这个值由 `ContextStep`
里的 `setup_context` 计算（`merge(智能体绑定, 用户可访问)`）。放在 Context 之前则拿不到该值，
只能重算一遍，等于把范围逻辑写两份，必然漂移。

**为什么在 Route 之前**：路由（选专家/选执行器）是有成本的一步，范围未确认时不应该先跑完路由
再被打断。判断「是不是知识库轮次」不需要 `RouteStep` 的结果，用智能体配置的
`capabilities` / `engine_type` 即可。

该步骤内部完成：判定 → 构造选项 → 写入 `pending_scope_gate` → 调用 `ask_user_question`。
若是 `awaiting_user` 则终止本轮（不进入 Route/Assemble/Execution），否则原样透传给后续步骤。
非知识库轮次直接放行。

理由：放在 pipeline 而不是 runner 里，四个入口共用同一门禁，避免 EmbedChat 与 API 行为分叉。

### 4.2 新增判定函数

`app/services/ai/knowledge_utils.py`：

```python
async def resolve_knowledge_scope_gate(
    *,
    query: str,
    ctx: AgentContext | None,
    conversation_scope: dict | None,
    threshold: int,
) -> KnowledgeScopeGate | None:
    """返回 None 表示不需要门禁；返回 gate 表示需要用户确认。"""
```

`KnowledgeScopeGate` 为 dataclass：`gate_id`、`options`（已构造好的四选项）、`ranked_ids`、
`bound_ids`、`all_ids`、`total_count`、`bound_count`。后四个 ID 列表用于写入
`pending_scope_gate` 快照，供通道 B 还原。

### 4.3 配置项

| 键 | 默认 | 说明 |
|---|---|---|
| `knowledge_scope_gate_enabled` | `true` | 总开关 |
| `knowledge_scope_gate_threshold` | `5` | 触发门禁的库数阈值 |
| `knowledge_scope_gate_ranked_limit` | `3` | 选项①中「最相关库」的数量 |

走 `ConfigService`，与 `knowledge_ragflow_*` 同组，支持热更新。

### 4.4 前端改动

| 位置 | 改动 |
|---|---|
| `frontend/src/views/EmbedChat.vue`、`AgentDebug.vue` | 渲染 `ask_user_question` 卡片（现有组件已支持，需确认 `__scope_pick__` 分支的点击行为） |
| 库选择器 | 「指定其他库」点击后打开，需带搜索框（100 库场景必需） |
| 输入框上方徽标 | 显示当前会话范围，可点击重选 |
| `frontend/src/utils/userQuestion.ts` | 提交回答时，把选中的 dataset_id 同时写入 `knowledge_dataset_ids` |

## 5. 埋点

复用 `knowledge_retrieval_log_service`，新增字段 `scope_mode`（`all` / `bound` / `ranked` / `picked` / `unset`），
并新增三类事件：

- `knowledge_scope_gate_shown`：触发次数（按会话去重计数）
- `knowledge_scope_gate_selected`：四个选项的选择分布
- `knowledge_scope_gate_bypassed`：用户取消或补充输入未命中的次数

上线后用于验证：阈值 5 是否合适、选项①的推荐准确率、选择后命中率与延迟的变化。
没有这组数据无法判断门禁是否真的改善了检索质量。

## 6. 测试计划

| 层 | 用例 |
|---|---|
| 单元 `tests/ai/test_knowledge_scope_gate.py` | 四道与门各自不满足时不触发；`effective` 与「可访问总数」区分正确；阈值边界（=5 不触发，=6 触发） |
| 单元 `tests/ai/test_knowledge_catalog.py` | `rank_knowledge_catalog` 排序、top-N、低分过滤；`match_knowledge_catalog` 行为不变 |
| 单元 | 会话范围读写：`_decode_scope` 兼容旧值（无 `knowledge_scope` 字段）；Redis 不可用时降级为空 scope |
| 集成 `tests/ai/runners/test_knowledge_runner_scope.py` | 门禁触发后本轮不检索（`search_knowledge_base` 未被调用）；用户选库后下一轮按硬范围检索；取消后不重复问 |
| 前端契约 `tests/frontend/test_user_question_scope_contract.py` | 回执写入 `knowledge_dataset_ids`；徽标展示 |

## 7. 风险与回滚

| 风险 | 处理 |
|---|---|
| 门禁过于频繁打扰用户 | 默认阈值 5 + 会话记忆；出问题时可 `knowledge_scope_gate_enabled=false` 一键关闭 |
| 选项①推荐不准 | 描述里不承诺结果，用户可见库名；后续用埋点评估准确率再决定是否保留 |
| 前端库选择器 100 库不可用 | 上线前确认搜索与分组；否则第 ④ 项先隐藏，只保留前三项 |
| `replace()` 整体覆盖导致丢字段 | 写入前先 `get()` 当前 scope，合并后整体写回；补单测 |

回滚方式：关闭配置开关即恢复现有全量检索行为，无数据迁移、无需回滚 SQL（本方案不涉及 DDL）。

## 8. 实施顺序

1. 后端判定与选项构造（`resolve_knowledge_scope_gate` + `rank_knowledge_catalog`）+ 单元测试
2. pipeline 接入 `ScopeGateStep` + 集成测试（此时可用 `ask_user_question` 直接验证链路）
3. 会话范围读写（`ConversationResourceService` 扩展）+ 测试
4. 前端：库选择器接入第 ④ 项、范围徽标、回执写回
5. 埋点接入与看板
6. 灰度：先对内部账号开启，观察两周选择分布与命中率变化后再全量

## 9. 未决问题

- 阈值是否按「库数」还是按「库数 × 平均文档量」判断更合理（当前按库数，简单可解释）。
- 选项①的三库是否应允许用户微调（当前不支持，选完即锁定）。
- 多智能体场景下，同一会话切换智能体时是否应清空 `knowledge_scope`（当前设计：清空后重新判定）。
