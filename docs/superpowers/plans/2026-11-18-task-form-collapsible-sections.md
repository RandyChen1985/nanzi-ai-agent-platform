# 定时任务表单章节折叠 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让定时任务新建/编辑弹窗的「做什么 / 什么时候跑 / 结果发到哪」三组可以折叠，收起后仍能看到摘要与必填错误。

**Architecture:** 新增一个受控折叠组件 `TaskFormSection.vue` 承载三组的标题行与插槽；摘要与错误归组抽成纯函数 `utils/taskFormSections.ts`，供组件绑定与 `saveTask` 校验共用；`TaskCenter.vue` 只负责三处模板包裹、三个展开 ref 与保存前校验前移。零后端、零数据结构改动。

**Tech Stack:** Vue 3.5 `<script setup lang="ts">` + Tailwind 3；测试用 Node（`--experimental-strip-types`）跑纯函数、pytest 契约锁结构，`vue-tsc -b` 与 `compile_vue_templates.cjs` 做类型/模板校验。

**规格：** `docs/superpowers/specs/2026-11-18-task-form-collapsible-sections-design.md`

---

## 文件结构

| 文件 | 责任 |
| --- | --- |
| Create `frontend/src/utils/taskFormSections.ts` | 摘要拼装 `buildTaskSectionSummaries` + 错误归组 `resolveTaskSectionIssues` + `TASK_SECTION_IDS` |
| Create `frontend/src/components/task/TaskFormSection.vue` | 受控折叠：标题行按钮、`aria-expanded/controls`、收起摘要、错误红点、`v-show` 内容插槽 |
| Modify `frontend/src/views/TaskCenter.vue` | 三处标题+内容包进组件；三个展开 ref；摘要/错误 computed；`saveTask` 校验前移并自动展开 |
| Create `tests/frontend/task_form_sections.mjs` | 纯函数行为断言（Node 真跑） |
| Create `tests/frontend/test_task_form_section_contract.py` | 结构契约（组件语义、三组接入、保存联动） |
| Modify `tests/CHECKLIST.md` | 登记本次交付 |

---

### Task 1: 纯函数模块（摘要拼装 + 错误归组）

**Files:**
- Create: `frontend/src/utils/taskFormSections.ts`
- Test: `tests/frontend/task_form_sections.mjs`

- [ ] **Step 1: 先写失败测试**

创建 `tests/frontend/task_form_sections.mjs`：

```js
// 折叠摘要与错误归组的真实行为：源码字符串断言看不出来，必须调用实现。
import assert from 'node:assert/strict'

import {
  buildTaskSectionSummaries,
  resolveTaskSectionIssues,
  TASK_SECTION_IDS,
} from '../../frontend/src/utils/taskFormSections.ts'

// --- 1. 三段 id 固定，供 aria-controls 与滚动定位共用 ---
assert.deepEqual(TASK_SECTION_IDS, {
  what: 'task-section-what',
  when: 'task-section-when',
  result: 'task-section-result',
})

// --- 2. 全部填好：无错，firstInvalid 为 null ---
const ok = resolveTaskSectionIssues({
  name: 'PUE 日报',
  prompt: '查询昨日 PUE 并生成日报',
  cronExpr: '0 9 * * *',
  cronValid: true,
})
assert.deepEqual(ok.issues, { what: false, when: false, result: false })
assert.equal(ok.firstInvalid, null)

// --- 3. 只有名称空 → 归到 what ---
const noName = resolveTaskSectionIssues({
  name: '   ',
  prompt: '查询昨日 PUE',
  cronExpr: '0 9 * * *',
  cronValid: true,
})
assert.equal(noName.issues.what, true)
assert.equal(noName.firstInvalid, 'what')

// --- 4. 只有提示词空 → 同样归到 what ---
const noPrompt = resolveTaskSectionIssues({
  name: 'PUE 日报',
  prompt: '',
  cronExpr: '0 9 * * *',
  cronValid: true,
})
assert.equal(noPrompt.issues.what, true)
assert.equal(noPrompt.firstInvalid, 'what')

// --- 5. cron 空或非法 → 归到 when ---
const noCron = resolveTaskSectionIssues({
  name: 'PUE 日报',
  prompt: '查询昨日 PUE',
  cronExpr: '',
  cronValid: true,
})
assert.equal(noCron.issues.when, true)
assert.equal(noCron.firstInvalid, 'when')

const badCron = resolveTaskSectionIssues({
  name: 'PUE 日报',
  prompt: '查询昨日 PUE',
  cronExpr: 'bad cron',
  cronValid: false,
})
assert.equal(badCron.issues.when, true)
assert.equal(badCron.firstInvalid, 'when')

// --- 6. 两组都错时按 what → when 顺序取第一个 ---
const both = resolveTaskSectionIssues({
  name: '',
  prompt: '',
  cronExpr: '',
  cronValid: false,
})
assert.equal(both.firstInvalid, 'what')

// --- 7. 摘要：正常情况三段都给出人话 ---
const normal = buildTaskSectionSummaries({
  name: 'PUE 日报',
  agent: '数据分析专家',
  firstSentence: '查询昨日 PUE 并生成日报',
  when: '每天 09:00',
  whenValid: true,
  nextRun: '11-19 09:00',
  channels: ['企业微信', '邮件'],
})
assert.equal(normal.what, '《PUE 日报》· 数据分析专家 · 查询昨日 PUE 并生成日报')
assert.equal(normal.when, '每天 09:00 · 下次 11-19 09:00')
assert.equal(normal.result, '企业微信 + 邮件')

// --- 8. 摘要：缺项显示「未填写」，无下次运行只给周期，无渠道说清不发 ---
const sparse = buildTaskSectionSummaries({
  name: '',
  agent: '',
  firstSentence: '',
  when: '每天 09:00',
  whenValid: true,
  nextRun: '',
  channels: [],
})
assert.equal(sparse.what, '《未填写》· 未填写 · 未填写')
assert.equal(sparse.when, '每天 09:00')
assert.equal(sparse.result, '不发送通知')

// --- 9. 摘要：cron 非法时明确说表达式有问题，不冒充周期 ---
const badWhen = buildTaskSectionSummaries({
  name: 'PUE 日报',
  agent: '数据分析专家',
  firstSentence: '查询昨日 PUE',
  when: '表达式格式不正确',
  whenValid: false,
  nextRun: '11-19 09:00',
  channels: ['站内'],
})
assert.equal(badWhen.when, '表达式格式不正确')

// --- 10. 摘要：超长提示词首句截断到 40 字并带省略号 ---
const longText = '甲'.repeat(60)
const clipped = buildTaskSectionSummaries({
  name: '任务',
  agent: '专家',
  firstSentence: longText,
  when: '每天 09:00',
  whenValid: true,
  nextRun: '',
  channels: [],
})
assert.equal(clipped.what, `《任务》· 专家 · ${'甲'.repeat(40)}…`)

console.log('task_form_sections 行为测试通过（摘要拼装与错误归组全部符合规格）')
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --experimental-strip-types --no-warnings tests/frontend/task_form_sections.mjs`
Expected: FAIL —— `Cannot find module '.../frontend/src/utils/taskFormSections.ts'`

- [ ] **Step 3: 写最小实现**

创建 `frontend/src/utils/taskFormSections.ts`：

```ts
/**
 * 定时任务表单三段的纯展示逻辑。
 *
 * 摘要与红点必须同源：两处都从同一份输入计算，否则会出现「红点亮着、摘要却说
 * 一切正常」。这里不碰 DOM、不读路由，方便 Node 直接跑行为断言。
 */

export type TaskSectionKey = 'what' | 'when' | 'result'

export const TASK_SECTION_IDS: Record<TaskSectionKey, string> = {
  what: 'task-section-what',
  when: 'task-section-when',
  result: 'task-section-result',
}

const SECTION_ORDER: TaskSectionKey[] = ['what', 'when', 'result']
const PLACEHOLDER = '未填写'

export interface TaskSectionIssueInput {
  name: string
  prompt: string
  cronExpr: string
  cronValid: boolean
}

export interface TaskSectionIssueResult {
  issues: Record<TaskSectionKey, boolean>
  firstInvalid: TaskSectionKey | null
}

/**
 * 组内是否有必须补全的项。`result` 目前没有必填（渠道默认「站内」，
 * 保存前还会剔除不可用渠道），恒为 false，保留键位是为了顺序与语义完整。
 */
export function resolveTaskSectionIssues(
  input: TaskSectionIssueInput,
): TaskSectionIssueResult {
  const issues: Record<TaskSectionKey, boolean> = {
    what: !String(input.name || '').trim() || !String(input.prompt || '').trim(),
    when: !String(input.cronExpr || '').trim() || !input.cronValid,
    result: false,
  }
  const firstInvalid = SECTION_ORDER.find((key) => issues[key]) ?? null
  return { issues, firstInvalid }
}

export interface TaskSectionSummaryInput {
  name: string
  agent: string
  firstSentence: string
  when: string
  whenValid: boolean
  nextRun: string
  channels: string[]
}

export type TaskSectionSummaries = Record<TaskSectionKey, string>

function clip(text: string, max: number): string {
  const value = String(text || '').trim()
  if (value.length <= max) return value
  return `${value.slice(0, max)}…`
}

/** 收起态的一行摘要；所有文本都截断，保证标题行不换行。 */
export function buildTaskSectionSummaries(
  input: TaskSectionSummaryInput,
): TaskSectionSummaries {
  const name = clip(input.name, 20) || PLACEHOLDER
  const agent = clip(input.agent, 12) || PLACEHOLDER
  const firstSentence = clip(input.firstSentence, 40) || PLACEHOLDER
  const when = input.whenValid
    ? String(input.when || '').trim() || PLACEHOLDER
    : '表达式格式不正确'
  const nextRun = String(input.nextRun || '').trim()
  const channels = (input.channels || []).filter(Boolean)
  return {
    what: `《${name}》· ${agent} · ${firstSentence}`,
    when: nextRun ? `${when} · 下次 ${nextRun}` : when,
    result: channels.length ? channels.join(' + ') : '不发送通知',
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `node --experimental-strip-types --no-warnings tests/frontend/task_form_sections.mjs`
Expected: PASS，输出 `task_form_sections 行为测试通过（摘要拼装与错误归组全部符合规格）`

- [ ] **Step 5: 提交**

```bash
git add frontend/src/utils/taskFormSections.ts tests/frontend/task_form_sections.mjs
git commit -m "feat(task): 新增表单三段摘要与错误归组的纯函数"
```

---

### Task 2: 折叠组件 `TaskFormSection.vue`

**Files:**
- Create: `frontend/src/components/task/TaskFormSection.vue`
- Test: `tests/frontend/test_task_form_section_contract.py`（本任务先建文件并只放组件相关用例，Task 3/4 再追加）

- [ ] **Step 1: 先写失败测试**

创建 `tests/frontend/test_task_form_section_contract.py`：

```python
"""定时任务表单三段折叠的契约。

锁定四件事：
1. 折叠组件是可访问的受控组件（button + aria-expanded/aria-controls + v-show）；
2. 收起后标题行给出摘要，组内有必填错误时给出红点，两者同源；
3. TaskCenter.vue 三组标题与内容都被组件包住，没有内容漏在折叠外；
4. 保存失败会自动展开第一个出错的组。

行为验证（摘要拼装与错误归组）见 tests/frontend/task_form_sections.mjs。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SECTION = "frontend/src/components/task/TaskFormSection.vue"

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _run_node(script: str) -> str:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_section_logic_behaviour_via_node():
    output = _run_node("tests/frontend/task_form_sections.mjs")
    assert "task_form_sections 行为测试通过" in output


def test_section_header_is_accessible_toggle_button():
    source = _source(SECTION)

    assert '<button' in source
    assert 'type="button"' in source
    assert ':aria-expanded="modelValue"' in source
    assert ':aria-controls="sectionId"' in source
    # 内容区必须保留 DOM（v-show 而不是 v-if），否则 aria-controls 指向空气
    assert 'v-show="modelValue"' in source
    assert ':id="sectionId"' in source


def test_section_props_cover_title_hint_summary_and_error():
    source = _source(SECTION)

    for token in ('sectionId', 'title', 'hint', 'summary', 'hasError', 'modelValue'):
        assert token in source, f"组件 props 缺少 {token}"
    assert "defineEmits" in source
    assert "'update:modelValue'" in source


def test_section_shows_summary_only_when_collapsed_and_error_replaces_it():
    source = _source(SECTION)

    # 收起才显示摘要行
    assert 'v-if="!modelValue && (hasError || summary)"' in source
    # 有错时摘要位置换成「需补全」，不并存
    assert "'需补全'" in source
    assert "'font-bold text-red-600'" in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/frontend/test_task_form_section_contract.py -q`
Expected: FAIL —— `FileNotFoundError`（组件尚未创建）

- [ ] **Step 3: 写组件实现**

创建 `frontend/src/components/task/TaskFormSection.vue`：

```vue
<template>
  <section>
    <!-- 整行是按钮：键盘 Enter/Space 原生可用，不必额外绑 keydown -->
    <button
      type="button"
      class="flex w-full items-center gap-2 border-b border-dashed border-gray-200 pb-1 text-left"
      :aria-expanded="modelValue"
      :aria-controls="sectionId"
      @click="emit('update:modelValue', !modelValue)"
    >
      <span class="text-[11px] font-black text-primary tracking-wide">{{ title }}</span>
      <span v-if="hint" class="text-[10px] text-gray-400">{{ hint }}</span>
      <span
        v-if="hasError"
        class="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full bg-red-500 text-[9px] font-black leading-none text-white"
        aria-hidden="true"
      >!</span>
      <span class="ml-auto flex items-center gap-1 text-[10px] font-black text-gray-400">
        <span aria-hidden="true">{{ modelValue ? '▾' : '▸' }}</span>
        <span class="sr-only">{{ modelValue ? '收起本节' : '展开本节' }}</span>
      </span>
    </button>

    <!-- 收起态：有错优先说「需补全」，否则给一行摘要 -->
    <p
      v-if="!modelValue && (hasError || summary)"
      class="truncate text-[11px] leading-relaxed"
      :class="hasError ? 'font-bold text-red-600' : 'text-gray-500'"
      :title="hasError ? '有必填项未完成，展开后补全' : summary"
    >
      {{ hasError ? '需补全' : summary }}
    </p>

    <!-- pt-5 与原 space-y-5 的组内间距对齐，展开态观感不变 -->
    <div v-show="modelValue" :id="sectionId" class="space-y-5 pt-5">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
defineProps<{
  /** 内容区 id，供 aria-controls 与保存失败时的滚动定位共用 */
  sectionId: string
  title: string
  hint?: string
  /** 收起态的一行摘要 */
  summary?: string
  /** 组内是否有必须补全的项 */
  hasError?: boolean
  /** 是否展开（受控） */
  modelValue: boolean
}>()

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/frontend/test_task_form_section_contract.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/task/TaskFormSection.vue tests/frontend/test_task_form_section_contract.py
git commit -m "feat(task): 新增表单章节折叠组件"
```

---

### Task 3: 三组接入 TaskCenter.vue

**Files:**
- Modify: `frontend/src/views/TaskCenter.vue`
- Test: `tests/frontend/test_task_form_section_contract.py`（追加用例）

- [ ] **Step 1: 先写失败测试（追加到契约文件末尾）**

```python
TASK_CENTER = "frontend/src/views/TaskCenter.vue"


def test_three_sections_are_wrapped_by_the_component():
    source = _source(TASK_CENTER)

    assert "from '@/components/task/TaskFormSection.vue'" in source
    # 三组各出现一次组件标签
    assert source.count("<TaskFormSection") >= 3
    for section_id in (
        "TASK_SECTION_IDS.what",
        "TASK_SECTION_IDS.when",
        "TASK_SECTION_IDS.result",
    ):
        assert section_id in source, f"缺少 {section_id} 绑定"
    # 摘要与红点都来自纯函数模块，不是模板里现拼
    assert "buildTaskSectionSummaries(" in source
    assert "resolveTaskSectionIssues(" in source
    assert "taskSectionSummary.what" in source
    assert "taskSectionIssues.issues.what" in source


def test_sections_default_to_expanded():
    source = _source(TASK_CENTER)

    assert "const showWhatSection = ref(true)" in source
    assert "const showWhenSection = ref(true)" in source
    assert "const showResultSection = ref(true)" in source


def test_nested_retry_fold_still_independent():
    source = _source(TASK_CENTER)

    # 内层「执行失败策略」仍是自己的开关，没被组级折叠取代
    assert "const showAdvancedRetry = ref(false)" in source
    assert 'v-show="showAdvancedRetry"' in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/frontend/test_task_form_section_contract.py -q`
Expected: FAIL —— 断言 `缺少 TaskFormSection 引入` 一类

- [ ] **Step 3: 加导入与状态**

在 `TaskCenter.vue` 的既有组件导入区（约 20 行 `import { formatCronRunTime, ... } from '@/utils/cronSchedule'` 附近）加：

```ts
import TaskFormSection from '@/components/task/TaskFormSection.vue'
import {
  buildTaskSectionSummaries,
  resolveTaskSectionIssues,
  TASK_SECTION_IDS,
  type TaskSectionKey,
} from '@/utils/taskFormSections'
```

在 `const showAdvancedRetry = ref(false)` 之前加：

```ts
/**
 * 三段折叠：默认全展开。用户明确要求「打开就能看全貌，想收再收」，
 * 因此不做持久化、不跨弹窗记忆。
 */
const showWhatSection = ref(true)
const showWhenSection = ref(true)
const showResultSection = ref(true)

const taskSectionSummary = computed(() =>
  buildTaskSectionSummaries({
    name: String(editingTask.value?.name || ''),
    agent: taskSummaryText.value.agent,
    firstSentence: taskSummaryText.value.what,
    when: taskSummaryText.value.when,
    whenValid: taskSummaryText.value.whenValid,
    nextRun: taskSummaryNext.value,
    channels: taskSummaryText.value.channels,
  }),
)

const taskSectionIssues = computed(() =>
  resolveTaskSectionIssues({
    name: String(editingTask.value?.name || ''),
    prompt: String(editingTask.value?.prompt || ''),
    cronExpr: String(editingTask.value?.cron_expr || ''),
    cronValid: cronExprValidation.value.valid,
  }),
)

/** 保存失败时展开出错的组，并把它滚到可见——收起草稿里报错等于让用户找不到。 */
const expandSection = (key: TaskSectionKey | null) => {
  if (!key) return
  if (key === 'what') showWhatSection.value = true
  else if (key === 'when') showWhenSection.value = true
  else showResultSection.value = true
  if (typeof document !== 'undefined') {
    document
      .getElementById(TASK_SECTION_IDS[key])
      ?.scrollIntoView({ block: 'nearest' })
  }
}
```

- [ ] **Step 4: 三处模板包裹**

**4a. 「做什么」组**：把标题块替换为组件开标签。

替换前：

```html
          <div class="flex items-baseline gap-2 border-b border-dashed border-gray-200 pb-1">
            <span class="text-[11px] font-black text-primary tracking-wide">做什么</span>
            <span class="text-[10px] text-gray-400">一句话说清目标与交付</span>
          </div>
```

替换后：

```html
          <TaskFormSection
            v-model="showWhatSection"
            :section-id="TASK_SECTION_IDS.what"
            title="做什么"
            hint="一句话说清目标与交付"
            :summary="taskSectionSummary.what"
            :has-error="taskSectionIssues.issues.what"
          >
```

然后在「什么时候跑」注释（`<!-- 方案 A：「什么时候跑」整组前移到「做什么」之后；字段与绑定未改动 -->`）之前插入闭合标签：

```html
          </TaskFormSection>
```

**4b. 「什么时候跑」组**：替换标题块为组件开标签。

替换前：

```html
          <div class="flex items-baseline gap-2 border-b border-dashed border-gray-200 pb-1">
            <span class="text-[11px] font-black text-primary tracking-wide">什么时候跑</span>
            <span class="text-[10px] text-gray-400">按人话选，随时能看到下次运行时间</span>
          </div>
```

替换后：

```html
          <TaskFormSection
            v-model="showWhenSection"
            :section-id="TASK_SECTION_IDS.when"
            title="什么时候跑"
            hint="按人话选，随时能看到下次运行时间"
            :summary="taskSectionSummary.when"
            :has-error="taskSectionIssues.issues.when"
          >
```

在本组末尾（`v-show="showAdvancedRetry"` 那个 div 的 `</div>` 之后、下一个组标题 `<div class="flex items-baseline gap-2 border-b border-dashed border-gray-200 pb-1">` 之前）插入：

```html
          </TaskFormSection>
```

**4c. 「结果发到哪」组**：替换标题块为组件开标签。

替换前：

```html
          <div class="flex items-baseline gap-2 border-b border-dashed border-gray-200 pb-1">
            <span class="text-[11px] font-black text-primary tracking-wide">结果发到哪</span>
            <span class="text-[10px] text-gray-400">站内消息始终可用</span>
          </div>
```

替换后：

```html
          <TaskFormSection
            v-model="showResultSection"
            :section-id="TASK_SECTION_IDS.result"
            title="结果发到哪"
            hint="站内消息始终可用"
            :summary="taskSectionSummary.result"
            :has-error="taskSectionIssues.issues.result"
          >
```

在本组末尾（左表单列闭合 `</div>` 之前、右栏 `<aside` 之前的那个 `</div>` 前面）插入：

```html
          </TaskFormSection>
```

包裹完成后，左列表单三个直接子元素就是三个 `<TaskFormSection>`，组内元素全部在插槽内。

- [ ] **Step 5: 跑测试与类型检查**

Run:
```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/frontend/test_task_form_section_contract.py -q
cd frontend && NODE_OPTIONS=--max-old-space-size=6144 npx vue-tsc -b --force 2>&1 | tail -5
```

Expected: pytest 7 passed；`vue-tsc` 报错数与改动前基线一致（改动文件 0 新增）

- [ ] **Step 6: 提交**

```bash
git add frontend/src/views/TaskCenter.vue tests/frontend/test_task_form_section_contract.py
git commit -m "feat(task): 新建/编辑弹窗三组章节支持折叠"
```

---

### Task 4: 保存校验前移 + 自动展开出错组

**Files:**
- Modify: `frontend/src/views/TaskCenter.vue`
- Test: `tests/frontend/test_task_form_section_contract.py`（追加用例）

- [ ] **Step 1: 先写失败测试（追加）**

```python
def test_save_expands_first_invalid_section_before_toasting():
    source = _source(TASK_CENTER)

    assert "expandSection('what')" in source
    assert "expandSection('when')" in source
    # 名称/提示词的前端校验必须早于 cron 校验，否则「做什么」组永远等后端 422
    assert source.index("expandSection('what')") < source.index("expandSection('when')")
    assert "请填写任务名称" in source
    assert "请填写「要做什么」" in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/frontend/test_task_form_section_contract.py -q`
Expected: FAIL —— `expandSection('what')` 不存在

- [ ] **Step 3: 改 `saveTask` 开头**

把 `saveTask` 现有开头：

```ts
const saveTask = async () => {
  try {
    // 非法表达式就地拦截：后端会返回 422，但原因埋在 detail 里，不如直接说明是哪一段错了
    const cronExpr = String(editingTask.value?.cron_expr || '').trim()
    if (!cronExpr) {
      showToast('请输入 Cron 表达式', 'error')
      return
    }
    if (!cronExprValidation.value.valid) {
      showToast(cronExprValidation.value.message, 'error')
      return
    }
```

替换为：

```ts
const saveTask = async () => {
  try {
    // 名称与提示词后端是必填（422）；前端前移校验，否则收起态下报错位置不可见
    const sectionIssues = resolveTaskSectionIssues({
      name: String(editingTask.value?.name || ''),
      prompt: String(editingTask.value?.prompt || ''),
      cronExpr: String(editingTask.value?.cron_expr || ''),
      cronValid: cronExprValidation.value.valid,
    })
    if (sectionIssues.issues.what) {
      expandSection('what')
      showToast(
        String(editingTask.value?.name || '').trim()
          ? '请填写「要做什么」'
          : '请填写任务名称',
        'error',
      )
      return
    }
    // 非法表达式就地拦截：后端会返回 422，但原因埋在 detail 里，不如直接说明是哪一段错了
    const cronExpr = String(editingTask.value?.cron_expr || '').trim()
    if (!cronExpr) {
      expandSection('when')
      showToast('请输入 Cron 表达式', 'error')
      return
    }
    if (!cronExprValidation.value.valid) {
      expandSection('when')
      showToast(cronExprValidation.value.message, 'error')
      return
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/frontend/test_task_form_section_contract.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/views/TaskCenter.vue tests/frontend/test_task_form_section_contract.py
git commit -m "feat(task): 保存失败时自动展开并定位到出错的章节"
```

---

### Task 5: 全量回归与清单登记

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 跑相关回归**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/frontend -q 2>&1 | tail -3
node tests/frontend/compile_vue_templates.cjs 2>&1 | tail -2
cd frontend && NODE_OPTIONS=--max-old-space-size=6144 npx vue-tsc -b --force 2>&1 | tail -3
```

Expected: `tests/frontend` 全绿（与改动前基线的 skipped 数一致）；模板编译 `0 problem`；`vue-tsc` 报错数 = 基线（155 左右，且本次两个改动文件 0 新增）

- [ ] **Step 2: 在 `tests/CHECKLIST.md` 顶部表格插入一条记录**

格式与既有行一致（标题（English）｜涉及文件｜说明｜状态｜日期），状态写「✅ 已实现；待 `./dev.sh` 后实机验收」。

- [ ] **Step 3: 提交**

```bash
git add tests/CHECKLIST.md
git commit -m "docs(checklist): 登记定时任务表单章节折叠"
```

---

## 自查（写完后逐条确认）

- 规格 §2 组件 → Task 2；§3 三组摘要 → Task 1 摘要函数 + Task 3 绑定；§4 错误与保存联动 → Task 1 归组 + Task 4；§5 边界（默认展开/不持久化/内层折叠独立）→ Task 3 用例；§6 测试 → 每个任务的 Step 1/Step 4 与 Task 5。
- 无 TBD/TODO；所有代码块可直接粘贴。
- 类型一致：`TaskSectionKey` / `TASK_SECTION_IDS` / `buildTaskSectionSummaries` / `resolveTaskSectionIssues` / `expandSection` 在 Task 1 定义、Task 3/4 使用，命名一致。
