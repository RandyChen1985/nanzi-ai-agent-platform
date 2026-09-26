# chat-history-filtering Specification

## Purpose
TBD - created by archiving change 2026-09-26-chat-history-filtering. Update Purpose after archive.
## Requirements
### Requirement: 会话历史来源筛选 (Scope Filter)
后端 `/api/v1/chat/history` 必须（MUST）支持可选的 `scope` 查询参数，用于按会话来源收窄结果，取值为 `all`、`task`、`chat`，默认行为等价于 `all`。

- `task` 必须（MUST）仅返回任务会话，判定口径为 `conversation_id` 以 `task_conv_` 开头（与 TaskCenter 定时任务口径一致，并覆盖在基础 ID 后追加 `_run_` 后缀的派生运行会话）。
- `chat` 必须（MUST）返回所有非任务会话，且必须（MUST）包含 `conversation_id` 为空的记录。
- 传入非法取值时，系统必须（MUST）按 `all` 处理并正常返回结果，不得（MUST NOT）返回 4xx 错误。

#### Scenario: 仅查看任务会话
- **WHEN** 客户端请求 `/api/v1/chat/history?scope=task`
- **THEN** 返回结果中的每一条记录，其 `conversation_id` 都必须以 `task_conv_` 开头

#### Scenario: 仅查看普通对话并保留空会话标识记录
- **WHEN** 客户端请求 `/api/v1/chat/history?scope=chat`，且数据集中存在 `conversation_id` 为 `NULL` 的历史记录
- **THEN** 返回结果必须排除所有 `task_conv_` 前缀的会话，同时必须包含 `conversation_id` 为 `NULL` 的记录

#### Scenario: 非法取值降级为全部
- **WHEN** 客户端请求 `/api/v1/chat/history?scope=unknown_value`
- **THEN** 系统必须返回与不传 `scope` 时相同的结果，且状态码为 200

### Requirement: 会话历史多维度筛选参数
后端 `/api/v1/chat/history` 必须（MUST）支持按智能体、执行状态与时间范围筛选历史会话，且所有筛选条件必须（MUST）在服务端生效以配合分页。

- 智能体筛选复用 `agent_id` 参数。
- 执行状态筛选复用 `status` 参数。
- 时间范围复用 `start_date` 与 `end_date` 参数，语义为「会话的最近活跃时间落在区间内」。
- 新增的 `scope` 参数必须（MUST）为可选且有默认值，不得（MUST NOT）破坏未传参调用方的既有行为。

#### Scenario: 组合筛选
- **WHEN** 客户端同时传入 `scope=chat`、`status=failed`、`start_date` 与 `end_date`
- **THEN** 返回结果必须同时满足来源、状态与时间三个条件，并对过滤后的结果集执行分页与总数统计

### Requirement: 分组模式下的会话级检索语义
当 `group_by_conversation=true` 时，关键词与执行状态必须（MUST）以「会话内存在满足条件的轮次」为语义进行过滤，而不是仅匹配该会话的最新一轮。

- 关键词必须（MUST）在会话的全部轮次中匹配，任一历史轮次命中即视为该会话命中。
- 执行状态必须（MUST）以「会话内存在该状态的轮次」为语义。
- 当关键词与状态同时存在时，必须（MUST）要求同一会话内存在**同时满足两者**的轮次。
- 该存在性判断必须（MUST）在调用方可见的数据范围内进行，不得（MUST NOT）跨越用户权限边界。
- 当 `group_by_conversation=false` 时，关键词与状态必须（MUST）保持对当前轮次直接过滤的语义，不得（MUST NOT）套用会话级存在性判断。

#### Scenario: 关键词命中会话的早期轮次
- **WHEN** 某会话共有 3 轮交互，关键词仅出现在第 1 轮，且用户以该关键词搜索
- **THEN** 该会话必须出现在搜索结果中

#### Scenario: 命中历史失败但最新成功的会话
- **WHEN** 某会话历史上存在失败轮次，但最新一轮状态为成功，且用户筛选 `status=failed`
- **THEN** 该会话必须出现在搜索结果中

#### Scenario: 关键词与状态需由同一轮次同时满足
- **WHEN** 某会话第 1 轮命中所搜索的关键词但状态成功，第 2 轮状态失败但不含该关键词
- **THEN** 同时传入该关键词与 `status=failed` 时，该会话必须（MUST）不出现在搜索结果中

#### Scenario: 非分组模式保持逐轮语义
- **WHEN** 客户端以 `group_by_conversation=false` 传入关键词或状态
- **THEN** 返回结果必须仅包含自身满足该条件的轮次记录

#### Scenario: 非管理员筛选任务会话不越权
- **WHEN** 非管理员用户请求 `/api/v1/chat/history?scope=task`
- **THEN** 返回结果必须仅包含该用户自身可见的会话，不得包含其他用户的会话

### Requirement: 侧边栏筛选交互与激活态回显
会话历史侧边栏必须（MUST）提供筛选入口与筛选面板，且在无任何筛选激活时不得（MUST NOT）额外占用垂直空间。

- 筛选入口必须（MUST）置于搜索输入框外部右侧，不得与输入框内嵌的清空按钮重叠。
- 存在激活筛选时，入口必须（MUST）呈现激活态，并以可移除 chip 的形式回显当前筛选条件。
- 筛选面板必须（MUST）包含来源、状态、时间三组控件；当父组件指示允许时，必须（MUST）包含智能体筛选控件。
- 面板必须（MUST）支持点击外部或按 Esc 关闭，并提供重置动作。
- 移动端必须（MUST）以不会遮挡内容的内联展开方式呈现筛选面板。

#### Scenario: 默认状态无额外占位
- **WHEN** 用户打开侧边栏且未设置任何筛选
- **THEN** 侧边栏必须只显示搜索行，不得显示筛选面板或 chip 回显行

#### Scenario: 激活筛选后回显
- **WHEN** 用户将来源设置为「任务会话」
- **THEN** 漏斗入口必须呈现激活态，且必须出现一个可移除的「任务会话」chip

#### Scenario: 集成锁定场景隐藏智能体筛选
- **WHEN** 父组件传入的 `showAgentFilter` 为 `false`
- **THEN** 筛选面板必须（MUST）不展示智能体筛选控件

#### Scenario: 移动端内联展开
- **WHEN** 用户在移动端全屏抽屉中点击筛选入口
- **THEN** 筛选面板必须以内联展开方式呈现，不得遮挡或与抽屉滚动容器产生冲突

### Requirement: 筛选变更的状态一致性
筛选条件发生变化时，前端必须（MUST）重置分页与折叠状态，避免出现数据遗漏或结果被隐藏。

- 筛选变化必须（MUST）将页码重置为第一页并清空现有列表后重新请求。
- 筛选变化必须（MUST）清空日期分组的折叠状态，避免默认折叠的分组隐藏筛选结果。
- 筛选结果为空时，必须（MUST）展示区别于「暂无会话历史」的空状态，并提供重置筛选入口。

#### Scenario: 筛选后重置分页
- **WHEN** 用户已滚动加载到第 3 页，随后切换会话来源筛选
- **THEN** 系统必须从第 1 页重新请求，且不得出现偏移错位导致的记录遗漏

#### Scenario: 筛选后展开被折叠分组
- **WHEN** 「更早」分组处于折叠状态，用户切换筛选条件且结果落在「更早」分组
- **THEN** 该分组必须处于展开状态，使筛选结果对用户可见

#### Scenario: 筛选无结果的空状态
- **WHEN** 用户设置的筛选条件没有任何匹配会话
- **THEN** 系统必须展示「无匹配筛选结果」提示并提供重置筛选入口，不得展示「暂无会话历史」
