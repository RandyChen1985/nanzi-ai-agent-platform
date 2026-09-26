# 设计：会话历史多维筛选 (chat-history-filtering)

## 1. 问题定义

### 1.1 直接诉求
侧边栏搜索框只能输入关键词，用户希望增加范围限定，典型表达是「全部 / 仅任务会话 / 其他分类」。

### 1.2 调研发现的关键事实

| 事实 | 证据 | 对设计的影响 |
|---|---|---|
| 任务会话可仅凭 `conversation_id` 前缀识别 | `task_center_service.py:274` 使用 `like("task_conv_%")`；`scheduler_service.py:301-308` 在基础 ID 后追加 `_run_` 与随机 hex 后缀生成派生运行会话 ID | 「来源」筛选无需新增字段 |
| 历史响应已自带智能体身份 | `app/schemas/agent.py:200-203` 返回 `agent_name` / `agent_display_name` / `agent_avatar_url` | 智能体下拉无需新增接口 |
| 后端已预留 4 个未启用的筛选参数 | `chat.py:2083-2095` 已有 `status`、`start_date`、`end_date`、`agent_id` | 多数维度只需前端接线 |
| 分组模式下 `keyword` 只匹配最新一轮 | `chat.py:2131-2162`：subquery 取 `max(id)` 代表行，`keyword` 在 join 之后才应用 | **必须修复**，否则新增维度也无法解决检索困难 |
| 侧边栏宽度仅 288px | `ChatHistorySidebar.vue:167` 的 `w-72` | 筛选项不能平铺，必须收纳 |

### 1.3 现存缺陷（本次一并修复）
`group_by_conversation=true` 时，`keyword`、`status`、`agent_id` 等过滤都应用在「每个会话 `max(id)` 的那一条」上。对 `agent_id` 这类会话级属性无影响，但对 `keyword` / `status` 这类**轮次级**属性，会导致：

- 关键词只搜得到每个会话的最新一轮，早期轮次永远搜不到；
- 「失败」筛选只能找到「最新一轮失败」的会话，找不到「历史上失败过但最近成功」的会话；
- 而 `turn_count` 统计的是整个会话的轮数，与过滤口径不一致，用户看到「3 轮交互」却只搜到 1 轮的内容。

## 2. 架构与影响面

```
EmbedChat.vue / AgentDebug.vue   ← 持有筛选状态，映射为查询参数
        │  props: filters / availableAgents / showAgentFilter
        │  emits: update:filters
        ▼
ChatHistorySidebar.vue           ← 纯展示与交互，不持有查询语义
        │  GET /api/v1/chat/history?scope&agent_id&status&start_date&end_date&keyword
        ▼
app/api/v1/endpoints/chat.py     ← 参数解析、用户范围、身份回填与编排
        │  build_history_query(...)
        ▼
app/services/ai/history_query.py ← 查询构建：会话级代表行过滤 + 轮次级会话键子查询
```

| 层 | 文件 | 改动性质 |
|---|---|---|
| 后端 | `app/api/v1/endpoints/chat.py` | 新增 `scope` 参数 + 改为调用查询构建模块 |
| 后端 | `app/services/ai/history_query.py` | **新建**：筛选条件到 SQLAlchemy 查询的构建 |
| 前端 UI | `frontend/src/components/ChatHistorySidebar.vue` | 新增筛选交互 |
| 前端接线 | `frontend/src/views/EmbedChat.vue` | 状态与参数映射 |
| 前端接线 | `frontend/src/views/AgentDebug.vue` | 同步接入 |
| 数据库 | — | **无变更** |

**筛选执行位置：必须服务端筛选。** 历史列表是服务端分页（`page_size=20` + `hasMore` 由 `newItems.length >= 20` 推导）。若在前端本地 `filter`，会出现两个错误体验：本页 20 条筛完只剩 3 条却仍显示「20 条已加载」，以及仍有数据时却显示「已加载全部历史」。因此四个维度全部作为查询参数下发。

## 3. 后端契约

### 3.1 新增参数
`GET /api/v1/chat/history` 新增：

- `scope`：`Optional[str]`，取值 `all` / `task` / `chat`，默认 `all`（不传等价于 `all`，保持向后兼容）。

过滤映射：

- `task` → `AgentExecutionHistory.conversation_id.like("task_conv_%")`
- `chat` → `or_(AgentExecutionHistory.conversation_id.is_(None), ~AgentExecutionHistory.conversation_id.like("task_conv_%"))`
  - 必须显式处理 `NULL`：`conversation_id` 列 nullable（`app/models/audit.py:41`），SQL 中 `NULL NOT LIKE 'x'` 结果为 `NULL`（非真），若不处理会导致 `conversation_id` 为空的历史记录在「普通对话」下消失。
- 传入非法值时应忽略该过滤（等价 `all`），不抛 400，避免前端灰度期间出现硬失败。

### 3.2 维度语义分类

这是本次设计的核心约定。五个维度按「属性归属层级」分成两类：

| 维度 | 语义层级 | 含义 | 实现位置 |
|---|---|---|---|
| `scope` | 会话级 | 会话来源 | 代表行 WHERE |
| `agent_id` | 会话级 | 会话所属智能体 | 代表行 WHERE（现状保留） |
| 时间范围 | 会话级 | 会话的**最近活跃时间** | 代表行 `created_at`（现状保留） |
| `status` | **轮次级** | 会话内**存在**该状态轮次 | 会话键集合子查询（新） |
| `keyword` | **轮次级** | 会话内**任意轮次**命中 | 会话键集合子查询（新） |

选择理由：

- **时间用「最近活跃」而非「任意轮次落在区间内」**。历史列表呈现的是会话，用户说「近 7 天」的心智是「我最近 7 天聊过的会话」。若用「任意轮次落在区间」，一个从三个月前持续到今天的会话会同时出现在「近 7 天」和「更早」的检索预期里，语义混乱。
- **状态与关键词用「存在性」**。用户找「失败的会话」是想找「有过失败的那次」，而不是「最后一次恰好失败」；搜索同理。这同时也是 1.3 节缺陷的修复方式。

### 3.3 轮次级子查询的适用边界

会话级属性（`scope` / `agent_id` / 时间）可直接过滤分组代表行；轮次级属性（`keyword` / `status`）必须借助子查询判定「会话内是否存在满足条件的轮次」。**该子查询仅在 `group_by_conversation=true` 时启用。** `group_by_conversation=false` 时每一行本身就是独立轮次，`keyword` / `status` 直接作为该行的 WHERE 条件即可；此时若套用会话级判定会造成语义错位（变成「该轮所属会话中存在某轮命中」，与「该轮命中」不是一回事）。

实现采用**「会话键集合 + IN」**而非关联 `EXISTS`：

- 内层子查询按筛选条件选出满足条件的会话键并 `distinct()`，外层以 `conversation_key IN (子查询)` 过滤分组代表行；
- 键表达式为 `coalesce(conversation_id, trace_id)`，而 `trace_id` 为 NOT NULL（`app/models/audit.py:40`），因此该键**永不为 NULL**，`IN` 不存在 SQL 三值逻辑陷阱；
- 由于同一会话的所有行共享同一会话键，代表行的键命中即等价于「该会话存在匹配轮次」，无需关联外层别名，实现更稳健且编译结果可被测试断言覆盖。

因此实现上需要分支：

- `group_by_conversation=true`：`keyword` / `status` 走会话键集合子查询，`scope` / `agent_id` / 时间走代表行 WHERE；
- `group_by_conversation=false`：全部走该行直接 WHERE（保持现有行为）。

### 3.4 会话键集合子查询的要点

- 会话键表达式必须与外层分组使用的键完全一致，即 `coalesce(conversation_id, trace_id)`（与 `chat.py:2139` 的 `group_by` 表达式相同），否则 `conversation_id` 为空的历史会被漏掉。
- 子查询内部的用户约束**必须基于子查询自身的表别名重建**，不能直接复用外层基于 `AgentExecutionHistory` 构造的过滤表达式：子查询与外层引用同一张表，复用同一实体表达式会产生同表引用歧义。实现上让子查询接收 `user_id` / `username`，并在内部用 `aliased(AgentExecutionHistory)` 重新构造条件。
- 多个轮次级条件（`keyword` 与 `status` 同时存在）必须合并进**同一个**子查询，语义为「同一会话内存在同时满足两者的轮次」。若拆成两个独立子查询，会退化为「存在命中关键词的轮次」且「存在失败轮次」，两者可能来自不同轮次——这是一处必须固化的语义细节。

### 3.5 越权风险分析

`scope=task` 使用宽泛的 `task_conv_%` 前缀扫描，初看与 `task_center_service.py:214-216` 中「非管理员不使用宽泛前缀扫描，避免越权看到他人已删任务残留」的约束冲突。实际不冲突：

- `chat.py:2124-2128` 已在分组之前构建 `scope_filters`，非管理员会被强制加上 `AgentExecutionHistory.user_id == history_user_id`；
- 该 `scope_filters` 同时作用于 subquery（`chat.py:2138`）和主查询（`chat.py:2150-2151`），且按 3.4 节要求必须以同一 `user_id` 约束重建于新增的轮次级子查询内部。

因此用户只可能在自己可见的会话范围内筛出任务会话，不存在跨用户泄露。**实现时必须保证轮次级子查询内部重建了同样的 `user_id` 约束，否则这条结论不成立。**

### 3.6 兼容性

- 新增参数可选、默认不改变行为，不破坏现有调用方（`EmbedChat`、`AgentDebug`）。
- 唯一行为变更：分组模式下 `keyword` / `status` 由「代表行」变为「会话内任意轮次」，属于有意的缺陷修复，需在 `tests/CHECKLIST.md` 标注。

## 4. 前端组件契约

### 4.1 `ChatHistorySidebar.vue`

新增 props：

```ts
interface ChatHistoryFilters {
  scope: "all" | "task" | "chat";
  agentId: string;
  status: "" | "success" | "failed";
  timeRange: "" | "today" | "7d" | "30d" | "custom";
  startDate?: string;
  endDate?: string;
}

interface AgentOption {
  id: string;
  display_name: string;
  avatar_url?: string;
}
```

- `filters?: ChatHistoryFilters`：当前筛选值（受控）。
- `availableAgents?: AgentOption[]`：智能体下拉候选。
- `showAgentFilter?: boolean`：是否展示智能体筛选，默认 `false`。

新增 emits：

- `(e: "update:filters", value: ChatHistoryFilters)`
- `(e: "reset-filters")`

**智能体筛选的显示条件**：仅当 `showAgentFilter` 为 `true` 时展示。原因是 `EmbedChat` 在集成锁定场景下 `config.agentId` 有值，`fetchHistory` 已经把 `params.agent_id` 固定为当前智能体（`EmbedChat.vue:5438`），此时历史只可能来自一个智能体，下拉只有单一选项，属于纯噪音。父组件负责判定并传入该布尔值。

### 4.2 UI 结构

三段式，**默认状态下与当前界面完全一致（零额外占位）**：

1. **搜索行**：`[输入框(内嵌清空按钮)] [漏斗按钮]`
   - 漏斗按钮放在输入框**外部右侧**，而不是内部。现有清空按钮已定位在 `right-2.5`（`ChatHistorySidebar.vue:241-251`），若把漏斗也塞进输入框内部，两者会在输入 `keyword` 时相邻挤压。
2. **激活 chip 行**：仅当存在非默认筛选值时渲染，横向可滚动，每个 chip 展示维度摘要并支持点击移除。
3. **筛选面板**：绝对定位于搜索行下方（`absolute` + `z-30`），点击外部或按 Esc 关闭。四组控件：
   - 来源：分段控件（全部 / 任务会话 / 普通对话）
   - 智能体：带头像下拉（条件展示）
   - 状态：分段控件（全部 / 成功 / 失败）
   - 时间：快捷 chips（全部 / 今天 / 近 7 天 / 近 30 天）+ 自定义时展开两个日期输入
   - 面板底部：重置

**移动端**：`isMobile` 时侧边栏本身是全屏抽屉（`ChatHistorySidebar.vue:165-168`），筛选面板改为**内联展开**而非浮层，避免浮层在小屏上遮挡且与抽屉的滚动容器冲突。

### 4.3 视觉与交互一致性

- 漏斗激活态使用主题色 `text-primary` + 数量角标，沿用现有激活会话高亮（`bg-primary/[0.06]` + `ring-1 ring-primary/20`）的色彩语言。
- 沿用现有 `text-xs` / `rounded-xl` / `dark:` 暗色适配，不引入新的视觉规格。
- 不使用浏览器原生 `alert()` / `confirm()`（`openspec/project.md:90` 约束），重置等确认动作如需二次确认须使用平台 `ConfirmModal`。

## 5. 数据流与状态管理

筛选状态**由父组件持有**，子组件保持受控展示组件定位（与现有 `modelValue` keyword 的处理方式一致）。理由：查询参数拼装、分页重置、请求发起都发生在父组件，状态与请求生命周期同源可避免双子源不同步。

- 筛选项变化 → **立即**请求（不走防抖）；只有 `keyword` 走现有 300ms 防抖。
- 任一筛选变化 → 重置 `historyPage = 1`、`historyHasMore = true`、清空 `historyList`，然后重新拉取。
- 时间范围换算为 `start_date` / `end_date`：
  - `today`：本地当天 `00:00:00` 至 now；
  - `7d` / `30d`：now 减 7 / 30 天至 now；
  - `custom`：用户所选区间起止。
  - 采用本地时间构造后按现有 `datetime.fromisoformat` 约定传递（`chat.py:2109-2115` 已兼容 `Z` 与 `+00:00` 偏移）。

## 6. 边界与错误处理

| 场景 | 处理 |
|---|---|
| 筛选变化未重置分页 | 必须重置 `historyPage=1`，否则 `offset` 错位导致跳页漏数据 |
| 筛选后分组折叠态残留 | 筛选变化时清空 `collapsedGroups`。否则「更早」默认折叠（`ChatHistorySidebar.vue:126-128`）会让筛选结果被折叠隐藏，用户误判为「无结果」 |
| 筛选结果为空 | 复用现有空状态容器，但文案区分「无匹配结果」并给出「重置筛选」入口，与「暂无会话历史」区分 |
| 筛选条件与关键词同时为空 | 等价于当前默认行为，请求不带任何筛选参数 |
| `hasMore` 判定 | 保持 `newItems.length >= 20` 逻辑不变；筛选后该值基于新结果集重新推导 |
| 自定义区间起止颠倒 | 提交前校正为 `start <= end`，避免空结果 |

## 7. 备选方案与决策记录

| 决策点 | 选项 | 结论与理由 |
|---|---|---|
| 筛选执行位置 | 服务端 / 客户端 | **服务端**。客户端会破坏分页与 `hasMore` 语义（详见 2 节） |
| UI 形态 | A 漏斗面板 / B 来源常驻 chips / C 查询语法前缀 | **A**。侧边栏仅 288px，A 默认零占位且能容纳四个维度；B 永久占用约 34px 高度（搜索框下方紧贴「今天」分组，会拥挤）；C 可发现性差且在移动端全屏抽屉下不可用 |
| 时间维度语义 | 最近活跃 / 任意轮次落在区间 | **最近活跃**。符合历史列表「我最近聊过什么」的心智 |
| 状态维度语义 | 存在失败轮次 / 最新一轮失败 | **存在失败轮次**。用户找的是「失败过的那次」，同时修复 1.3 节缺陷 |
| `keyword` 缺陷是否顺带修 | 修 / 不修 | **修**。该缺陷与本需求同源（都属分组模式检索语义），不修则新维度无法达成用户目标 |
| 智能体筛选可见性 | 始终显示 / 集成锁定时隐藏 | **锁定时隐藏**。锁定场景下历史已被限定为单一智能体，显示下拉无意义 |

## 8. 测试策略

- **后端语义回归**（重点）：
  - `scope` 三种取值：`task` 只返回 `task_conv_%`；`chat` 返回非任务会话且**包含 `conversation_id` 为 NULL 的记录**；`all` 不做过滤。
  - 「关键词只出现在早期轮次」时仍能被检索到（1.3 节缺陷的回归测试）。
  - 「会话内存在失败轮次但最新一轮成功」时，`status=failed` 仍能命中。
  - `keyword` 与 `status` 同时存在时，命中要求是「同一会话内存在同时满足两者的轮次」。
  - `group_by_conversation=false` 时语义不发生错位。
  - 非管理员用户即便使用 `scope=task`，也不会返回他人会话（越权回归）。
- **前端契约测试**：按项目惯例放在 `tests/frontend/`，使用 `pytest --confcutdir=tests/frontend`，断言组件 props/emits 契约、父组件的参数映射（含时间范围换算）、以及筛选变化重置分页与折叠态。
- **类型检查**：`vue-tsc --noEmit`。
- **文档**：更新 `tests/CHECKLIST.md`，标注 `keyword` / `status` 的行为变更。

## 9. 未纳入范围（YAGNI）

- 「仅看有数据产出（`has_data_output`）的会话」：用户本轮未选择，不实现。
- 多智能体组合筛选、标签体系、保存筛选预设：无当前需求。
- 数据库索引调整：现有 `conversation_id`、`agent_id`、`created_at` 均已建索引，暂不新增。
- 调整 `group_by_conversation` 的分组口径或 `aggregatedHistoryList` 的聚合逻辑：本次不动。
