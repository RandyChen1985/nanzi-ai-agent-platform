# API 工具「业务分组页签」设计（2026-11-12）

原型：[`prototypes/2026-11-12-tool-group-tabs.html`](../../../prototypes/2026-11-12-tool-group-tabs.html)（方案 A / B / C 对照，含 6 组与 11 组两种数据规模）

## 1. 需求

用户（附截图，API 工具注册表页）提出三点：

1. 新建 / 编辑 API 工具时要有「业务分组」；
2. 顶部搜索面板要能按它过滤；
3. 页面能按这个分组做多 Tab 展示。

用户同时希望「这个分组只用来过滤查询」。

## 2. 现状核查（读码结论，非推测）

| 诉求 | 现状 | 依据 |
| --- | --- | --- |
| 新建时能填 | **已存在** | `ToolWizard.vue:293` 业务分组输入框（datalist 候选） |
| 编辑时能改 | **已存在** | 编辑弹窗 `GroupSelect`（可选已有、也能新建） |
| 顶部能按它筛 | **已存在** | 筛选面板的分组下拉（用户截图那一项） |
| 多 Tab 展示 | **缺失** | 只有「列表视图 / 分组视图（折叠）」两档 |

额外既有能力：表格行内分组徽标点一下即「只看该组」、勾选后批量设置分组、「分组管理」弹窗。

**一处必须纠正的措辞**：`group_name` 不只用于过滤。智能体配置的「工具能力」步骤也按它归组
（`frontend/src/utils/toolGrouping.ts`、`frontend/src/utils/agentRestrictions.ts`）。所以界面文案
不能写「仅用于过滤查询」，否则与真实行为不符，且会给后续维护者埋错前提。

## 3. 方案选择

| 方案 | 形态 | 结论 |
| --- | --- | --- |
| **A（采用）** | 页签常驻在工具栏下方，与「列表 / 分组」视图切换解耦 | 页签决定「看哪一组」，视图切换只管「这一组怎么排」；与筛选下拉共用状态；分组已确定时表格去掉分组列 |
| B | 页签只在「分组视图」里出现 | 改动最小，但「按分组切换」不是第一级动作，要先切视图 |
| C | 页签 + 组内再按 HTTP 方法折叠 | 层级过深，无证据表明单组会大到需要二级导航 |

## 4. 状态与数据流

**单一状态源**：`groupFilter`（`'all'` / `UNGROUPED_FILTER` / 组名）。页签点击与筛选面板下拉
都写它，`filterByGroup`（行内徽标）也写它。不存在第二份分组状态。

**两种计数口径，刻意分开**：

| 用途 | 计算 | 语义 |
| --- | --- | --- |
| 页签 / 筛选下拉 | `computeGroupTabs(tools, {keyword, method, status}, UNGROUPED_FILTER)` | 当前筛选条件下的**命中数** |
| 表单分组下拉 / 分组管理 | `groupFilterOptions`（全量） | 该组**一共有多少**工具 |

混用必有一边说谎：页签显示全量会让用户点进空组，表单显示命中数会让用户以为分组里的工具少了。

**页签顺序**：`全部` → 各组（命中数降序，同数量按中文名）→ `未分组`（仅 > 0 时出现，固定末位）。

## 5. 模块边界

- `frontend/src/utils/toolGroupTabs.ts`（新增，纯函数）：
  `matchesToolFilters(tool, filters)`、`toolGroupName(tool)`、`computeGroupTabs(tools, filters, ungroupedKey)`
  → 返回 `{ total, ungrouped, groups, items }`。无 Vue 依赖，Node 里可直接跑断言。
- `frontend/src/components/system/ToolRegistry.vue`：
  `registryFilters`（computed）→ `filteredTools` 与 `groupTabCounts` 共用 `matchesToolFilters`，
  页签只负责渲染 `groupTabItems` 与写 `groupFilter`。

## 6. 交互细节

- **分组列**：`groupFilter === 'all'` 时才渲染「业务分组」列；空态 `colspan` 随之 6 / 5 切换。
- **溢出**：页签容器横向滚动（隐藏滚动条）；`scrollWidth - clientWidth > 4` 时在右侧出现
  「更多分组」下拉，列出全部页签项。用 `ResizeObserver` + `resize` 双监听，覆盖侧栏折叠、
  分组增删、窗口缩放三种宽度变化。
- **兜底**：当前 `groupFilter` 不在页签项里（搜索后该组已无命中、或分组刚被删除）时自动回到
  `all`——否则页签消失、表格空白、下拉空值，用户只会认为功能坏了。
- **无障碍**：页签容器 `role="tablist"`，按钮 `role="tab"` + `aria-selected`；「更多分组」按钮带
  `aria-expanded` / `aria-haspopup`。

## 7. 边界条件

| 场景 | 行为 |
| --- | --- |
| `group_name` 为 `''` / 纯空格 / `null` | 一律按未分组统计（历史数据三种都存在） |
| 所有工具都有分组 | 不渲染「未分组」页签 |
| 三层筛选后某组 0 命中 | 该组不出现；当前选中组被筛空则回 `all` |
| 三层筛选后全部 0 命中 | 只剩「全部 0」，表格显示「暂无匹配工具」 |
| 分组 11+ 个 | 横向滚动 + 「更多分组」下拉 |

## 8. 测试

- `tests/frontend/tool_group_tabs_probe.cjs`：Node 加载真实 TS 模块真跑，输出计数与顺序。
- `tests/frontend/test_tool_group_tabs_contract.py`（13 条）：页签顺序与计数、方法与状态与关键词
  过滤、无命中、无未分组、空白分组、`matchesToolFilters` 契约；源码侧锁「与筛选共用
  `groupFilter`」「计数来自纯函数」「分组列与 `colspan` 联动」「溢出有出口」「脏分组兜底」。

## 9. 不在本次范围

- 脚本工具页（`ScriptToolList.vue`）不做同样的页签——用户指的是 API 工具页，且脚本工具走的是
  自己那套筛选面板；要加另开需求。
- 业务分组改为受控字典（只能选不能敲）：现状允许自由输入，会存在「履约罚款 / 履约罚金」近义组，
  建议另在「分组管理」里做合并能力。
