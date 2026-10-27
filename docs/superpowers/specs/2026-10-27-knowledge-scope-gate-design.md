# 知识库检索范围门禁（Scope Gate）设计

- 日期：2026-10-27
- 状态：已实施（实现与本文的偏差见 §10）
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

条件 ③ 的失效（即重新判定范围）有三种情况：

- 同一会话**切换智能体**（`agent_id` 变化）——不同智能体的知识范围不同，旧范围不适用，必须清空重判；
- 用户在徽标上主动点「重新选择」；
- `knowledge_scope.confirmed_at` 距今超过 TTL（与 Redis scope 的 30 天一致，自然过期）。

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

**选中即锁定，但允许微调**：用户选完任一项后，范围立即生效并锁定（后续轮次不再自动变动），
但徽标上的范围可点开调整——增加或移除个别库，不必重走门禁。锁定约束的是「系统不得擅自改变
用户已确认的范围」，不限制用户自己修改。

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

**通道 A（主，前端；v1 未实施）**：发卡时前端从 `pending_scope_gate` 快照（随卡片事件下发或由接口读取）
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
后重新判定）。**该徽标未实施。**

已实施的是另一条只读提示：智能体级 `scope_gate=bound_only` 时，输入框上方常驻
`ScopeBoundBanner`「仅检索已绑定的知识库」+ 绑定库数量，说明此时选其他库不会生效。
它只解释状态、不提供操作，因此不涉及「锁定与微调」的取舍。

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

**双层开关**：全局管「能不能用」，智能体管「要不要用」。

| 层级 | 键 / 位置 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| 全局 | `knowledge_scope_gate_enabled`（`system_config`，SystemConfig 页面） | bool | `true` | 总开关。出事故时运维一键全站关闭，无需发版 |
| 全局 | `knowledge_scope_gate_threshold` | int | `5` | 触发门禁的库数阈值 |
| 全局 | `knowledge_scope_gate_ranked_limit` | int | `3` | 选项①中「最相关库」的数量 |
| 智能体 | `engine_config.scope_gate`（`AgentManagement.vue` 智能体配置，仅 `KNOWLEDGE_BASE` 类型可见） | 四态互斥 | `"inherit"` | `inherit` 跟随全局 / `on` 强制开启 / `off` 强制关闭 / `bound_only` 仅用绑定库（不弹卡） |

生效判定：

```
全局 enabled == false          → 整体不生效（无论智能体怎么配，含 on）
智能体 scope_gate == "off"     → 该智能体不生效
智能体 scope_gate == "bound_only" → 该智能体不弹卡，且范围只取绑定库
智能体 scope_gate == "on"      → 全局开着时生效（含义：不跟随后续灰度）
智能体 scope_gate == "inherit" → 跟随全局
```

``on`` 不再越过全局：一处「强制开启」若能在总闸关闭后继续弹卡，总闸就不成其为总闸，
出事故时也止不住血。全局开着时 ``on`` 与 ``inherit`` 行为一致，保留它是为将来接入灰度
（那时 ``on`` 表示「这个智能体不跟随灰度」）。

**为什么两层都要**：

- 只有全局：对外客服机器人问「你要在哪个知识库里查」很突兀，而内部知识助手则很自然。
  门禁是打断式交互，不同业务的容忍度差异大，需要智能体级表达「我不要这个」。
- 只有智能体级：出问题时无法一键止血，得逐个智能体去关，几百个智能体根本来不及。
  全局开关是运维的生命线。

**为什么不给阈值也做智能体级**：配置项会从 1 个变成 N 个组合，排查问题时要同时看两处，
收益远小于成本。个别智能体用 `on/off` 表达意愿已经够用。阈值只保留全局一处。

智能体级开关走 `engine_config`，与现有 `safety_check_enabled` 同一模式
（`app/services/ai/executors/openclaw_executor.py` 的读取方式一致），不需要新表新字段。

### 4.4 前端改动

| 位置 | 改动 |
|---|---|
| `frontend/src/views/SystemConfig.vue` | 新增 3 个全局配置项，按现有 `knowledge_ragflow_*` 的分组与说明文案格式填写 |
| `frontend/src/views/AgentManagement.vue` | 「检索范围确认」四态单选（跟随全局 / 强制开启 / 关闭 / 仅用绑定库），每项带一行用途说明，写 `agentForm.engine_config.scope_gate`，随「保存智能体」落库 |
| `frontend/src/views/EmbedChat.vue`、`AgentDebug.vue` | 渲染 `ask_user_question` 卡片（现有组件已支持，需确认 `__scope_pick__` 分支的点击行为） |
| `frontend/src/components/chat/ScopeBoundBanner.vue` | `scope_gate=bound_only` 时输入框上方常驻状态提示（只读，不提供操作）；聊天页与调试页共用，Auto Router 模式因无法预判路由目标而不提示 |
| 库选择器 | 「指定其他库」点击后打开，需带搜索框（100 库场景必需） |
| 输入框上方徽标 | 显示当前会话范围，可点击微调（预选当前库集合） |
| `frontend/src/utils/userQuestion.ts` | 提交回答时，把选中的 dataset_id 同时写入 `knowledge_dataset_ids` |

## 5. 埋点（未实施）

> 本条与 §4.4 的通道 A、§6 的徽标/微调在 v1 **均未实施**：v1 走通道 B（回执时用
> `pending_scope_gate` 快照还原 ID），功能可用，但缺少「前端直接回传 ID」与可微调的徽标
> （只读的 `ScopeBoundBanner` 已实现，但它不提供操作）。
> 保留原文作为后续迭代的目标描述，不代表当前代码已具备。

原计划：

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
2. 双层开关接入（全局配置项 + `engine_config.scope_gate` 读取与判定）+ 单测
3. pipeline 接入 `ScopeGateStep` + 集成测试（此时可用 `ask_user_question` 直接验证链路）
4. 会话范围读写（`ConversationResourceService` 扩展）+ 测试
5. 前端：SystemConfig 配置项、智能体编辑器开关、库选择器接入第 ④ 项、范围徽标与微调、回执写回
6. 埋点接入与看板
7. 灰度：先对内部账号开启，观察两周选择分布与命中率变化后再全量

## 9. 已确认的设计决策

以下三项在评审中已定，不再作为待议项：

| 决策 | 结论 | 理由 |
|---|---|---|
| 阈值维度 | 按「库数」，不按文档量加权 | 简单、可解释、用户能自己算；文档量加权会让同一用户在库内容变化时行为漂移 |
| 选项①的锁定与微调 | 默认锁定，但徽标可微调 | 锁定是约束系统不擅改用户选择；微调是用户主动修改，两者不冲突 |
| 切换智能体时的范围 | 清空重判 | 不同智能体知识范围不同，沿用旧范围会检索到该智能体根本不该搜的库 |

## 10. 实施记录与偏差（2026-10-27）

按本文实施时发现三处原文判断有误，已按实际情况调整，行为不变、约束不变：

| 项 | 原文 | 实际实现 | 原因 |
|---|---|---|---|
| 门禁挂载点 | 新增 `ScopeGateStep` 插在 `ContextStep` 之后 | 下沉到 `KnowledgeAgentRunner._execute_raw`，在 `resolve_knowledge_dataset_ids` 之前 | `AgentContext` 由 `setup_context` 创建，而它是在 `AssembleStep`（`assemble_step.py:161,173`）里调用的，`ContextStep` 阶段拿不到 `effective_dataset_ids`（即与门④要用的范围）；下沉到 runner 还能天然保证只作用于知识库链路，且不必改动 `create_default_pipeline` |
| 会话状态存储 | 会话表新字段 | `ConversationResourceService` 的 Redis scope 新增 `knowledge_scope` / `pending_scope_gate` | 平台没有会话表，会话级挂载本来就存在 Redis；同时给该服务补了 `patch_scope()`，避免整体覆盖写把其他挂载项清掉 |
| 选项③「仅智能体默认」置灰 | 卡片上置灰 | 智能体未绑库时该选项不出现在卡片里 | `ask_user_question` 的选项 schema 没有禁用态，置灰无法表达 |
| 选项④「指定其他库」 | 打开库列表弹层 | v1 引导用户改用「知识库附件」精确指定 | 附件选库是既有能力，加附件会让门禁自动跳过（与门②一致）；弹层选择器属于前端新组件，留作后续增强 |

实施后产出：

- `app/services/ai/knowledge_scope_gate.py`（新建）：开关解析、四道与门、选项构造、回执解析
- `app/services/ai/knowledge_catalog.py`：新增 `rank_knowledge_catalog`
- `app/services/conversation_resource_service.py`：scope 新增两字段 + `patch_scope`
- `app/services/ai/runners/knowledge_agent_runner.py`：`_maybe_open_scope_gate` / `_build_scope_gate_chunk` / `_restore_scope_from_reply`
- 前端：`SystemConfig.vue` 三个全局配置项、`AgentManagement.vue` 智能体级四态开关
- 测试：`tests/ai/test_knowledge_catalog_rank.py`、`tests/ai/test_knowledge_scope_gate.py`、`tests/ai/test_conversation_scope_fields.py`、`tests/ai/runners/test_knowledge_scope_gate_runner.py`、`tests/test_knowledge_scope_gate_migration_contract.py`

### 10.1 联调后修复（2026-10-27，用户实测反馈）

用户实测时卡片能正常弹出（10 个库触发），但点提交后报
「用户回答未通过校验：问题不存在或不属于当前会话」。这一条暴露了 5 个问题，均已修复：

| 问题 | 根因 | 修复 |
|---|---|---|
| 回执校验失败 | 我直接构造 `user_question` SSE 事件，**跳过了 `UserQuestionStore.create_pending` 登记**；平台原生卡片靠 `persist_user_question_event()` 登记，回执时 `submit_answer` 查不到就抛 `PermissionError` | `_build_scope_gate_chunk` 改为 async，发卡前调用 `persist_user_question_event()`；登记失败则返回 None、退回全量行为（不发一张点了必然失败的卡） |
| 回执轮用回执全文当检索词 | `current_user_query` 在回执轮是整段【用户回答】文本（含问题、选项、question_id），拿它检索会搜回噪音 | 新增 `_original_query_before_receipt()`，恢复范围时把检索词换回触发门禁的原始提问 |
| 选「我自己指定」会重复弹卡 | 该选项返回空 ID 列表 → 不走恢复分支 → 又落入门禁判定 → 同一张卡再弹一次 | 回执选择为 `__scope_pick__` 时跳过门禁判定，本轮按原逻辑（全量）执行，并清掉 pending 快照 |
| 确认的范围没有真正生效 | `knowledge_scope` 只用于「不再弹卡」，`resolve_knowledge_dataset_ids` 并不读它，下一轮又回到全量——功能等于没生效 | 新增 `_apply_confirmed_scope()`，在预检前把会话确认范围与可用范围求交后应用；本轮显式选库时以显式选择优先 |
| 切换智能体未清空 | §9 已定的「清空重判」尚未落地 | `knowledge_scope` 增加 `agent_id`；新增 `_drop_scope_if_agent_changed()`，归属不一致时清空并重新判定 |

另外补了两道守卫：回执文本（`【用户回答】` 前缀）一律不参与「追问上一轮结果」的短路判定，
否则范围恢复会被静默跳过；范围内已失效的库（被删/权限收回）自动剔除，全失效时退回本轮解析结果。

### 10.2 委派（subagent）场景：不弹卡，改自动收窄

主智能体通过 `sub_agent_call` 委派知识库智能体时，**交互式门禁不具备可行性**，
三条原因都是硬约束：

| 约束 | 位置 | 后果 |
|---|---|---|
| 卡片事件被丢弃 | `agent_delegate_tool._consume_sub_agent_stream`：`INTERRUPT_SSE_TYPES` 只含 `permission_required/external_execution_required/error`，转发白名单只含 `log/browser_session/citation` | `user_question` 既不中断也不转发，被静默吞掉 |
| 子代理会话独立 | `child_session_id = f"child_session_{run_id}"` | 待回答记录登记在子会话，用户在**主会话**提交回执 → 必然「问题不存在或不属于当前会话」 |
| 委派流提前结束 | 门禁命中时 runner `yield chunk; return` | 子流结束且 `_extract_delegation_text` 取不到正文 → 委派返回空 → 主智能体拿到「子代理无输出」 |

若不处理，一个本可正常回答的委派轮会退化成空结果——比不做门禁更差。因此：

1. **判定收敛到一处**：把 `delegated_sub_agent` 加入
   `tool_nudge_policy.is_automatic_delivery_context` 的无人值守集合（与
   `is_scheduled_task`、`is_subscription_task`、`quick_suggestions_forbidden` 并列）。
   这样原生 `ask_user_question` 的 nudge 也一并被禁用，不必在两条链上各判一次。
   `agent_delegate_tool._dispatch_sub_agent_executor` 负责把
   `execution_options={"delegated_sub_agent": True}` 传给子执行器。
2. **门禁短路**：`KnowledgeAgentRunner._maybe_open_scope_gate` 在
   `_has_no_interactive_user()` 为真时直接返回 `None`，不写待回答快照。
3. **自动收窄**：`_narrow_scope_without_asking` 在范围超阈值时，按
   `rank_knowledge_catalog` 取前 `ranked_limit` 个库检索，并写日志
   `Delegated scope auto-narrowed: N -> M`。一个都没匹配上时**保留原范围**
   （宁可多检索，也不要因猜错而漏掉答案），异常同样放行原范围。
4. **工具自身兜底**：`AskUserQuestionTool.ainvoke` 先用
   `is_automatic_delivery_context(None, execution_options_context.get())` 判定，
   命中则直接返回 `status=error` 并给出「不要再尝试提问」的 hint。
   只在 nudge 层禁用是不够的——`ask_user_question` 是**系统隐式工具**
   （`ToolRegistry.get_system_implicit_tools()` 有 6 处注入点），与 `config.tools`
   无关，模型仍可主动调用；在工具入口拒绝一处生效，且模型能拿到明确原因，
   不会像「工具凭空消失」那样去猜测或编造调用。
5. **上下文隔离**：子代理的 `setup_context` 会覆盖 `execution_options` 的
   ContextVar，因此委派工具额外保存并在 `finally` 中与 `agent_context` 一起恢复
   `original_execution_options`。否则一次委派之后，**主智能体**在同一请求的后续轮次
   也会被判定成无人值守，连它自己的提问都会被拒——这属于本改动引入的副作用，
   由 `tests/ai/tools/test_delegation_context_isolation.py` 锁住。

定时任务与订阅任务走的是同一条判定，因此也自动获得「不弹卡 + 自动收窄」的行为，
这符合它们同样没有在线用户的实际情况。

