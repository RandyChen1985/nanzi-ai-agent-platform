# 定时任务表单章节折叠（可收起的三段）

**需求（用户原话）：**「定时任务，新建，编辑的弹框中，几个章节可以支持折叠（做什么，什么时候跑，结果发到哪）」

**确认结论：** ① 三组默认全部展开、点标题手动收起；② 收起后标题行显示摘要，组内必填有错时显示红点；③ 实现走方案 A（抽子组件）。

---

## 1. 现状

- `TaskCenter.vue`（3528 行）的新建/编辑弹窗已按方案 A 分为三组，组标题是 `border-b border-dashed` 的浅色分隔条（`做什么` 约 2494 行、`什么时候跑` 约 2907 行、`结果发到哪` 约 3070 行），**没有任何折叠能力**。
- 组内已有独立小折叠「执行失败策略」（`showAdvancedRetry`，约 3033-3042 行），使用 `▸/▾` 字符 + `v-show` + `aria-expanded`；本次复用它所在的视觉语言，但不合并进组级折叠。
- 摘要数据源已存在且被右侧粘性摘要栏使用：`taskSummaryText`（`when` / `whenValid` / `agent` / `what` / `constraints` / `channels`）与 `taskSummaryNext`（`cronNextRuns[0]`）。**折叠摘要直接复用这两个 computed，不新增请求、不复制拼装规则。**
- 校验现状：`saveTask`（1182 行）只校验 cron 表达式并 `showToast`；任务名称与提示词没有前端校验，靠后端 422（`app/schemas/task.py` 中 `name` / `prompt` 为 `Field(...)` 必填）。

## 2. 组件

新增 `frontend/src/components/task/TaskFormSection.vue`（`<script setup lang="ts">`，与同目录 `CronAdvancedBuilder.vue` 等保持一致的写法）。

```ts
props: {
  sectionId: string      // 内容区 id，供 aria-controls 与滚动定位使用
  title: string          // 「做什么」
  hint?: string          // 标题后的一句话说明
  summary?: string       // 收起时显示的内容
  hasError?: boolean     // 组内是否有必须补全的项
  modelValue: boolean    // 是否展开
}
emits: ['update:modelValue']
```

结构：

- 标题行是整行 `<button type="button">`（键盘 Enter/Space 原生可用），左侧 `title`，其右 `hint`，最右侧 `▸/▾`（`rotate-180` 过渡）。
- `hasError` 时：标题行出现红色「!」点，摘要位置**替换**为红字「需补全」（不并存，避免一行里既有摘要又要求补全而互相矛盾）。
- 收起且有 `summary` 时：标题行下方一行小字显示摘要（`text-[11px] text-gray-500`，过长截断）。
- 内容区 `v-show="modelValue"`，`id="sectionId"`；按钮带 `aria-expanded` 与 `aria-controls`。
- 保留现有标题行的 `border-b border-dashed border-gray-200 pb-1` 观感，确保视觉与改组前一致。

默认插槽承载组内全部内容；`hint` 为纯文本，需要富文本/链接时用具名插槽 `#hint` 覆盖，不改 props。

`sectionId` 固定为 `task-section-what` / `task-section-when` / `task-section-result`，供 `aria-controls` 与保存失败时的 `scrollIntoView` 共用。

## 3. 三个章节的收起摘要

| 组 | 组内内容（全部进插槽） | 收起摘要 |
| --- | --- | --- |
| 做什么 | 任务名称、执行大脑、① 要做什么、② 输出有什么要求 | `《任务名》· 智能体名 · 提示词首句`；缺项显示「未填写」 |
| 什么时候跑 | 运行周期配置、接下来三次运行、执行失败策略（内层折叠） | `每天 09:00 · 下次 11-19 09:00`；表达式非法时显示「表达式格式不正确」 |
| 结果发到哪 | 结果通知渠道（含群选择） | `企业微信 + 邮件`；无渠道显示「不发送通知」 |

提示词首句沿用 `taskSummaryText.what`（已按 `[\n。；;]` 切分取首句）：`buildTaskSectionSummaries` 的 `firstSentence` 入参就是 `taskSummaryText.what`，不自造截断规则。

## 4. 错误提示与保存联动

新增纯函数模块 `frontend/src/utils/taskFormSections.ts`：

```ts
export type TaskSectionKey = 'what' | 'when' | 'result'

export function resolveTaskSectionIssues(input: {
  name: string
  prompt: string
  cronExpr: string
  cronValid: boolean
}): { issues: Record<TaskSectionKey, boolean>; firstInvalid: TaskSectionKey | null }

export function buildTaskSectionSummaries(input: {
  name: string
  agent: string
  firstSentence: string
  when: string
  whenValid: boolean
  nextRun: string
  channels: string[]
}): Record<TaskSectionKey, string>
```

- `what` 出错 = 名称为空或提示词为空；`when` 出错 = cron 为空或表达式非法；`result` 目前无必填，恒为 `false`（渠道默认 `['portal']`，且保存前会 `pruneUnavailableNotificationChannels()`）。
- `firstInvalid` 按 `what → when → result` 顺序取第一个，供保存失败时自动展开。
- `saveTask` 在现有 cron 校验之前补两道：名称空 → `showToast('请填写任务名称', 'error')` + 展开 `what`；提示词空 → `showToast('请填写「要做什么」', 'error')` + 展开 `what`。cron 空/非法 → 展开 `when` 后照旧 toast。展开后若内容区在视口外，用 `document.getElementById(sectionId)?.scrollIntoView({ block: 'nearest' })` 滚到可见。
- 红点与摘要同源：`hasError` 来自 `resolveTaskSectionIssues`，摘要来自 `buildTaskSectionSummaries`，两者都由同一份输入计算，避免「红点亮着但摘要说正常」。

## 5. 边界

- 三组默认展开；每次打开弹窗都全展开（不做持久化、不做跨弹窗记忆），符合已确认的选择。
- 编辑已有任务同样全展开。
- 不新增「全部展开 / 全部收起」按钮。
- 窄屏（`isMobile`）与右侧粘性摘要栏的布局完全不变，折叠只作用于左侧表单列。
- 组内「执行失败策略」内层折叠保持独立，两套折叠状态互不影响。
- 零后端、零数据结构、零接口改动。

## 6. 测试

| 层 | 文件 | 断言 |
| --- | --- | --- |
| 纯函数 | `tests/frontend/task_form_sections.mjs`（Node 直跑） | 摘要拼装：正常/缺名称/无渠道/cron 非法/无下次运行；错误归组：仅名称空 → `what`、cron 非法 → `when`、两者都错时 `firstInvalid='what'`、全对时无错 |
| 契约 | `tests/frontend/test_task_form_section_contract.py` | `TaskFormSection.vue` 存在且标题行是 `<button>`、带 `aria-expanded`/`aria-controls`、`v-show` 绑 `modelValue`；`TaskCenter.vue` 三组标题都通过该组件渲染且传入 `summary`/`has-error`；`saveTask` 引用 `resolveTaskSectionIssues` 并展开 `firstInvalid`；`showAdvancedRetry` 内层折叠仍在 |
| 类型与模板 | `vue-tsc -b`、`compile_vue_templates.cjs` | 与本批改动前基线一致 |

## 7. 风险与取舍

- **漏包内容**：把某段（如「接下来三次运行」）留在组件外，收起后仍露出碎片。契约测试断言三组的组内首尾标记都在 `TaskFormSection` 标签内。
- **摘要重复**：右侧粘性摘要栏已有一句话复述；收起摘要与之同源、文案不同（分段短摘要 vs 整句），允许重复。
- **不把「输出有什么要求」拆成独立折叠**：它属于「做什么」组，拆开会让 ①/② 两栏指令的语义断裂。
