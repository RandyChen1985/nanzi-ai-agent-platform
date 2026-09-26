## 为什么需要此变更 (Why)

会话历史侧边栏（`ChatHistorySidebar.vue`）目前的检索能力只有一个关键词输入框，用户无法限定检索范围。当历史会话积累到一定规模后，用户面临两个具体困难：

1. **无法区分会话来源**。南孜平台的会话历史同时混放了「用户主动发起的交互对话」与「TaskCenter 定时任务自动产生的执行会话」。这两类会话在 `conversation_id` 上有明确区别（任务会话统一使用 `task_conv_` 前缀），但界面上完全无法区分，导致定时任务每天产生的会话噪音淹没了用户自己的对话记录。用户明确提出的诉求是「能否只看任务会话，或者只看普通对话」。
2. **无法按维度收窄**。即使加上关键词，用户也无法按智能体、执行状态、时间范围收窄结果，只能靠滚动和肉眼筛选。

同时，本次调研发现了一个**现存的搜索缺陷**：在 `group_by_conversation=true`（侧边栏的默认且唯一模式）下，`keyword` 与 `status` 过滤被应用在分组的代表行上，而代表行是每个会话 `max(id)` 的最新一轮记录。这意味着**关键词只能命中每个会话的最新一轮，命中不了更早的轮次**——用户主观感受就是「明明记得聊过这个话题，却搜不出来」。这个问题不修复的话，新增再多筛选维度也无法真正解决检索困难。

因此本变更既要新增多维度筛选能力，也要修正分组模式下的检索语义。

## 变更内容 (What Changes)

- **新增「会话来源」筛选**：支持「全部 / 任务会话 / 普通对话」三态切换。任务会话通过 `conversation_id LIKE 'task_conv_%'` 识别，与 TaskCenter 现有判定口径保持一致。
- **新增「智能体」筛选**：按智能体收窄历史，下拉项复用历史响应中已包含的 `agent_display_name` 与 `agent_avatar_url`，不新增接口。
- **新增「执行状态」筛选**：支持「全部 / 成功 / 失败」。
- **新增「时间范围」筛选**：支持「全部 / 今天 / 近 7 天 / 近 30 天 / 自定义区间」。
- **前端 UI 采用「漏斗按钮 + 弹出筛选面板」**：默认状态与当前界面完全一致（零额外占位），有筛选激活时通过按钮高亮、数量角标与可移除 chip 回显。
- **后端 `/api/v1/chat/history` 新增可选参数 `scope`**（`all` / `task` / `chat`），默认 `all`。
- **修复分组模式下的检索语义**：`keyword` 与 `status` 由「匹配代表行」改为「匹配会话内任意轮次」，使「会话内存在命中」成为真实语义。
- **无数据库变更**：所有筛选均通过现有索引列（`conversation_id`、`agent_id`、`status`、`created_at`）完成查询层过滤，不新增字段、不新增迁移 SQL。

## 能力范围 (Capabilities)

### 新增能力 (New Capabilities)
- `chat-history-filtering`: 覆盖南孜平台会话历史的多维度检索能力，包括前端侧边栏筛选交互（漏斗入口、筛选面板、激活态回显）、筛选状态到查询参数的映射，以及后端 `/api/v1/chat/history` 的来源过滤与分组模式检索语义。

### 修改能力 (Modified Capabilities)
- `agent-debug`: 其 `History Management` 需求中的 `Filtering` 条款由「支持按关键词搜索历史记录」扩展为「支持按关键词与多维度筛选检索历史记录」，因为 `AgentDebug.vue` 复用同一侧边栏组件，将同步获得筛选能力。

## 涉及范围与影响 (Impact)

- **后端服务与接口**：
  - `app/api/v1/endpoints/chat.py`：`get_history` 新增 `scope` 查询参数，并改为调用新的查询构建模块。
  - `app/services/ai/history_query.py`（新增）：把筛选条件构建为 SQLAlchemy 查询，统一 `scope` / 关键词 / 状态 / 时间的过滤语义。
- **前端交互与组件**：
  - `frontend/src/components/ChatHistorySidebar.vue`：新增筛选面板、漏斗入口按钮、激活 chip 回显行；扩展 props 与 emits。
  - `frontend/src/views/EmbedChat.vue`：新增筛选状态、筛选变更后重置分页并重新拉取、传入智能体候选列表与集成锁定判定。
  - `frontend/src/views/AgentDebug.vue`：同步接入筛选状态与候选列表，保持两处复用体验一致。
- **测试与文档**：
  - 新增后端语义回归测试，覆盖 `scope` 三种取值与「关键词只命中早期轮次」的修复。
  - 新增前端契约测试（`pytest --confcutdir=tests/frontend`）。
  - 更新 `tests/CHECKLIST.md`。
- **兼容性影响**：
  - 新增参数可选且有默认值，不破坏任何现有调用方。
  - 唯一行为变更：分组模式下的 `keyword` / `status` 语义由「最新一轮」变为「会话内任意轮次」，属于有意的缺陷修复，需在 `tests/CHECKLIST.md` 中标注。
- **无数据库结构变更**。
