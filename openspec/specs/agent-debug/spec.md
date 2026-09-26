# agent-debug Specification

## Purpose
TBD - created by archiving change add-agent-debug-menu. Update Purpose after archive.
## Requirements
### Requirement: Chat Interface
系统 **MUST** 提供类似对话的交互界面。
- **Inputs**: 支持多行文本输入。
- **Actions**: 发送按钮。
- **Shortcuts**: 支持 `Ctrl + Enter` (Windows/Linux) 或 `Cmd + Enter` (Mac) 快捷键发送消息。

#### Scenario: Send Message via Shortcut
- Given User has typed a message in the input box
- When User presses `Cmd + Enter`
- Then The message is sent
- And The input box is cleared

### Requirement: Process Visualization (Chain of Thought)
系统 **MUST** 展示智能体的思考和执行过程（即 CoT 或 Tool Calls），并提供完整的日志查阅能力。
- **Visibility**: 默认显示主要步骤（如“正在查询数据库...”、“正在分析数据...”）。
- **Details**: 支持展开/折叠查看详细的输入/输出（如 SQL 语句、API 返回的 JSON）。
- **Traceability**: 在对话界面提供“查看日志”入口，点击可查看该次对话的完整执行链路，包括：
    - 调用的工具名称 (Tool Name)
    - 传递的参数 (Arguments)
    - 工具返回的原始响应 (Raw Response)
    - 执行耗时与状态

#### Scenario: View Detailed Logs
- Given Agent has completed a response (or is streaming)
- When User clicks the "View Logs" button on the message bubble
- Then A log viewer panel/modal appears
- And The panel shows a chronological list of all execution steps
- And User can inspect the raw JSON of parameters and results

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

### Requirement: Independent Debugging Page
系统 **MUST** 提供一个独立的页面用于智能体调试。

#### Scenario: Navigate to Agent Debugging
- Given User logs into the dashboard
- When User clicks "智能体调试" in the sidebar
- Then The system navigates to `/dashboard/agent-debug`
- And The "智能体调试" page is displayed

### Requirement: Menu Accessibility
“智能体调试”菜单 **MUST** 在 Dashboard 侧边栏中可见。

#### Scenario: Menu Visibility
- Given User is on the dashboard
- Then The "智能体调试" menu item is visible in the sidebar

### Requirement: Global Execution Visibility
系统 **MUST** 在仪表板中提供智能体运行状况的宏观统计。
- **Metrics**: 包含工具调用成功率、平均耗时、最活跃工具。
- **Periodicity**: 支持按“今日”、“本周”、“本月”进行筛选。

#### Scenario: View Tool Success Rate
- Given Admin is on the Overview page
- When They select "今日" as the time range
- Then A pie chart shows the distribution of tool call results (Success vs Error)
- And A card displays the overall success percentage

### Requirement: Performance Monitoring
系统 **MUST** 追踪智能体的响应性能。
- **Metric**: 记录每一步（Step）的平均执行时间。

#### Scenario: Identify Slow Tools
- Given Admin views the Performance Line Chart
- When They hover over a specific tool's line
- Then They can see the average latency trend for that tool over the last 24 hours

