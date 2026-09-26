## MODIFIED Requirements

### Requirement: History Management
系统 **MUST** 提供侧边栏以浏览和管理历史对话记录。
- **Layout**: 采用双栏布局，左侧为历史记录列表，右侧为当前对话窗口。
- **List Items**: 列表项应展示对话时间、摘要、Agent 版本及执行状态。
- **Interaction**: 点击列表项可加载对应的历史对话详情。
- **Filtering**: 支持按关键词与多维度筛选检索历史记录，筛选维度包括会话来源（全部 / 任务会话 / 普通对话）、智能体、执行状态（成功 / 失败）与时间范围。所有筛选条件 **MUST** 在服务端生效以配合分页；关键词与执行状态的过滤语义 **MUST** 覆盖会话内的全部轮次，而非仅匹配最新一轮。

#### Scenario: Browse History
- Given User is on the Agent Debug page
- When User clicks a history item from the sidebar
- Then The main chat window loads the conversation context of that session
- And The user can continue the conversation from that point

#### Scenario: Filter history by scope
- Given User is on the Agent Debug page and the history sidebar is open
- When User sets the scope filter to "任务会话"
- Then The sidebar **MUST** list only conversations whose `conversation_id` starts with `task_conv_`
- And The active filter **MUST** be reflected in the filter entry's active state

#### Scenario: Filter history by keyword across all turns
- Given A conversation contains the searched keyword only in an earlier turn
- When User searches that keyword
- Then The conversation **MUST** appear in the sidebar result list

#### Scenario: Reset pagination on filter change
- Given User has scrolled the history list to a later page
- When User changes any filter dimension
- Then The system **MUST** re-request from the first page and clear collapsed date groups
