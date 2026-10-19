# 工作台「最近会话」覆盖全部智能体 — 设计

日期：2026-10-19
状态：已实施

## 背景

「我的工作台」（`/dashboard/workbench`）的「最近会话」区块，实际只显示 ChatBI 会话。用户截图里三条卡片副标题全是「ChatBI · 数据智能助手」，质疑为什么只显示某一种智能体对象。

## 根因

工作台没有自己的会话查询，直接复用数据门户首页：

```
WorkbenchHomeService.build
  → _load_portal_activity
    → DataPortalHomeService.build
      → 只查 capabilities 含 data_query 且 is_enabled 的 AIAgent
```

`DataPortalHomeService` 的「最近分析」产品语义是数据分析，因此按能力过滤是对的；错在把它当成了工作台的会话来源。副标题也是硬编码前缀 `ChatBI · {display_name}`，视觉上进一步强化了「只有数据类会话」。

## 决策

| 维度 | 决定 | 理由 |
| --- | --- | --- |
| 覆盖范围 | 全部智能体的普通对话 | 工作台是个人工作入口，不是数据门户子页 |
| 任务会话 | 排除 `task_conv_` 前缀 | 语义是「继续对话」，任务会话已在「最近任务」列；与智能助手历史列表 `scope=chat` 一致 |
| 空会话 ID | 排除 `conversation_id IS NULL` | 「继续对话」依赖会话 ID 跳转，无 ID 无法恢复上下文 |
| 卡片副标题 | 智能体展示名 | 状态已由 `WorkbenchItemMeta` 徽标承载，再拼一次会重复 |
| 门户首页 | 保持只显示 ChatBI 会话 | 「最近分析」仍应锁死在数据分析语义 |
| 停用智能体 | 仍显示其历史会话名字 | 用 `AIAgent` 直查而非 `list_allowed_agents`，避免变成「未知智能体」 |

## 实现

新增 `app/services/workbench_conversation_service.py`：

- `list_recent_conversations(db, user_id, limit=30)`：按 `coalesce(conversation_id, trace_id)` 分组取每个会话最新一轮（`max(id)` 代表行 + join），`user_id` 隔离，`scope_condition("chat")` 排除任务会话，`created_at` 倒序取 30 条候选。
- `conversation_items_from_rows(rows, identity_map)`：组装 `WorkbenchItem`，标题压平空白并截断 60 字，副标题取 `display_name`（`build_agent_identity_map` 的顺序是 name/display_name/avatar，必须显式选下标 1），`target` 带 `conversation_id` 与 `agent_id`。
- `_load_agent_identity`：按出现过的 `agent_id` 直查 `AIAgent`（含停用），回填展示名。

改造入口：

- `DataPortalHomeService.build(..., include_conversations: bool = True)`：`False` 时跳过 ChatBI 会话查询与智能体扫描，门户首页保持默认行为。会话查询抽成模块级 `_load_chatbi_conversations`，行为不变。
- `workbench_home_service._load_portal_activity`：传 `include_conversations=False`，返回值只留 `reports`；新增 `_load_conversations` 走新服务，`source_status["conversations"]` 由新 loader 决定。
- 前端 `WorkbenchResume.vue`、`PersonalWorkbench.vue` 零改动（副标题来自后端）。

## 数据流

```
AgentExecutionHistory（全智能体共用表）
  → [工作台] 全部智能体 + scope=chat + 有会话 ID + 按会话取最新轮 → resume_items（4 条）
  → [门户首页] 仅 data_query 能力智能体 → recent_analysis.conversation
```

## 错误处理

会话装载包在既有 `_safe_load` 里：查询异常 → 该来源 `source_status=conversations:error`，页面显示「部分数据暂时无法获取」，其余区块不受影响；`resume_items` 退化为空并展示空态。

## 测试

- `tests/services/test_workbench_conversation_service.py`（新增 6 项）：副标题取 display_name、去重与跳过空会话 ID、标题压平截断、未知智能体兜底、SQL 语义（GROUP BY 会话键 / 用户隔离 / `NOT LIKE task_conv_` / 不按 agent 能力过滤）、无数据时不再查智能体。
- `tests/services/test_workbench_home_service.py`（新增 2 项）：门户活动剔除会话且传 `include_conversations=False`；`resume_items` 来自工作台会话装载器、`source_status.conversations=ok`。
- `tests/services/test_data_portal_home_service.py`（新增 1 项）：`include_conversations=False` 不触发 ChatBI 会话查询，默认仍查询。

## 已知取舍

- 纯时间序取 4 条，同一智能体可能占满整列；未按智能体做配额（YAGNI，需要时再加权重）。
- 分组查询会聚合该用户全部历史行；`(user_id, created_at)` 索引已在 `db-prod/V14` 与 PG baseline 中存在，单用户数据量下有界。
- 未做真机/真实库验证：只做了 sqlite 端到端语义验证 + 单测 + 相关套件回归。
