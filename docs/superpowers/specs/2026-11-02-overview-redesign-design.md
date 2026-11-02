# 概览页改版设计（管理员 / 平台用户）

日期：2026-11-02
Mock：[`prototypes/2026-11-02-overview-redesign.html`](../../../prototypes/2026-11-02-overview-redesign.html)

## 背景

现有「系统概览」存在几处实质问题，不只是观感：

1. **图例溢出**：`AgentHealthPanel.toolPieOption` 的 legend 是 `orient: vertical, left: left, top: center`，没有 `type: 'scroll'` 也没有高度上限。工具一多，图例按内容高度居中并溢出容器，被裁掉半截（线上表现为 `mcp_r…`、`publis…`）。
2. **环形图承载不了排行语义**：十几个扇区里最小的只占 3%，无法比较；长 MCP 工具名在 10px 图例里必然截断。
3. **五个指标卡权重相同**：成功率（结论）与总用户数（背景）占同样的面积与字号，视线无处停留。
4. **耗时趋势大量为 0**：24 小时里 15 小时无数据，Y 轴仍拉满，曲线被压成两根竖刺。
5. **异常信息与定位脱节**：知道「12 次失败」，但不知道集中在哪个工具、要跳去哪。

## 目标

- 让「当前是否健康」成为第一眼信息，失败集中在哪个工具一眼可见。
- 工具分布从「占比饼图」改为「可比较的排行」。
- 管理员与平台用户共用同一套视觉语言，但指标各自贴合其职责。

## 视觉基调

页面主体保持白卡 + 浅灰底，主色只出现在圆点标记、链接、进度条、图表主线。**不使用大面积色块**——第一版 mock 用整块蓝色渐变卡承载成功率，被判定为「页面就这一块大蓝，很突兀」，已改为：极淡冷色渐变底（`#f7faff → #fff`）+ 左侧 3px 主色竖条 + 更大字号来建立层级。

## 组件设计

新增组件放在 `frontend/src/components/dashboard/`，与现有组件同目录。

| 组件 | 职责 | 输入 |
| :--- | :--- | :--- |
| `MetricHeroCard.vue` | 主角指标：标题、大号数值、右侧成功/失败明细、底部进度条 | `label/value/unit/total/success/failed/tone` |
| `MetricMiniCard.vue` | 次级指标：图标 + 标题 + 数值 + 脚注 | `label/value/unit/foot/icon/iconColor` |
| `ToolUsageRanking.vue` | 工具调用排行：横向条形 + 计数 + 占比，末项聚合「其余 N 项」 | `items: {name,value}[]`，`limit`，`total` |
| `LatencyTrendPanel.vue` | 耗时趋势：面积渐变 + 阈值虚线 + 超阈区间底色 + 峰值标注 | `points: {hour,avg_ms}[]`，`threshold` |
| `CoreMetricsPanel.vue` | 核心指标：名称 + 状态徽章 + 数值 | `items: {label,value,unit,tone}[]` |
| `AgentPerformanceTable.vue` | 智能体性能：调用量 / 平均耗时 / 成功率 | `rows` |
| `AgentTokenBars.vue` | Token 分布：横向条形 | `items: {name,value}[]` |
| `DashboardAlertStrip.vue` | 异常摘要条：数量 + 涉及对象（不带跳转按钮） | `count/subjects` |

`ToolUsageRanking` 底部提供 `#footer` 插槽，用于放「最近失败」列表，避免中部左栏留白。

### 数据映射

全部复用现有接口，**不改后端**：

- `GET /api/portal/dashboard/agent-stats` → `health_stats`、`tool_usage`、`performance_trend`、`recent_errors`、`agent_performance`
- `GET /api/portal/dashboard/stats` → `total_users`、`active_users`、`api_calls`、`total_tokens`、`success_rate`、`avg_response_time`、`api_key_status`

排行条形的长度 = `value / total`，与右侧百分比严格同源（第一版 mock 曾按最大值归一化，与百分比对不上，已修正）。

### 两个视角的差异

| | 管理员 | 平台用户 |
| :--- | :--- | :--- |
| 主角卡 | 接口成功率 | 我的成功率 |
| 次级卡 | API 调用 / Token / 用户 / 工具成功率 | 我的调用 / 我的 Token / 平均响应 / API Key 状态 |
| 中部左 | 工具调用分布 + 最近失败 | 我的工具使用 + 我的最近失败 |
| 中部右 | 执行耗时趋势 + 核心指标 | 我的耗时趋势 + 我的指标 |
| 底部 | 智能体性能排行 / Token 分布 / 最近用户 / 24h 趋势 | 智能体性能 / Token 分布 / 最近调用明细 / 快速开始 |

## 状态语义

统一阈值，写在组件常量里并可被覆盖：

- 成功率 ≥ 99% 绿色进度条；< 99% 琥珀色。
- 平均耗时 > 5000ms 标记「偏高」（琥珀），否则「正常」（绿）。
- 工具成功率 < 95% 标记异常（红）。

## 不做的事

- 不改后端接口与聚合 SQL。
- 概览页不再引用的旧组件（`StatsCard`、`AgentHealthPanel`、`AgentPerformancePanel`、`AgentTokenDistributionChart`、`RecentFailuresCard`）一并删除，避免死代码累积；`RecentUsersCard`、`RecentCallsCard`、`RequestTrendChart` 仍在使用，保留。
- 不改动 `/dashboard/playground`、`ChatLogs` 等其它入口。
- 「最近用户」「24h 请求趋势」沿用现有组件与样式，仅调整外层布局。

## 验收

1. 工具数量 ≥ 12 时，排行列表完整可读，无截断、无横向滚动。
2. 耗时趋势能直接看出「哪一时段超过阈值」。
3. 管理员与普通用户进入各自概览，均无布局错位；`role !== 'admin'` 不会看到平台级指标。
4. `vue-tsc --noEmit` 通过；`pytest tests/frontend` 全绿。
5. 现有 `test_overview_header_contract.py` 的标题与工具栏断言保持通过（改造不得破坏工具栏的 ghost 样式约定）。
