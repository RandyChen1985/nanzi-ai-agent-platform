# 任务：会话历史多维筛选 (chat-history-filtering)

> 本文是用于追踪与归档的粗粒度任务清单。逐步的 TDD 实施步骤（含完整代码与验证命令）见同目录 [`implementation-plan.md`](./implementation-plan.md)。

## 1. 后端：新建查询构建模块 `app/services/ai/history_query.py`

- [ ] 1.1 新建模块，导出 `TASK_CONVERSATION_PREFIX`、`normalize_scope`、`conversation_key`、`scope_condition`、`turn_level_conversation_keys`、`build_history_query`。
- [ ] 1.2 `normalize_scope` 把空值与非法取值归一化为 `all`，不抛异常。
- [ ] 1.3 `scope_condition` 实现来源过滤：`task` 使用 `conversation_id.like("task_conv_%")`；`chat` 使用 `or_(conversation_id.is_(None), not_(conversation_id.like("task_conv_%")))` 显式覆盖 NULL；`all` 返回 `None`。
- [ ] 1.4 `turn_level_conversation_keys` 使用 `aliased(AgentExecutionHistory)` 重建用户约束，并把 `keyword` 与 `status` 合并进**同一个**子查询。
- [ ] 1.5 `build_history_query` 返回 `(rows_query, count_query)`：会话级条件作用于分组代表行；轮次级条件仅在 `group_by_conversation=true` 时以会话键 `IN` 子查询生效。
- [ ] 1.6 确认 `count_query` 在分页（offset / limit）之前构建，总数统计不受分页影响。

## 2. 后端：接入 `app/api/v1/endpoints/chat.py`

- [ ] 2.1 在 `get_history` 签名中新增 `scope: Optional[str] = None` 查询参数。
- [ ] 2.2 将函数内联的查询拼装替换为调用 `build_history_query(...)`，保持用户范围解析、智能体身份回填、Redis 元数据合并与分页后处理逻辑不变。
- [ ] 2.3 确认 `scope` / `agent_id` / `status` / 时间范围的对外行为符合 design 3.2 节定义的语义分层。

## 3. 前端组件：`frontend/src/components/ChatHistorySidebar.vue`

- [ ] 3.1 新增 `ChatHistoryFilters` 与 `AgentOption` 类型定义。
- [ ] 3.2 扩展 props：`filters`、`availableAgents`、`showAgentFilter`（默认 `false`），全部提供默认值以保证未接入的调用方不报错。
- [ ] 3.3 扩展 emits：`update:filters`、`reset-filters`。
- [ ] 3.4 搜索行改造：在输入框外部右侧新增漏斗按钮，避免与内嵌清空按钮（`right-2.5`）挤压。
- [ ] 3.5 漏斗按钮激活态：有筛选时使用 `text-primary` 并显示激活数量角标。
- [ ] 3.6 新增激活 chip 回显行：仅在存在非默认筛选时渲染，横向可滚动，chip 支持点击移除。
- [ ] 3.7 新增筛选面板：绝对定位于搜索行下方（`absolute` + `z-30`），支持点击外部与 Esc 关闭。
- [ ] 3.8 面板内实现四组控件：来源分段、智能体下拉（受 `showAgentFilter` 控制）、状态分段、时间快捷选项与自定义日期区间。
- [ ] 3.9 面板底部实现「重置」动作，触发 `reset-filters`。
- [ ] 3.10 移动端（`isMobile`）下筛选面板改为内联展开，避免与全屏抽屉的滚动容器冲突。
- [ ] 3.11 沿用现有 `text-xs` / `rounded-xl` / `dark:` 视觉规格，不引入新设计规格。

## 4. 前端接线：`frontend/src/views/EmbedChat.vue`

- [ ] 4.1 新增筛选响应式状态（`scope` / `agentId` / `status` / `timeRange` / `startDate` / `endDate`）与其默认值。
- [ ] 4.2 在 `fetchHistory` 的 `params` 中透传 `scope`、`status`、`start_date`、`end_date`；`agent_id` 沿用现有集成锁定逻辑。
- [ ] 4.3 时间范围换算为 `start_date` / `end_date`（今天按本地 00:00；7d/30d 按 now 减 N 天；自定义取所选区间并校正起止颠倒）。
- [ ] 4.4 筛选变化时立即请求，并重置 `historyPage=1`、`historyHasMore=true`、清空 `historyList`。
- [ ] 4.5 筛选变化时清空 `collapsedGroups`，避免默认折叠的「更早」组隐藏筛选结果。
- [ ] 4.6 计算 `showAgentFilter`：集成锁定（`config.agentId` 有值）时传 `false`，否则传 `true`。
- [ ] 4.7 将 `allowedAgents` 映射为 `AgentOption[]` 传入 `availableAgents`，复用已有 `/api/portal/agents/allowed` 数据，不新增接口。
- [ ] 4.8 绑定 `@update:filters` 与 `@reset-filters`。
- [ ] 4.9 空结果态文案区分「无匹配筛选结果」并提供「重置筛选」入口。

## 5. 前端接线：`frontend/src/views/AgentDebug.vue`

- [ ] 5.1 同步接入筛选状态与 `@update:filters` / `@reset-filters` 事件。
- [ ] 5.2 同步透传 `scope` / `status` / `start_date` / `end_date` 参数，并在筛选变化时重置分页。
- [ ] 5.3 传入 `availableAgents` 与 `showAgentFilter`，保持与 `EmbedChat` 体验一致。

## 6. 测试与文档

- [ ] 6.1 后端语义测试：`scope` 三种取值（含 `chat` 覆盖 `conversation_id IS NULL`）。
- [ ] 6.2 后端回归测试：关键词仅出现在早期轮次时仍可命中。
- [ ] 6.3 后端回归测试：「会话内存在失败轮次但最新一轮成功」时 `status=failed` 可命中。
- [ ] 6.4 后端测试：`keyword` 与 `status` 组合语义（须由同一轮次同时满足）。
- [ ] 6.5 后端测试：`group_by_conversation=false` 时不套用会话级判定。
- [ ] 6.6 越权回归测试：非管理员使用 `scope=task` 时不返回他人会话。
- [ ] 6.7 前端契约测试：覆盖组件 props/emits 契约、参数映射与时间范围换算、筛选变化重置分页与折叠态。
- [ ] 6.8 运行后端相关测试与 `pytest --confcutdir=tests/frontend`。
- [ ] 6.9 运行 `vue-tsc --noEmit` 类型检查。
- [ ] 6.10 更新 `tests/CHECKLIST.md`，标注 `keyword` / `status` 在分组模式下的行为变更。

## 7. 验证清单

- [ ] 7.1 默认状态下侧边栏视觉与改动前完全一致（零额外占位）。
- [ ] 7.2 仅选「任务会话」时列表只剩 `task_conv_` 前缀会话；切「普通对话」时反之。
- [ ] 7.3 关键词能命中会话的早期轮次（旧行为下命中不了）。
- [ ] 7.4 四项筛选叠加后分页、`hasMore`、空结果态均正常。
- [ ] 7.5 移动端全屏抽屉下筛选面板可用且不遮挡。
- [ ] 7.6 集成锁定场景下不出现智能体下拉。
- [ ] 7.7 确认无数据库迁移脚本产生。
