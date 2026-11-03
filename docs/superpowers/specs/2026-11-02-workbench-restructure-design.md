# 我的工作台重构设计（2026-11-02）

## 背景

`/dashboard/workbench` 的工作台 tab 在 2026-09 做过一轮「UI/UX 全面升级」，那轮的产出是六张外观精致的白卡片：待处理、最近会话、最近产出、最近任务、常用助手、我的资源，每张都带 eyebrow 标签、圆点状态、彩色徽标和虚线占位。

问题不在单张卡片好看不好看，而在**六张卡加起来读起来很累**：

- 「最近会话 + 最近产出 + 最近任务」三块各自最多 4 条，加起来占了近 500px 只装了 9 条记录，用户要判断「今天做了什么」得跨三块拼接；
- 每块底部都写着「更多…会出现在这里」的虚线占位，在数据本来就少的时候，占位比内容还显眼；
- 顶部还有一行 summary chips，把下面卡片上已经写着的数字再罗列一遍；
- 运行中卡片画了一条固定 33% 的脉冲进度条——后端没有进度字段，它永远不会动；
- 待处理卡片副标题里有「来源：tasks」这类写给自己看的注记。

用户第一轮的判断是「好像也不怎么样啊，你要不要按照合理的、美观的、信息密度综合考虑重构一版」。所以这次的主导目标不是配色，而是**信息密度与阅读路径**。

## 设计思路

先把页面上的东西按「我会怎么用它」重新归类，而不是按「它来自哪张表」：

| 归类 | 内容 | 形态 |
|---|---|---|
| 要我做 | 待处理事项 | 主列，按严重度排序，左侧竖条 |
| 正在跑 | 运行中的任务 | 主列，紧凑行 + 真实耗时 |
| 我做过的 | 会话 / 产出 / 任务 | 主列，**一条统一时间线** |
| 我有的 | 知识库、数据集、记忆、tokens… | 侧栏，紧凑资源格 |
| 我常用的 | 常用助手 | 侧栏，行列表 |

### 关键决策

**1. 三类活动合并成一条时间线。**
`resume_items`、`latest_results`、`recent_tasks` 的 item 结构本来就同构，且都带 `occurred_at`。前端按时间倒序合并即可，后端零改动。合并后配四个筛选 tab（全部 / 会话 / 产出 / 任务）让用户回到单一类型。

合并的风险是丢掉可达性——原来三块各自有「打开智能助手 / 查看全部结果 / 查看执行记录」入口。解决办法是底部入口**跟着当前 tab 变**：选「会话」时底部是「打开智能助手」，选「任务」时是「查看执行记录」，选「全部」时只显示条数。

**2. 空态按数据决定，不按 mode 决定。**
原来的实现里，`mode === 'active'` / `'quiet'` / `'new_user'` 各有一整套重复的模板分支，同样三张卡在 active 和 quiet 分支里逐字重复了两遍。

重构后：

- 待处理为 0 → 不留空卡片，换成一行绿色「今天没有待处理事项 · 有新的进展会在这里提示」；
- 运行中为 0 → 整块消失，连提示都不给（没在跑就是没在跑，不需要解释）；
- 运行中的显示条件是 `payload.running_items.length`，不是 `mode === 'active'`——安静态同样可能有任务在跑。

`mode` 只保留两个用途：`quiet` 决定要不要显示那行绿色提示，`new_user` 决定要不要显示欢迎块和推荐场景。

**3. 侧栏放「我有的」和「我常用的」。**
用户要求「我的资源放上面，常用助手放下面」，且图标要「中性灰」。

资源格从 8 张彩色图标卡改为左图标、右内容的两列紧凑格：30px 图标块 + label + 数值 + 单位。图标统一 `#f5f7fa` 底 + `text-gray-400`，**只有未读通知保留琥珀**——它是状态不是分类，值得被单独看见。这一条是有意的例外，不改配色分工的话，8 个灰图标里没有一个会吸引注意力。

**4. 删掉所有假信息。**
运行中的进度条改成按 `execution_time_ms` 或 `occurred_at` 计算的真实耗时（「已运行 1 分 36 秒」）；「来源：xxx」这类开发注记移除；summary chips 移除。

**5. emoji 清零。**
`utils/workbenchDisplay.ts` 里给每类 item 配了 emoji 图标（⏱📰📊💬🔔•），助手也用 `AGENT_EMOJIS` 哈希取一个。这违反本仓库「统一线性图标、禁止 emoji 当图标」的约定，本次全部换成内联 SVG，无引用的 `workbenchKindLabel` / `workbenchKindClass` / `workbenchSeverityClass` / `workbenchKindIcon` / `workbenchAgentEmoji` 一并删除。

## 组件变化

| 操作 | 组件 | 说明 |
|---|---|---|
| 新增 | `WorkbenchFeed.vue` | 三类活动合并的时间线，含筛选 tab 与随 tab 变的底部入口 |
| 新增 | `WorkbenchResourceGrid.vue` | 侧栏资源格（左图标右内容，两列） |
| 重写 | `WorkbenchAttention.vue` | 左侧竖条 + 图标 + 标题/徽章 + 一行副信息 + 动作按钮 |
| 重写 | `WorkbenchRunning.vue` | 紧凑行，真实耗时替代假进度条 |
| 重写 | `WorkbenchNextScheduled.vue` | 无标题单行条，跟在「正在运行」之后 |
| 重写 | `WorkbenchAgents.vue` | 侧栏行列表，头像 + 名称 + 描述 + 调用次数（hover 出置顶） |
| 重写 | `WorkbenchScenarios.vue` | 新用户态的 2×2 场景卡 |
| 重写 | `WorkbenchSectionHeader.vue` | 3px 竖条 + 标题 + 计数徽章 + 右侧文本入口 |
| 删除 | `WorkbenchResume.vue` / `WorkbenchResults.vue` / `WorkbenchTasks.vue` | 内容已并入 `WorkbenchFeed` |
| 删除 | `WorkbenchMobileViewAll.vue` / `WorkbenchItemMeta.vue` | 无调用方 |
| 保持不动 | `WorkbenchPersonalResources.vue` | 同时被 `components/embed/WelcomeDashboard.vue` 使用，改布局会连带改嵌入页 |

## 实现要点

**图标用 path 数组，不用条件标签。**
在 `<svg>` 里写 `<template v-if>` 或 `<g v-if>` 会让 `@vue/compiler-sfc` 的解析器误判模板块边界，最终报一个指向 `<script setup>` 行的 `Element is missing end tag`——错误位置离真正原因很远，很容易排查到错误方向。改成每组图标算出 `string[]`，模板只写 `<path v-for="(d, i) in iconPaths(item)" :key="i" :d="d" />`。

同理，**注释里也不要出现尖括号标签**（`/* 不能把 <template v-if> 放进 svg */` 这种注释同样会被计入标签深度）。

**助手行不能是 `button`。**
行尾的置顶按钮是真按钮，外层再用 `button` 会得到非法嵌套。外层改为 `div` + `role="button"` + `tabindex="0"` + `keydown.enter` / `keydown.space`，交互等价且合法。

**响应式。**
`grid xl:grid-cols-[minmax(0,1fr)_336px]`：≥1280px 双栏且侧栏 sticky；以下折叠为单列，资源与助手落在主内容之后。

## 验证

- `tests/frontend/test_personal_workbench_contract.py`：12 项通过。其中 2 项是重写的——正向断言新结构，反向断言旧形态不得回归（四个已删除组件名、`今日运行正常`、`待处理 0`、`animate-pulse`、`w-1/3`、三句「更多…会出现在这里」、`来源：`、六个 emoji 全部禁止出现）。
- `vue-tsc --noEmit`：0 报错。
- `vite build`：通过。
- 三种状态（有待处理 / 空闲无待办 / 新用户全空）用真实组件 + 假数据在浏览器实渲染截图确认，截图存于 `prototypes/shots/impl-*.png`，设计稿为 `prototypes/2026-11-02-workbench-restructure.html`。

## 未做的事

- 后端 `workbench_home_service` 未改动，三类活动各自 4 条的上限不变；要「看到第 11 条」得改后端分页。
- 没有做移动端专项适配（沿用折叠为单列的默认行为）。
- `WorkbenchPersonalResources.vue` 的旧卡片布局保留，仅不再被工作台使用。
