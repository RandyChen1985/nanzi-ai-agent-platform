# 资源广场（前端）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在工作台落地「资源广场」tab、两个只读概览页、统一的资源访问审批与「我的申请」，并把两个资源入口卡接进工作台首屏。

**Architecture:** 广场拆成「面板 / 卡片 / 申请弹窗」三个组件挂进 `PersonalWorkbench.vue` 的第二个 tab；两个只读页走独立路由（可深链、可返回）；审批与「我的申请」在既有页面上改造，接口切到 `/api/portal/access-requests/*`。

**Tech Stack:** Vue 3 + TypeScript + Vite 7 + Tailwind CSS 3 + vue-tsc + pytest 前端契约测试。

**Spec:** [`docs/superpowers/specs/2026-10-27-resource-square-design.md`](../specs/2026-10-27-resource-square-design.md)
**后端计划（先执行）:** [`2026-10-27-resource-square-backend.md`](./2026-10-27-resource-square-backend.md)
**交互原型:** [`docs/mockups/2026-10-27-resource-square.html`](../../mockups/2026-10-27-resource-square.html)

**前置：** 后端计划已合入，`/api/portal/resource-square/*` 与 `/api/portal/access-requests/*` 可用。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `frontend/src/types/resourceSquare.ts` | 广场与申请的类型 |
| `frontend/src/api/resourceSquare.ts` | 接口封装（统一错误信息） |
| `frontend/src/components/resource-square/ResourceSquareCard.vue` | 统一卡片与四种状态 |
| `frontend/src/components/resource-square/ApplyAccessModal.vue` | 申请理由弹窗 |
| `frontend/src/components/resource-square/ResourceSquarePanel.vue` | 搜索、筛选、两段、分页、统计 |
| `frontend/src/views/ResourceSquareKnowledge.vue` | 知识库只读概览 |
| `frontend/src/views/ResourceSquareDataset.vue` | 数据集只读概览 |
| `frontend/src/views/PersonalWorkbench.vue` | tab 与 `?tab=square` |
| `frontend/src/views/Users.vue` | 审批弹窗改造 |
| `frontend/src/views/PersonalCenter.vue` | 我的申请改造 |
| `frontend/src/components/PortalNotificationBell.vue` | 新通知类型 |
| `frontend/src/components/workbench/WorkbenchPersonalResources.vue` | 两个新资源卡 |
| `frontend/src/constants/personalResources.ts` | 资源定义同步 |
| `frontend/src/views/SystemConfig.vue` | 数据集目录开关 |
| `frontend/src/router/index.ts` | 两条只读页路由 |

---

### Task 1: 类型与接口封装

**Files:**
- Create: `frontend/src/types/resourceSquare.ts`
- Create: `frontend/src/api/resourceSquare.ts`
- Test: `tests/frontend/test_resource_square_api_contract.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/frontend/test_resource_square_api_contract.py
"""资源广场接口契约：路径、参数名、状态枚举与后端保持一致。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "frontend/src/api/resourceSquare.ts"
TYPES = ROOT / "frontend/src/types/resourceSquare.ts"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_square_endpoints_match_backend():
    api = _read(API)
    assert "/api/portal/resource-square/knowledge" in api
    assert "/api/portal/resource-square/datasets" in api
    assert "/api/portal/access-requests" in api


def test_request_status_enum_is_closed_set():
    types = _read(TYPES)
    for value in ("pending", "approved", "rejected", "cancelled"):
        assert value in types
    assert "has_access" in types and "request_status" in types


def test_apply_modal_requires_resource_type():
    types = _read(TYPES)
    assert 'resource_type: "dataset" | "metadata"' in types
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_resource_square_api_contract.py -q`
Expected: FAIL（文件不存在）

- [ ] **Step 3: 写类型**

```ts
// frontend/src/types/resourceSquare.ts
export type ResourceAccessStatus = "pending" | "approved" | "rejected" | "cancelled"
export type SquareTab = "all" | "kb" | "ds"
export type SquareStateFilter = "all" | "apply" | "pending" | "has"

export interface SquareKnowledgeItem {
  dataset_id: string
  name: string
  description: string | null
  visibility: "public" | "team"
  owner: string | null
  owner_dept_id: number | null
  tags: string[]
  doc_count: number | null
  chunk_count: number | null
  updated_at: string | null
  has_access: boolean
  request_status: ResourceAccessStatus | null
  request_id: number | null
}

export interface SquareDatasetItem {
  id: number
  name: string
  display_name: string
  description: string | null
  data_source: string | null
  tags: string[]
  quality_score: number | null
  table_count: number
  metric_count: number
  updated_at: string | null
  has_access: boolean
  request_status: ResourceAccessStatus | null
  request_id: number | null
}

export interface SquareSummary {
  apply: number
  pending: number
  has: number
}

export interface SquarePage<T> {
  items: T[]
  total: number
  page: number
  size: number
  summary: SquareSummary
}

export interface SquareApplyPayload {
  resource_type: "dataset" | "metadata"
  resource_id: string
  reason?: string
}

export interface AccessRequestItem {
  id: number
  resource_type: "dataset" | "metadata"
  resource_id: string
  resource_name: string | null
  applicant_user_id: number
  applicant_user_name: string | null
  reason: string | null
  status: ResourceAccessStatus
  reviewer_user_name: string | null
  review_comment: string | null
  created_at: string | null
  reviewed_at: string | null
  resource_exists?: boolean
  resource_enabled?: boolean
}

export interface KnowledgeReadonly {
  dataset_id: string
  name: string
  description: string | null
  visibility: string
  owner: string | null
  tags: string[]
  updated_at: string | null
  documents: Array<{
    id: string
    name: string
    file_type: string | null
    size: number | null
    chunk_count: number | null
    run_status: string | null
    updated_at: string | null
  }>
}

export interface DatasetReadonly {
  id: number
  name: string
  display_name: string
  description: string | null
  data_source: string | null
  tags: string[]
  quality_score: number | null
  updated_at: string | null
  relationship_count: number
  tables: Array<{ physical_name: string; term: string; field_count: number; description: string | null }>
  metrics: Array<{ name: string; display_name: string; description: string | null; unit: string | null }>
}
```

- [ ] **Step 4: 写接口封装**

```ts
// frontend/src/api/resourceSquare.ts
import axios from "axios"

import type {
  AccessRequestItem,
  DatasetReadonly,
  KnowledgeReadonly,
  SquareApplyPayload,
  SquareDatasetItem,
  SquareKnowledgeItem,
  SquarePage,
} from "@/types/resourceSquare"

const BASE = "/api/portal"

function errorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

export async function fetchSquareKnowledge(params: {
  keyword?: string
  visibility?: "all" | "public" | "team"
  status?: string
  page?: number
  size?: number
}): Promise<SquarePage<SquareKnowledgeItem>> {
  try {
    const resp = await axios.get(`${BASE}/resource-square/knowledge`, { params })
    return resp.data.data
  } catch (error) {
    throw new Error(errorMessage(error, "读取知识库列表失败"))
  }
}

export async function fetchSquareDatasets(params: {
  keyword?: string
  status?: string
  page?: number
  size?: number
}): Promise<SquarePage<SquareDatasetItem> & { enabled: boolean }> {
  try {
    const resp = await axios.get(`${BASE}/resource-square/datasets`, { params })
    return resp.data.data
  } catch (error) {
    throw new Error(errorMessage(error, "读取数据集列表失败"))
  }
}

export async function applyResourceAccess(payload: SquareApplyPayload): Promise<{ id: number; status: string }> {
  try {
    const resp = await axios.post(`${BASE}/access-requests`, payload)
    return resp.data.data
  } catch (error) {
    throw new Error(errorMessage(error, "提交申请失败"))
  }
}

export async function cancelAccessRequest(requestId: number): Promise<void> {
  await axios.post(`${BASE}/access-requests/${requestId}/cancel`)
}

export async function fetchMyAccessRequests(): Promise<AccessRequestItem[]> {
  const resp = await axios.get(`${BASE}/access-requests/mine`)
  return resp.data.data.items || []
}

export async function fetchPendingAccessRequests(resourceType?: string): Promise<AccessRequestItem[]> {
  const resp = await axios.get(`${BASE}/access-requests/pending`, {
    params: resourceType && resourceType !== "all" ? { resource_type: resourceType } : {},
  })
  return resp.data.data.items || []
}

export async function fetchReviewedAccessRequests(resourceType?: string): Promise<AccessRequestItem[]> {
  const resp = await axios.get(`${BASE}/access-requests/reviews`, {
    params: resourceType && resourceType !== "all" ? { resource_type: resourceType } : {},
  })
  return resp.data.data.items || []
}

export async function decideAccessRequest(
  requestId: number,
  action: "approve" | "reject",
  comment?: string,
): Promise<void> {
  await axios.post(`${BASE}/access-requests/${requestId}/${action}`, { comment: comment || null })
}

export async function fetchKnowledgeReadonly(datasetId: string, page = 1, size = 100): Promise<KnowledgeReadonly> {
  const resp = await axios.get(`${BASE}/resource-square/knowledge/${encodeURIComponent(datasetId)}`, {
    params: { page, size },
  })
  return resp.data.data
}

export async function fetchDatasetReadonly(datasetId: number): Promise<DatasetReadonly> {
  const resp = await axios.get(`${BASE}/resource-square/datasets/${datasetId}`)
  return resp.data.data
}
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_resource_square_api_contract.py -q`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/resourceSquare.ts frontend/src/api/resourceSquare.ts tests/frontend/test_resource_square_api_contract.py
git commit -m "feat(web): 资源广场类型与接口封装"
```

---

### Task 2: 卡片与申请弹窗

**Files:**
- Create: `frontend/src/components/resource-square/ResourceSquareCard.vue`
- Create: `frontend/src/components/resource-square/ApplyAccessModal.vue`
- Test: `tests/frontend/test_resource_square_card_states.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/frontend/test_resource_square_card_states.py
"""卡片四种状态：可申请 / 审批中 / 已授权 / 已驳回，按钮与徽标必须互斥。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CARD = ROOT / "frontend/src/components/resource-square/ResourceSquareCard.vue"
MODAL = ROOT / "frontend/src/components/resource-square/ApplyAccessModal.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_card_has_four_mutually_exclusive_states():
    card = _read(CARD)
    assert "已有权限" in card
    assert "审批中" in card
    assert "已驳回" in card
    assert "重新申请" in card
    for state in ("has", "pending", "rejected"):
        assert f"state === '{state}'" in card


def test_card_hides_apply_button_when_granted():
    card = _read(CARD)
    assert 'v-if="state === \'apply\'"' in card or "v-if=\"state === 'apply'\"" in card


def test_apply_modal_collects_optional_reason_and_warns_where_to_track():
    modal = _read(MODAL)
    assert "申请理由" in modal
    assert "个人中心" in modal
    assert "正在等待审批" in modal
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_resource_square_card_states.py -q`
Expected: FAIL

- [ ] **Step 3: 实现卡片**

```vue
<!-- frontend/src/components/resource-square/ResourceSquareCard.vue -->
<script setup lang="ts">
import { computed } from "vue"

const props = defineProps<{
  kind: "kb" | "ds"
  name: string
  subtitle?: string | null
  description?: string | null
  visibility?: "public" | "team" | null
  owner?: string | null
  badges?: string[]
  metrics?: Array<{ label: string; value: string }>
  hasAccess: boolean
  requestStatus?: "pending" | "approved" | "rejected" | "cancelled" | null
  requestId?: number | null
}>()

const emit = defineEmits<{
  (event: "apply"): void
  (event: "open"): void
}>()

type CardState = "apply" | "has" | "pending" | "rejected"

const state = computed<CardState>(() => {
  if (props.hasAccess) return "has"
  if (props.requestStatus === "pending") return "pending"
  if (props.requestStatus === "rejected") return "rejected"
  return "apply"
})

const visibilityLabel = computed(() =>
  props.visibility === "public" ? "公共级" : props.visibility === "team" ? "部门级" : "",
)

function onTitleClick() {
  if (state.value === "has") emit("open")
}
</script>

<template>
  <article
    class="flex flex-col rounded-2xl border bg-white p-4 shadow-xs transition-all dark:bg-gray-800/80"
    :class="state === 'has'
      ? 'border-emerald-200 dark:border-emerald-700/50'
      : 'border-gray-100 hover:border-primary/40 hover:shadow-md dark:border-gray-700/70'"
  >
    <div class="flex items-start gap-3">
      <span
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
        :class="kind === 'kb' ? 'bg-blue-50 text-blue-600 dark:bg-blue-900/30' : 'bg-indigo-50 text-indigo-600 dark:bg-indigo-900/30'"
      >
        <svg v-if="kind === 'kb'" class="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 6.3v13m0-13C10.8 5.5 9.2 5 7.5 5S4.2 5.5 3 6.3v13C4.2 18.5 5.8 18 7.5 18s3.3.5 4.5 1.3m0-13C13.2 5.5 14.8 5 16.5 5s3.3.5 4.5 1.3v13c-1.2-.8-2.8-1.3-4.5-1.3s-3.3.5-4.5 1.3" />
        </svg>
        <svg v-else class="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9">
          <ellipse cx="12" cy="6" rx="8" ry="3" />
          <path stroke-linecap="round" d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
        </svg>
      </span>

      <div class="min-w-0 flex-1">
        <h4 class="truncate text-[13.5px] font-semibold text-gray-900 dark:text-gray-100">
          <button type="button" class="text-left" @click="onTitleClick">{{ name }}</button>
          <span v-if="subtitle" class="ml-1 font-normal text-gray-400">{{ subtitle }}</span>
        </h4>
        <div class="mt-1.5 flex flex-wrap gap-1.5">
          <span v-if="visibilityLabel" class="rounded-md bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-600 dark:bg-blue-900/30">
            {{ visibilityLabel }}
          </span>
          <span v-for="badge in badges || []" :key="badge" class="rounded-md bg-gray-50 px-1.5 py-0.5 text-[10px] font-medium text-gray-500 dark:bg-gray-700/60">
            {{ badge }}
          </span>
        </div>
      </div>
    </div>

    <p class="mt-2.5 line-clamp-2 min-h-[37px] text-xs leading-5 text-gray-500 dark:text-gray-400">
      {{ description || "暂无描述" }}
    </p>

    <div class="mt-2.5 flex flex-wrap gap-3 border-t border-dashed border-gray-100 pt-2.5 text-[11px] text-gray-400 dark:border-gray-700/60">
      <span v-for="metric in metrics || []" :key="metric.label">{{ metric.value }}</span>
    </div>

    <div class="mt-2.5 flex items-center justify-between gap-2">
      <span class="truncate text-[11px] text-gray-400">{{ owner || "" }}</span>

      <span v-if="state === 'has'" class="rounded-lg bg-emerald-50 px-2 py-1 text-[11.5px] font-semibold text-emerald-600 dark:bg-emerald-900/20">
        已有权限
      </span>
      <span v-else-if="state === 'pending'" class="rounded-lg bg-amber-50 px-2 py-1 text-[11.5px] font-semibold text-amber-600 dark:bg-amber-900/20">
        审批中
      </span>
      <span v-else-if="state === 'rejected'" class="flex items-center gap-1.5">
        <span class="rounded-lg bg-red-50 px-2 py-1 text-[11.5px] font-semibold text-red-500 dark:bg-red-900/20">已驳回</span>
        <button type="button" class="rounded-lg border border-gray-200 px-2 py-1 text-[11.5px] font-semibold text-gray-600 hover:border-primary hover:text-primary dark:border-gray-600" @click="emit('apply')">
          重新申请
        </button>
      </span>
      <button
        v-if="state === 'apply'"
        type="button"
        class="rounded-lg bg-primary px-2.5 py-1.5 text-[11.5px] font-semibold text-white hover:bg-primary-dark"
        @click="emit('apply')"
      >
        申请访问
      </button>
    </div>
  </article>
</template>
```

- [ ] **Step 4: 实现申请弹窗**

```vue
<!-- frontend/src/components/resource-square/ApplyAccessModal.vue -->
<script setup lang="ts">
import { ref, watch } from "vue"

import { applyResourceAccess } from "@/api/resourceSquare"

const props = defineProps<{
  visible: boolean
  resourceType: "dataset" | "metadata"
  resourceId: string
  resourceName: string
  resourceMeta?: string
}>()

const emit = defineEmits<{
  (event: "close"): void
  (event: "submitted", payload: { id: number }): void
}>()

const reason = ref("")
const submitting = ref(false)
const error = ref("")

const quickReasons = ["季度对账需要核对历史口径", "搭建经营分析看板需要该数据", "新接手业务，需要补齐背景资料"]

watch(() => props.visible, (open) => {
  if (open) {
    reason.value = ""
    error.value = ""
  }
})

async function submit() {
  if (submitting.value) return
  submitting.value = true
  error.value = ""
  try {
    const result = await applyResourceAccess({
      resource_type: props.resourceType,
      resource_id: props.resourceId,
      reason: reason.value.trim() || undefined,
    })
    emit("submitted", { id: result.id })
    emit("close")
  } catch (err) {
    error.value = err instanceof Error ? err.message : "提交申请失败"
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div v-if="visible" class="fixed inset-0 z-[80] flex items-start justify-center overflow-auto bg-slate-900/50 p-4 pt-16" @click.self="emit('close')">
    <div class="w-full max-w-lg rounded-2xl border border-gray-100 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-800">
      <div class="flex items-start justify-between gap-3 px-5 pt-5">
        <div>
          <h3 class="text-base font-semibold text-gray-900 dark:text-gray-100">申请访问</h3>
          <p class="mt-1 text-xs text-gray-500">提交后由管理员审批，通过时自动给你加上访问授权。</p>
        </div>
        <button type="button" class="text-xs font-medium text-gray-400 hover:text-gray-600" @click="emit('close')">关闭</button>
      </div>

      <div class="px-5 pt-4">
        <div class="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2.5 dark:border-gray-700 dark:bg-gray-900/40">
          <p class="text-sm font-semibold text-gray-900 dark:text-gray-100">{{ resourceName }}</p>
          <p class="mt-0.5 text-xs text-gray-500">{{ resourceMeta || (resourceType === "metadata" ? "数据集 · 只读权限" : "知识库 · 申请通过后可检索") }}</p>
        </div>

        <label class="mt-4 block text-xs font-semibold text-gray-700 dark:text-gray-300">申请理由（可选，写清楚更容易通过）</label>
        <textarea
          v-model="reason"
          rows="3"
          class="mt-1.5 w-full resize-y rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 dark:border-gray-600 dark:bg-gray-900"
          placeholder="例如：季度对账需要查 2024 年的报销制度"
        />
        <div class="mt-2 flex flex-wrap gap-1.5">
          <button
            v-for="item in quickReasons"
            :key="item"
            type="button"
            class="rounded-full border border-gray-200 px-2 py-0.5 text-[11px] text-gray-500 hover:border-primary hover:text-primary dark:border-gray-600"
            @click="reason = item"
          >
            {{ item }}
          </button>
        </div>
        <p class="mt-2 text-[11px] leading-5 text-gray-400">
          提交后管理员会在「用户管理 → 待审核」里处理，你可以在个人中心「我的申请」查看进度；同一条申请正在等待审批时不能重复提交。
        </p>
        <p v-if="error" class="mt-2 text-xs text-red-500">{{ error }}</p>
      </div>

      <div class="mt-4 flex justify-end gap-2 border-t border-gray-100 px-5 py-3 dark:border-gray-700">
        <button type="button" class="rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 dark:border-gray-600" @click="emit('close')">取消</button>
        <button
          type="button"
          class="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-dark disabled:opacity-60"
          :disabled="submitting"
          @click="submit"
        >
          {{ submitting ? "提交中…" : "提交申请" }}
        </button>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_resource_square_card_states.py -q`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/resource-square/ResourceSquareCard.vue frontend/src/components/resource-square/ApplyAccessModal.vue tests/frontend/test_resource_square_card_states.py
git commit -m "feat(web): 资源广场卡片与申请弹窗"
```

---

### Task 3: 广场面板

**Files:**
- Create: `frontend/src/components/resource-square/ResourceSquarePanel.vue`
- Test: `tests/frontend/test_resource_square_panel_contract.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/frontend/test_resource_square_panel_contract.py
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "frontend/src/components/resource-square/ResourceSquarePanel.vue"


def test_panel_renders_two_sections_and_summary():
    panel = PANEL.read_text(encoding="utf-8")
    assert "知识库" in panel and "数据集" in panel
    assert "可申请" in panel and "审批中" in panel and "已有权限" in panel


def test_panel_loads_sections_independently():
    panel = PANEL.read_text(encoding="utf-8")
    # 两段各自 try/catch，一段失败不拖垮另一段
    assert "kbError" in panel and "dsError" in panel
    assert "fetchSquareKnowledge" in panel and "fetchSquareDatasets" in panel


def test_panel_warns_when_knowledge_data_is_stale():
    panel = PANEL.read_text(encoding="utf-8")
    assert "kbStale" in panel
    assert "数据来自缓存" in panel


def test_panel_supports_section_query():
    panel = PANEL.read_text(encoding="utf-8")
    assert "section" in panel
    assert "scrollIntoView" in panel
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_resource_square_panel_contract.py -q`
Expected: FAIL

- [ ] **Step 3: 实现面板**

```vue
<!-- frontend/src/components/resource-square/ResourceSquarePanel.vue -->
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"

import {
  fetchSquareDatasets,
  fetchSquareKnowledge,
} from "@/api/resourceSquare"
import type {
  SquareDatasetItem,
  SquareKnowledgeItem,
  SquareStateFilter,
  SquareSummary,
  SquareTab,
} from "@/types/resourceSquare"
import ApplyAccessModal from "@/components/resource-square/ApplyAccessModal.vue"
import ResourceSquareCard from "@/components/resource-square/ResourceSquareCard.vue"

const route = useRoute()
const router = useRouter()

const keyword = ref("")
const tab = ref<SquareTab>("all")
const stateFilter = ref<SquareStateFilter>("all")
const visibility = ref<"all" | "public" | "team">("all")
const page = ref(1)
const size = 12

const kbItems = ref<SquareKnowledgeItem[]>([])
const dsItems = ref<SquareDatasetItem[]>([])
const kbTotal = ref(0)
const dsTotal = ref(0)
const kbLoading = ref(false)
const dsLoading = ref(false)
const kbError = ref("")
const dsError = ref("")
const datasetsEnabled = ref(true)
const kbStale = ref(false)
const kbSummary = ref<SquareSummary>({ apply: 0, pending: 0, has: 0 })
const dsSummary = ref<SquareSummary>({ apply: 0, pending: 0, has: 0 })

const summary = computed<SquareSummary>(() => ({
  apply: kbSummary.value.apply + dsSummary.value.apply,
  pending: kbSummary.value.pending + dsSummary.value.pending,
  has: kbSummary.value.has + dsSummary.value.has,
}))

const applyTarget = ref<{
  resourceType: "dataset" | "metadata"
  resourceId: string
  resourceName: string
  resourceMeta?: string
} | null>(null)

const showKb = computed(() => tab.value !== "ds")
const showDs = computed(() => tab.value !== "kb")

async function loadKnowledge() {
  kbLoading.value = true
  kbError.value = ""
  try {
    const data = await fetchSquareKnowledge({
      keyword: keyword.value.trim() || undefined,
      visibility: visibility.value,
      status: stateFilter.value,
      page: page.value,
      size,
    })
    kbItems.value = data.items
    kbTotal.value = data.total
    kbStale.value = Boolean((data as { stale?: boolean }).stale)
    kbSummary.value = data.summary
  } catch (error) {
    kbError.value = error instanceof Error ? error.message : "读取知识库失败"
  } finally {
    kbLoading.value = false
  }
}

async function loadDatasets() {
  dsLoading.value = true
  dsError.value = ""
  try {
    const data = await fetchSquareDatasets({
      keyword: keyword.value.trim() || undefined,
      status: stateFilter.value,
      page: page.value,
      size,
    })
    dsItems.value = data.items
    dsTotal.value = data.total
    datasetsEnabled.value = data.enabled
    dsSummary.value = data.summary
  } catch (error) {
    dsError.value = error instanceof Error ? error.message : "读取数据集失败"
  } finally {
    dsLoading.value = false
  }
}

async function loadAll() {
  page.value = 1
  await Promise.all([loadKnowledge(), loadDatasets()])
}

async function reload() {
  await Promise.all([loadKnowledge(), loadDatasets()])
}

watch([keyword, tab, stateFilter, visibility], () => {
  void loadAll()
})

function applyKb(item: SquareKnowledgeItem) {
  applyTarget.value = {
    resourceType: "dataset",
    resourceId: item.dataset_id,
    resourceName: item.name,
    resourceMeta: `知识库 · ${item.visibility === "public" ? "公共级" : "部门级"} · ${item.doc_count ?? "—"} 个文档`,
  }
}

function applyDs(item: SquareDatasetItem) {
  applyTarget.value = {
    resourceType: "metadata",
    resourceId: String(item.id),
    resourceName: `${item.name}（${item.display_name}）`,
    resourceMeta: `数据集 · ${item.table_count} 张表 / ${item.metric_count} 个指标`,
  }
}

function openKb(item: SquareKnowledgeItem) {
  router.push({ name: "ResourceSquareKnowledge", params: { datasetId: item.dataset_id } })
}

function openDs(item: SquareDatasetItem) {
  router.push({ name: "ResourceSquareDataset", params: { id: String(item.id) } })
}

async function onSubmitted() {
  await reload()
}

async function focusSection() {
  const section = String(route.query.section || "")
  if (!section) return
  await nextTick()
  const el = document.getElementById(section === "datasets" ? "square-datasets" : "square-knowledge")
  el?.scrollIntoView({ behavior: "smooth", block: "start" })
}

onMounted(async () => {
  await loadAll()
  await focusSection()
})
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="text-base font-semibold text-gray-900 dark:text-gray-100">资源广场</h2>
        <p class="mt-0.5 text-xs text-gray-500">公共级与部门级知识库、平台数据集，找到需要的资源直接申请。</p>
      </div>
      <div class="flex flex-wrap gap-2 text-[11.5px] font-semibold">
        <span class="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-800">可申请 <b class="text-primary">{{ summary.apply }}</b></span>
        <span class="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-800">审批中 <b class="text-amber-600">{{ summary.pending }}</b></span>
        <span class="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-800">已有权限 <b class="text-emerald-600">{{ summary.has }}</b></span>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-2 rounded-xl border border-gray-100 bg-white p-3 shadow-xs dark:border-gray-700 dark:bg-gray-800">
      <input
        v-model="keyword"
        type="search"
        class="min-w-[200px] flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 dark:border-gray-600 dark:bg-gray-900"
        placeholder="搜索名称 / 描述 / 归属部门 / 标签"
      />
      <div class="flex rounded-lg bg-gray-100 p-0.5 dark:bg-gray-700/60">
        <button
          v-for="item in ([{ key: 'all', label: '全部' }, { key: 'kb', label: '知识库' }, { key: 'ds', label: '数据集' }] as const)"
          :key="item.key"
          type="button"
          class="rounded-md px-2.5 py-1 text-xs font-semibold"
          :class="tab === item.key ? 'bg-white text-primary shadow-xs dark:bg-gray-800' : 'text-gray-500'"
          @click="tab = item.key"
        >
          {{ item.label }}
        </button>
      </div>
      <select v-model="visibility" class="rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-900">
        <option value="all">全部范围</option>
        <option value="public">公共级</option>
        <option value="team">部门级</option>
      </select>
      <div class="flex rounded-lg bg-gray-100 p-0.5 dark:bg-gray-700/60">
        <button
          v-for="item in ([{ key: 'all', label: '全部状态' }, { key: 'apply', label: '可申请' }, { key: 'pending', label: '审批中' }, { key: 'has', label: '已授权' }] as const)"
          :key="item.key"
          type="button"
          class="rounded-md px-2.5 py-1 text-xs font-semibold"
          :class="stateFilter === item.key ? 'bg-white text-primary shadow-xs dark:bg-gray-800' : 'text-gray-500'"
          @click="stateFilter = item.key"
        >
          {{ item.label }}
        </button>
      </div>
    </div>

    <div v-if="showKb" id="square-knowledge" class="space-y-2.5">
      <div class="flex items-center gap-2">
        <h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">知识库</h3>
        <span class="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-500 dark:bg-gray-700">{{ kbItems.length }}</span>
        <span class="ml-auto text-[11px] text-gray-400">公共级与部门级；个人私有库不出现，也不接受申请</span>
      </div>
      <div v-if="kbStale && !kbError" class="rounded-xl border border-blue-100 bg-blue-50/70 px-3 py-2 text-[11px] text-blue-700">
        知识库数据来自缓存，规模与更新时间可能不是最新。
      </div>
      <div v-if="kbError" class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
        {{ kbError }}
        <button type="button" class="ml-2 font-semibold underline" @click="loadKnowledge">重试</button>
      </div>
      <div v-else-if="kbLoading" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <div v-for="i in 6" :key="i" class="h-40 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-800/60" />
      </div>
      <div v-else-if="kbItems.length" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <ResourceSquareCard
          v-for="item in kbItems"
          :key="item.dataset_id"
          kind="kb"
          :name="item.name"
          :description="item.description"
          :visibility="item.visibility"
          :owner="item.owner"
          :badges="item.tags"
          :metrics="[
            { label: 'docs', value: `${item.doc_count ?? '—'} 个文档` },
            { label: 'chunks', value: `${item.chunk_count ?? '—'} 切片` },
          ]"
          :has-access="item.has_access"
          :request-status="item.request_status"
          :request-id="item.request_id"
          @apply="applyKb(item)"
          @open="openKb(item)"
        />
      </div>
      <div v-else class="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-xs text-gray-400">
        没有匹配的知识库，试试换个关键词或清空筛选。
      </div>
    </div>

    <div v-if="showDs" id="square-datasets" class="space-y-2.5">
      <div class="flex items-center gap-2">
        <h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">数据集</h3>
        <span class="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-500 dark:bg-gray-700">{{ dsItems.length }}</span>
        <span class="ml-auto text-[11px] text-gray-400">全部启用数据集；审批通过后可进入只读概览</span>
      </div>
      <div v-if="!datasetsEnabled" class="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-xs text-gray-400">
        管理员暂未开放数据集目录。
      </div>
      <div v-else-if="dsError" class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
        {{ dsError }}
        <button type="button" class="ml-2 font-semibold underline" @click="loadDatasets">重试</button>
      </div>
      <div v-else-if="dsLoading" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <div v-for="i in 6" :key="i" class="h-40 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-800/60" />
      </div>
      <div v-else-if="dsItems.length" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <ResourceSquareCard
          v-for="item in dsItems"
          :key="item.id"
          kind="ds"
          :name="item.name"
          :subtitle="item.display_name"
          :description="item.description"
          :owner="item.data_source"
          :badges="item.tags"
          :metrics="[
            { label: 'tables', value: `${item.table_count} 张表` },
            { label: 'metrics', value: `${item.metric_count} 个指标` },
            { label: 'quality', value: item.quality_score == null ? '—' : `质量 ${item.quality_score}` },
          ]"
          :has-access="item.has_access"
          :request-status="item.request_status"
          :request-id="item.request_id"
          @apply="applyDs(item)"
          @open="openDs(item)"
        />
      </div>
      <div v-else class="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-xs text-gray-400">
        没有匹配的数据集。找不到需要的？联系管理员。
      </div>
    </div>

    <ApplyAccessModal
      v-if="applyTarget"
      :visible="Boolean(applyTarget)"
      :resource-type="applyTarget.resourceType"
      :resource-id="applyTarget.resourceId"
      :resource-name="applyTarget.resourceName"
      :resource-meta="applyTarget.resourceMeta"
      @close="applyTarget = null"
      @submitted="onSubmitted"
    />
  </section>
</template>
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_resource_square_panel_contract.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/resource-square/ResourceSquarePanel.vue tests/frontend/test_resource_square_panel_contract.py
git commit -m "feat(web): 资源广场面板"
```

---

### Task 4: 工作台 tab 接入

**Files:**
- Modify: `frontend/src/views/PersonalWorkbench.vue`
- Modify: `frontend/src/router/index.ts`（两条只读路由）
- Create: `frontend/src/views/ResourceSquareKnowledge.vue`
- Create: `frontend/src/views/ResourceSquareDataset.vue`
- Test: `tests/frontend/test_workbench_square_tab_contract.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/frontend/test_workbench_square_tab_contract.py
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "frontend/src/views/PersonalWorkbench.vue"
ROUTER = ROOT / "frontend/src/router/index.ts"


def test_workbench_has_square_tab_bound_to_query():
    page = WORKBENCH.read_text(encoding="utf-8")
    assert "ResourceSquarePanel" in page
    assert "activeTab" in page
    assert "tab" in page and "square" in page


def test_router_registers_two_readonly_routes():
    router = ROUTER.read_text(encoding="utf-8")
    assert "ResourceSquareKnowledge" in router
    assert "ResourceSquareDataset" in router
    assert "resource-square/knowledge/:datasetId" in router
    assert "resource-square/datasets/:id" in router
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_workbench_square_tab_contract.py -q`
Expected: FAIL

- [ ] **Step 3: 改工作台**

`PersonalWorkbench.vue` 的 `<script setup>` 顶部加：

```ts
import ResourceSquarePanel from "@/components/resource-square/ResourceSquarePanel.vue"
import { useRoute, useRouter } from "vue-router"

const route = useRoute()
const router = useRouter()
const activeTab = ref<"workbench" | "square">(route.query.tab === "square" ? "square" : "workbench")

function switchTab(next: "workbench" | "square") {
  activeTab.value = next
  router.replace({ query: { ...route.query, tab: next === "square" ? "square" : undefined } })
}
```

模板：在页面标题与 `WorkbenchPersonalResources` 之间插入 tab 栏；把原有主体内容包进 `v-if="activeTab === 'workbench'"`，并追加：

```vue
<nav class="flex gap-6 border-b border-gray-100 dark:border-gray-800">
  <button
    type="button"
    class="-mb-px border-b-2 pb-2.5 text-sm font-medium"
    :class="activeTab === 'workbench' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'"
    @click="switchTab('workbench')"
  >
    工作台
  </button>
  <button
    type="button"
    class="-mb-px border-b-2 pb-2.5 text-sm font-medium"
    :class="activeTab === 'square' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'"
    @click="switchTab('square')"
  >
    资源广场
  </button>
</nav>

<ResourceSquarePanel v-if="activeTab === 'square'" />
```

- [ ] **Step 4: 两个只读页**

`ResourceSquareKnowledge.vue`：

```vue
<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"

import { fetchKnowledgeReadonly } from "@/api/resourceSquare"
import type { KnowledgeReadonly } from "@/types/resourceSquare"

const route = useRoute()
const router = useRouter()
const datasetId = String(route.params.datasetId || "")
const data = ref<KnowledgeReadonly | null>(null)
const loading = ref(true)
const error = ref("")
const forbidden = ref(false)

onMounted(async () => {
  try {
    data.value = await fetchKnowledgeReadonly(datasetId)
  } catch (err) {
    const status = (err as { response?: { status?: number } })?.response?.status
    forbidden.value = status === 403
    error.value = err instanceof Error ? err.message : "读取知识库失败"
  } finally {
    loading.value = false
  }
})

function backToSquare() {
  router.push({ path: "/dashboard/workbench", query: { tab: "square" } })
}
</script>

<template>
  <div class="space-y-4">
    <div v-if="loading" class="h-40 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-800/60" />
    <div v-else-if="forbidden" class="rounded-2xl border border-dashed border-gray-200 px-6 py-12 text-center">
      <p class="text-sm text-gray-600 dark:text-gray-300">你还没有这个知识库的访问权限。</p>
      <button type="button" class="mt-3 rounded-lg bg-primary px-3 py-1.5 text-sm text-white" @click="backToSquare">去资源广场申请</button>
    </div>
    <div v-else-if="error" class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">{{ error }}</div>
    <template v-else-if="data">
      <div class="rounded-2xl border border-gray-100 bg-white p-5 shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 class="flex flex-wrap items-center gap-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
              {{ data.name }}
              <span class="rounded-md bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-600">{{ data.visibility === "public" ? "公共级" : "部门级" }}</span>
              <span class="rounded-md bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-600">只读</span>
            </h1>
            <p class="mt-1 text-xs text-gray-500">归属 {{ data.owner || "—" }} · {{ data.documents.length }} 个文档 · 更新于 {{ data.updated_at?.slice(0, 10) || "—" }}</p>
          </div>
          <div class="flex gap-2">
            <button type="button" class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600" @click="backToSquare">返回广场</button>
          </div>
        </div>
      </div>

      <div class="rounded-xl border border-blue-100 bg-blue-50/60 px-3 py-2.5 text-xs leading-6 text-gray-600 dark:border-blue-900/40 dark:bg-blue-900/20 dark:text-gray-300">
        你拥有这个知识库的只读权限，可以浏览文档目录并在对话中检索；上传、删除文档或调整可见范围请联系管理员。出于审计要求，这里不提供原文下载。
      </div>

      <div class="overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <table class="w-full text-left text-xs">
          <thead class="bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500 dark:bg-gray-900/40">
            <tr>
              <th class="px-4 py-2.5">文档</th>
              <th class="px-4 py-2.5">类型</th>
              <th class="px-4 py-2.5">切片</th>
              <th class="px-4 py-2.5">解析状态</th>
              <th class="px-4 py-2.5">更新时间</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-50 dark:divide-gray-700/60">
            <tr v-for="doc in data.documents" :key="doc.id">
              <td class="px-4 py-2.5 text-gray-700 dark:text-gray-200">{{ doc.name }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ doc.file_type || "—" }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ doc.chunk_count ?? "—" }}</td>
              <td class="px-4 py-2.5">
                <span class="rounded-md bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-600">{{ doc.run_status || "已解析" }}</span>
              </td>
              <td class="px-4 py-2.5 text-gray-400">{{ doc.updated_at || "—" }}</td>
            </tr>
            <tr v-if="!data.documents.length">
              <td colspan="5" class="px-4 py-8 text-center text-gray-400">这个知识库还没有文档。</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
```

`ResourceSquareDataset.vue`：

```vue
<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"

import { fetchDatasetReadonly } from "@/api/resourceSquare"
import type { DatasetReadonly } from "@/types/resourceSquare"

const route = useRoute()
const router = useRouter()
const datasetId = Number(route.params.id)
const data = ref<DatasetReadonly | null>(null)
const loading = ref(true)
const error = ref("")
const forbidden = ref(false)
const activeSection = ref<"tables" | "metrics">("tables")

onMounted(async () => {
  try {
    data.value = await fetchDatasetReadonly(datasetId)
  } catch (err) {
    const status = (err as { response?: { status?: number } })?.response?.status
    forbidden.value = status === 403
    error.value = err instanceof Error ? err.message : "读取数据集失败"
  } finally {
    loading.value = false
  }
})

function backToSquare() {
  router.push({ path: "/dashboard/workbench", query: { tab: "square" } })
}
</script>

<template>
  <div class="space-y-4">
    <div v-if="loading" class="h-40 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-800/60" />
    <div v-else-if="forbidden" class="rounded-2xl border border-dashed border-gray-200 px-6 py-12 text-center">
      <p class="text-sm text-gray-600 dark:text-gray-300">你还没有这个数据集的访问权限。</p>
      <button type="button" class="mt-3 rounded-lg bg-primary px-3 py-1.5 text-sm text-white" @click="backToSquare">去资源广场申请</button>
    </div>
    <div v-else-if="error" class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">{{ error }}</div>
    <template v-else-if="data">
      <div class="rounded-2xl border border-gray-100 bg-white p-5 shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 class="flex flex-wrap items-center gap-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
              {{ data.name }}
              <span class="text-sm font-normal text-gray-400">{{ data.display_name }}</span>
              <span class="rounded-md bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-600">只读</span>
            </h1>
            <p class="mt-1 text-xs text-gray-500">数据源 {{ data.data_source || "—" }} · 质量分 {{ data.quality_score ?? "—" }} · 更新于 {{ data.updated_at?.slice(0, 10) || "—" }}</p>
          </div>
          <button type="button" class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600" @click="backToSquare">返回广场</button>
        </div>
      </div>

      <div class="rounded-xl border border-blue-100 bg-blue-50/60 px-3 py-2.5 text-xs leading-6 text-gray-600 dark:border-blue-900/40 dark:bg-blue-900/20 dark:text-gray-300">
        你拥有这个数据集的只读权限，可以浏览表结构与指标口径，并直接在对话中查询；编辑元数据、改行级权限请联系管理员。
      </div>

      <div class="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <div class="rounded-xl border border-gray-100 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
          <p class="text-[11px] text-gray-500">物理表</p>
          <p class="mt-0.5 text-xl font-bold text-gray-900 dark:text-gray-100">{{ data.tables.length }}</p>
        </div>
        <div class="rounded-xl border border-gray-100 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
          <p class="text-[11px] text-gray-500">指标</p>
          <p class="mt-0.5 text-xl font-bold text-gray-900 dark:text-gray-100">{{ data.metrics.length }}</p>
        </div>
        <div class="rounded-xl border border-gray-100 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
          <p class="text-[11px] text-gray-500">表关系</p>
          <p class="mt-0.5 text-xl font-bold text-gray-900 dark:text-gray-100">{{ data.relationship_count }}</p>
        </div>
        <div class="rounded-xl border border-gray-100 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
          <p class="text-[11px] text-gray-500">质量分</p>
          <p class="mt-0.5 text-xl font-bold text-emerald-600">{{ data.quality_score ?? "—" }}</p>
        </div>
      </div>

      <div class="overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-xs dark:border-gray-700 dark:bg-gray-800">
        <div class="flex gap-5 border-b border-gray-100 px-4 dark:border-gray-700/60">
          <button
            type="button"
            class="-mb-px border-b-2 py-2.5 text-xs font-semibold"
            :class="activeSection === 'tables' ? 'border-primary text-primary' : 'border-transparent text-gray-500'"
            @click="activeSection = 'tables'"
          >
            表结构
          </button>
          <button
            type="button"
            class="-mb-px border-b-2 py-2.5 text-xs font-semibold"
            :class="activeSection === 'metrics' ? 'border-primary text-primary' : 'border-transparent text-gray-500'"
            @click="activeSection = 'metrics'"
          >
            指标
          </button>
        </div>

        <table v-if="activeSection === 'tables'" class="w-full text-left text-xs">
          <thead class="bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500 dark:bg-gray-900/40">
            <tr>
              <th class="px-4 py-2.5">物理表</th>
              <th class="px-4 py-2.5">业务术语</th>
              <th class="px-4 py-2.5">字段数</th>
              <th class="px-4 py-2.5">说明</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-50 dark:divide-gray-700/60">
            <tr v-for="table in data.tables" :key="table.physical_name">
              <td class="px-4 py-2.5 font-mono text-gray-700 dark:text-gray-200">{{ table.physical_name }}</td>
              <td class="px-4 py-2.5 text-gray-600">{{ table.term }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ table.field_count }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ table.description || "—" }}</td>
            </tr>
          </tbody>
        </table>

        <table v-else class="w-full text-left text-xs">
          <thead class="bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500 dark:bg-gray-900/40">
            <tr>
              <th class="px-4 py-2.5">指标</th>
              <th class="px-4 py-2.5">显示名</th>
              <th class="px-4 py-2.5">口径</th>
              <th class="px-4 py-2.5">单位</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-50 dark:divide-gray-700/60">
            <tr v-for="metric in data.metrics" :key="metric.name">
              <td class="px-4 py-2.5 font-mono text-gray-700 dark:text-gray-200">{{ metric.name }}</td>
              <td class="px-4 py-2.5 text-gray-600">{{ metric.display_name }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ metric.description || "—" }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ metric.unit || "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
```

- [ ] **Step 5: 注册路由**

`router/index.ts` 在 `workbench` 路由之后加：

```ts
{
  path: `resource-square/knowledge/:datasetId`,
  name: "ResourceSquareKnowledge",
  component: () => import("@/views/ResourceSquareKnowledge.vue"),
  meta: { title: "知识库概览" },
},
{
  path: `resource-square/datasets/:id`,
  name: "ResourceSquareDataset",
  component: () => import("@/views/ResourceSquareDataset.vue"),
  meta: { title: "数据集概览" },
},
```

> 注意：这两条必须挂在 `/dashboard` 的 children 下（与 `workbench` 同级），否则路径拼不出 `/dashboard/resource-square/...`。

- [ ] **Step 6: 运行确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_workbench_square_tab_contract.py -q`
Expected: 2 passed
Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无错误

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/PersonalWorkbench.vue frontend/src/views/ResourceSquareKnowledge.vue frontend/src/views/ResourceSquareDataset.vue frontend/src/router/index.ts tests/frontend/test_workbench_square_tab_contract.py
git commit -m "feat(web): 工作台资源广场 tab 与两个只读概览页"
```

---

### Task 5: 审核弹窗改造

**Files:**
- Modify: `frontend/src/views/Users.vue`
- Test: `tests/frontend/test_access_review_modal_contract.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/frontend/test_access_review_modal_contract.py
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
USERS = ROOT / "frontend/src/views/Users.vue"


def test_review_uses_unified_endpoint_and_type_filter():
    page = USERS.read_text(encoding="utf-8")
    assert "/api/portal/access-requests/pending" in page
    assert "/api/portal/access-requests/reviews" in page
    assert "resourceTypeFilter" in page
    assert "资源访问审批" in page


def test_review_blocks_approve_when_resource_unavailable():
    page = USERS.read_text(encoding="utf-8")
    assert "resource_exists" in page
    assert "resource_enabled" in page
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_access_review_modal_contract.py -q`
Expected: FAIL

- [ ] **Step 3: 改数据层**

`Users.vue` 中把审批相关接口换成统一端点（保留变量名，减少模板改动）：

```ts
const resourceTypeFilter = ref<"all" | "dataset" | "metadata">("all")

const fetchAccessReviews = async () => {
  accessReviewsLoading.value = true
  try {
    const response = await axios.get("/api/portal/access-requests/pending", {
      params: resourceTypeFilter.value === "all" ? {} : { resource_type: resourceTypeFilter.value },
    })
    const data = response.data?.data || {}
    accessReviews.value = data.items || []
    accessReviewTotal.value = data.total || 0
    pendingReviewCount.value = accessReviewTotal.value
  } catch (err: any) {
    showToast(err?.response?.data?.detail || "读取待审批申请失败", "error")
  } finally {
    accessReviewsLoading.value = false
  }
}

const fetchReviewedRequests = async () => {
  reviewedLoading.value = true
  try {
    const response = await axios.get("/api/portal/access-requests/reviews", {
      params: resourceTypeFilter.value === "all" ? {} : { resource_type: resourceTypeFilter.value },
    })
    reviewedRequests.value = response.data?.data?.items || []
  } catch (err: any) {
    showToast(err?.response?.data?.detail || "读取审批记录失败", "error")
  } finally {
    reviewedLoading.value = false
  }
}

const decideAccessReview = async (item: any, approve: boolean) => {
  if (reviewingRequestId.value) return
  reviewingRequestId.value = item.id
  try {
    await axios.post(
      `/api/portal/access-requests/${item.id}/${approve ? "approve" : "reject"}`,
      { comment: (reviewComments.value[item.id] || "").trim() || null },
    )
    showToast(approve ? "已通过，对方现在可以访问" : "已驳回", "success")
    delete reviewComments.value[item.id]
    await fetchAccessReviews()
  } catch (err: any) {
    showToast(err?.response?.data?.detail || "审批失败", "error")
  } finally {
    reviewingRequestId.value = null
  }
}
```

- [ ] **Step 4: 改模板**

在审批抽屉标题处把「知识库访问审批」改为「资源访问审批」，并在待审批 tab 上方加类型筛选：

```vue
<div class="flex flex-wrap gap-1.5 px-1 pb-2">
  <button
    v-for="item in ([{ key: 'all', label: '全部' }, { key: 'dataset', label: '知识库' }, { key: 'metadata', label: '数据集' }] as const)"
    :key="item.key"
    type="button"
    class="rounded-full border px-2.5 py-0.5 text-[11px] font-medium"
    :class="resourceTypeFilter === item.key ? 'border-primary bg-primary/5 text-primary' : 'border-gray-200 text-gray-500'"
    @click="resourceTypeFilter = item.key; switchAccessReviewTab(accessReviewTab)"
  >
    {{ item.label }}
  </button>
</div>
```

列表项加类型徽标与不可用提示：

```vue
<span class="rounded-md px-1.5 py-0.5 text-[10px] font-semibold"
      :class="item.resource_type === 'metadata' ? 'bg-indigo-50 text-indigo-600' : 'bg-blue-50 text-blue-600'">
  {{ item.resource_type === 'metadata' ? '数据集' : '知识库' }}
</span>
<span v-if="item.resource_exists === false" class="rounded-md bg-red-50 px-1.5 py-0.5 text-[10px] font-semibold text-red-500">资源已删除</span>
<span v-else-if="item.resource_enabled === false" class="rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-600">已停用</span>
```

「通过」按钮加禁用条件：

```vue
<button
  type="button"
  class="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
  :disabled="item.resource_exists === false || item.resource_enabled === false || reviewingRequestId === item.id"
  @click="decideAccessReview(item, true)"
>
  通过
</button>
```

抽屉里规模信息改为按类型展示：

```vue
<p class="text-xs text-gray-500">
  {{ item.resource_type === 'metadata'
      ? `数据源 ${item.data_source || '—'} · ${item.table_count ?? 0} 张表 / ${item.metric_count ?? 0} 个指标`
      : `${item.doc_count ?? '—'} 个文档 / ${item.chunk_count ?? '—'} 个切片` }}
  · 归属 {{ item.owner || '—' }}
</p>
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_access_review_modal_contract.py -q`
Expected: 2 passed
Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无错误

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/Users.vue tests/frontend/test_access_review_modal_contract.py
git commit -m "feat(web): 审批弹窗支持知识库与数据集混排"
```

---

### Task 6: 我的申请改造

**Files:**
- Modify: `frontend/src/views/PersonalCenter.vue`
- Test: `tests/frontend/test_my_access_requests_contract.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/frontend/test_my_access_requests_contract.py
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/PersonalCenter.vue"


def test_my_requests_use_unified_endpoint_and_type_badge():
    page = PAGE.read_text(encoding="utf-8")
    assert "/api/portal/access-requests/mine" in page
    assert "resource_type" in page
    assert "数据集" in page
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_my_access_requests_contract.py -q`
Expected: FAIL

- [ ] **Step 3: 改接口与展示**

`PersonalCenter.vue` 中「我的申请」加载改为：

```ts
const response = await axios.get("/api/portal/access-requests/mine")
```

列表项加类型徽标（放在名称前）：

```vue
<span
  class="rounded-md px-1.5 py-0.5 text-[10px] font-semibold"
  :class="item.resource_type === 'metadata' ? 'bg-indigo-50 text-indigo-600' : 'bg-blue-50 text-blue-600'"
>
  {{ item.resource_type === 'metadata' ? '数据集' : '知识库' }}
</span>
```

空态文案改为：

```
你还没有提交过资源访问申请。去「我的工作台 → 资源广场」看看有哪些知识库和数据集可以申请。
```

撤回按钮调用的接口改为 `/api/portal/access-requests/${id}/cancel`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_my_access_requests_contract.py -q`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/PersonalCenter.vue tests/frontend/test_my_access_requests_contract.py
git commit -m "feat(web): 我的申请整合知识库与数据集"
```

---

### Task 7: 通知与配置开关

**Files:**
- Modify: `frontend/src/components/PortalNotificationBell.vue`
- Modify: `frontend/src/views/SystemConfig.vue`
- Modify: `frontend/src/views/KnowledgeBaseManagement.vue`（发现弹窗文案）
- Test: `tests/frontend/test_resource_access_notification_contract.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/frontend/test_resource_access_notification_contract.py
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def test_bell_knows_new_categories_and_keeps_legacy():
    page = (ROOT / "frontend/src/components/PortalNotificationBell.vue").read_text(encoding="utf-8")
    assert "resource_access" in page
    assert "resource_access_result" in page
    assert "knowledge_access" in page  # 历史消息仍可识别


def test_system_config_registers_datasets_switch():
    page = (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")
    assert "resource_square_show_datasets" in page


def test_discover_modal_points_to_square():
    page = (ROOT / "frontend/src/views/KnowledgeBaseManagement.vue").read_text(encoding="utf-8")
    assert "资源广场" in page
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_resource_access_notification_contract.py -q`
Expected: FAIL

- [ ] **Step 3: 通知映射**

`notificationKindLabel` 中加（放在 `knowledge_access` 两行之前）：

```ts
if (item?.category === "resource_access") return "资源申请"
if (item?.category === "resource_access_result") return "申请结果"
```

`openNotification` 中，在 `if (item?.category === "knowledge_access")` 之前插入：

```ts
  if (item?.category === "resource_access") {
    closeNotifications();
    closeDetail();
    await router
      .push({ path: "/dashboard/users", query: { review: "1", request: String(meta.request_id || "") } })
      .catch(() => undefined);
    return;
  }
  if (item?.category === "resource_access_result") {
    closeNotifications();
    closeDetail();
    await router
      .push({ path: "/dashboard/personal", query: { tab: "permissions", subtab: "requests" } })
      .catch(() => undefined);
    return;
  }
```

旧的 `knowledge_access` / `knowledge_access_result` 分支保持不动，历史消息照样能点。

- [ ] **Step 4: 配置开关**

`SystemConfig.vue` 三处注册（照 `user_registration_enabled` 的位置）：

1. 说明文案表加：`resource_square_show_datasets: '资源广场是否向普通用户展示数据集目录。关闭后只保留知识库段，已提交的申请仍会正常审批。'`
2. 布尔开关渲染列表加 `'resource_square_show_datasets'`。
3. 分组列表归到「常规设置」或与 `knowledge_base_enabled` 同组。

- [ ] **Step 5: 发现弹窗文案**

`KnowledgeBaseManagement.vue` 发现弹窗底部加一行：

```vue
<p class="text-[11px] text-gray-400">
  更多资源可在「我的工作台 → 资源广场」查看与申请。
</p>
```

- [ ] **Step 6: 运行确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_resource_access_notification_contract.py -q`
Expected: 3 passed
Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无错误

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/PortalNotificationBell.vue frontend/src/views/SystemConfig.vue frontend/src/views/KnowledgeBaseManagement.vue tests/frontend/test_resource_access_notification_contract.py
git commit -m "feat(web): 资源申请通知、目录开关与发现入口文案"
```

---

### Task 8: 工作台资源卡前端

**Files:**
- Modify: `frontend/src/constants/personalResources.ts`
- Modify: `frontend/src/components/workbench/WorkbenchPersonalResources.vue`
- Modify: `frontend/src/views/PersonalWorkbench.vue`（点击分支）
- Test: `tests/frontend/test_workbench_resource_cards_contract.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/frontend/test_workbench_resource_cards_contract.py
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CONST = ROOT / "frontend/src/constants/personalResources.ts"
CARD = ROOT / "frontend/src/components/workbench/WorkbenchPersonalResources.vue"
WORKBENCH = ROOT / "frontend/src/views/PersonalWorkbench.vue"


def test_constants_start_with_knowledge_and_datasets():
    text = CONST.read_text(encoding="utf-8")
    memory_index = text.index('key: "memory"')
    assert text.index('key: "knowledge"') < memory_index
    assert text.index('key: "datasets"') < memory_index
    assert "EMBED_WELCOME_HIDDEN_RESOURCE_KEYS" in text
    assert '"knowledge"' in text.split("EMBED_WELCOME_HIDDEN_RESOURCE_KEYS")[1]


def test_card_component_has_two_new_branches():
    text = CARD.read_text(encoding="utf-8")
    assert "item.key === 'knowledge'" in text
    assert "item.key === 'datasets'" in text
    assert "xl:grid-cols-5" in text


def test_workbench_routes_resource_cards_to_square():
    text = WORKBENCH.read_text(encoding="utf-8")
    assert "tab: \"square\"" in text or "tab: 'square'" in text
    assert "section" in text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_workbench_resource_cards_contract.py -q`
Expected: FAIL

- [ ] **Step 3: 常量同步**

`personalResources.ts`：

```ts
export const PERSONAL_RESOURCE_DEFS = [
  { key: "knowledge", label: "我的知识库", unit: "个", tab: "square" },
  { key: "datasets", label: "我的数据集", unit: "个", tab: "square" },
  { key: "memory", label: "我的记忆", unit: "条", tab: "memory" },
  // …其余保持不变
] as const
```

`EMBED_WELCOME_HIDDEN_RESOURCE_KEYS` 改为：

```ts
export const EMBED_WELCOME_HIDDEN_RESOURCE_KEYS = new Set(["memory", "data", "knowledge", "datasets"])
```

>`PERSONAL_RESOURCE_MODAL_TABS` 会自动带上两项；「我的资源」弹层点击时需要同样跳到广场——在 `PersonalWorkbench` 的 `openPersonalResource` 分支里统一处理即可。

- [ ] **Step 4: 卡片图标与配色**

`WorkbenchPersonalResources.vue` 的 `getResourceStyle` 加两个 case：

```ts
    case "knowledge":
      return {
        iconBg: "bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400",
        hoverBorder: "hover:border-blue-200 dark:hover:border-blue-600/50",
      }
    case "datasets":
      return {
        iconBg: "bg-indigo-50 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400",
        hoverBorder: "hover:border-indigo-200 dark:hover:border-indigo-600/50",
      }
```

模板里加两个 SVG 分支（放在 memory 分支之前）：

```vue
<svg v-if="item.key === 'knowledge'" ...>（书本图标，同 ResourceSquareCard 的 kb 路径）</svg>
<svg v-else-if="item.key === 'datasets'" ...>（数据库图标）</svg>
```

并把 `xl:grid-cols-6` 改为 `xl:grid-cols-5`（两处：compact 与常规分支的常规分支）。

- [ ] **Step 5: 点击跳转**

`PersonalWorkbench.vue` 的 `openPersonalResource` 顶部加：

```ts
function openPersonalResource(item: WorkbenchPersonalResource) {
  if (item.key === "knowledge" || item.key === "datasets") {
    router.push({
      path: "/dashboard/workbench",
      query: { tab: "square", section: item.key === "knowledge" ? "knowledge" : "datasets" },
    })
    return
  }
  if (isInboxPersonalResource(item)) {
    openPortalInboxPanel()
    return
  }
  router.push({ path: "/dashboard/personal", query: { tab: item.tab } })
}
```

- [ ] **Step 6: 运行确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_workbench_resource_cards_contract.py -q`
Expected: 3 passed
Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无错误

- [ ] **Step 7: Commit**

```bash
git add frontend/src/constants/personalResources.ts frontend/src/components/workbench/WorkbenchPersonalResources.vue frontend/src/views/PersonalWorkbench.vue tests/frontend/test_workbench_resource_cards_contract.py
git commit -m "feat(workbench): 首屏新增我的知识库与数据集入口卡"
```

---

### Task 9: 全量验收

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无错误

- [ ] **Step 2: 前端契约全跑**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_resource_square_api_contract.py tests/frontend/test_resource_square_card_states.py tests/frontend/test_resource_square_panel_contract.py tests/frontend/test_workbench_square_tab_contract.py tests/frontend/test_access_review_modal_contract.py tests/frontend/test_my_access_requests_contract.py tests/frontend/test_resource_access_notification_contract.py tests/frontend/test_workbench_resource_cards_contract.py -q`
Expected: 全部 passed

- [ ] **Step 3: 更新 CHECKLIST**

在 `tests/CHECKLIST.md` 增加：

```markdown
- [ ] 资源广场：知识库/数据集两段加载、筛选、申请、状态回显
- [ ] 资源广场：只授权卡片可点进只读页，未授权直接访问 URL 得到 403 引导
- [ ] 审核：知识库与数据集混排、类型筛选、「资源已删除/已停用」禁止通过
- [ ] 我的申请：两类混排、撤回、驳回原因展示
- [ ] 工作台：资源条两个新入口卡跳转广场对应段，embed 不展示
- [ ] 通知：申请到达管理员、结果到达申请人，点击可跳转
```

- [ ] **Step 4: Commit**

```bash
git add tests/CHECKLIST.md
git commit -m "docs(test): 资源广场验收清单"
```

---

## 自审清单

- [ ] 所有接口路径与后端计划逐字一致（`/api/portal/access-requests/*`、`/api/portal/resource-square/*`）
- [ ] `resource_type` 取值为 `dataset` / `metadata`，前后端一致
- [ ] 卡片四状态互斥：`has` > `pending` > `rejected` > `apply`
- [ ] 两段独立加载、独立报错，一段失败不影响另一段
- [ ] 只读页 403 有「去资源广场申请」出口
- [ ] embed 欢迎页隐藏两个新入口卡
- [ ] `npx vue-tsc --noEmit` 与 8 个契约测试全绿

---

## 实现偏差记录（2026-10-27 落地后回写）

1. **HTTP 客户端**：计划写 `import axios from "axios"`，实际统一用 `@/utils/axios`
   （带 Cookie、401 跳登录、错误日志的实例），并在 `api/resourceSquare.ts` 里统一抽取
   `detail` 文案——后端用 400 + detail 表达业务规则，直接抛 `error.message` 只会显示
   `Request failed with status code 400`。
2. **tab 必须跟 URL 走**：从「我的知识库」卡片跳 `?tab=square` 时仍在 `/dashboard/workbench`
   这条路由上，组件被复用、`setup` 不重跑，只认初始值的话 URL 变了界面纹丝不动。
   补 `watch(() => route.query.tab)`。
3. **`v-else` 改独立 `v-if`**：工作台主体容器与广场面板之间夹了说明注释，Vue 编译器保留注释时
   会报「v-else has no adjacent v-if」。两处都改成显式条件，顺带更好读。
4. **`PERSONAL_RESOURCE_MODAL_TABS` 排除 square**：新加的两张卡 `tab` 都是 `square`，
   不排除的话 embed「我的资源」弹层会多出两个跳不出去的 tab。
5. **三个既有契约随产品变更更新**（不是删测试，而是把意图翻译到新实现）：
   `test_knowledge_access_request_contract`（审核卡片字段 `dataset_exists` → `resource_exists`/`resource_enabled`）、
   `test_knowledge_access_notification_contract`（收件人从归属人改为管理员、分类改 `resource_access*`、
   落点断言跟着换）、`test_user_registration_contract`（新配置项插到布尔开关数组末尾，
   保持既有相邻性断言）。后端同理更新 `test_knowledge_access_request_endpoints` 的 7 项。
