# AI 产物「预览 / 下载」双入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 「我的产出」抽屉与消息正文里的 AI 产物都提供「预览（画布 file-viewer）+ 下载」两个明确入口，并让下载对所有类型都真的是下载。

**Architecture:** 新增判定模块 `generatedFilePreviewPlan.ts`（零运行时依赖、可被 `node --experimental-strip-types` 直接加载做行为断言）+ 取内容模块 `generatedFilePreview.ts`（依赖 axios）。`useWorkspaceCanvas` 新增 `handleGeneratedFilePreview`，以**右侧钉住**打开画布（与消息正文点链接一致）；抽屉只 emit 意图，`EmbedChat` 完成 Host 绑定、并在预览成功后收起产物抽屉。

**Tech Stack:** Vue 3 + TypeScript + Vite 7 + Tailwind 3；`@file-viewer/vue3`（既有 `DocumentViewer.vue`）；pytest 纯源码/行为契约测试。

**Spec:** `docs/superpowers/specs/2026-10-11-artifact-preview-download-design.md`

> **提交约定（重要）：** 本仓库的用户级指令禁止 Agent 执行 `git commit`。因此本计划**刻意不含提交步骤**，每个任务以「测试通过」收尾；提交时机与 commit message 由用户决定。

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `frontend/src/utils/generatedFilePreviewPlan.ts`（新增） | **纯判定 + URL 构造**：扩展名 → 画布数据形态；产物下载地址补 `download=1`。零运行时依赖（只 import `./documentPreviewFormats.ts`），供行为测试直接加载。 |
| `frontend/src/utils/generatedFilePreview.ts`（新增） | **取内容 + 构造画布 payload**：Office/PDF 只传鉴权 URL；图片/CSV 取 Blob；文本取文本；`download-only` 提示并返回 `false`。依赖 axios。 |
| `frontend/src/composables/chat/useWorkspaceCanvas.ts`（改） | 新增 `handleGeneratedFilePreview`，把「产物 URL」接到画布状态机。 |
| `frontend/src/components/embed/MyArtifactsDrawer.vue`（改） | 列表项主体=预览；右侧「预览 / 下载」两个图标；`emit('preview-file', item)`。 |
| `frontend/src/views/EmbedChat.vue`（改） | `@preview-file` 接线（含 `resolveGeneratedFileHref` Host 绑定）。 |
| `frontend/src/components/MessageRenderer.vue`（改） | `<a>` 点击补 Office 扩展名 → `open-canvas`；生成文件链接后追加「下载」按钮。 |
| `frontend/src/components/embed/ChatCanvas.vue`（改） | `downloadFile` 的 document/pdf 分支补「直链 URL」路径，避免把 URL 当文本下载。 |
| `tests/frontend/generated_file_preview.mjs`（新增） | 行为断言判定表与 URL 构造。 |
| `tests/frontend/test_generated_file_preview_contract.py`（新增） | 源码形状契约 + 驱动上面那个 mjs。 |
| `tests/CHECKLIST.md`（改） | 追加一行验收记录。 |

---

## Task 1: 判定模块 `generatedFilePreviewPlan.ts`（TDD）

**Files:**
- Create: `frontend/src/utils/generatedFilePreviewPlan.ts`
- Test: `tests/frontend/generated_file_preview.mjs`、`tests/frontend/test_generated_file_preview_contract.py`

- [x] **Step 1: 写失败的行为测试**

Create `tests/frontend/generated_file_preview.mjs`：

```js
// 用 Node 真跑纯函数：产物 → 画布数据形态的判定表，静态字符串断言看不出
// 「.pptx 会不会被 .ppt 规则误伤」这类缺陷，这里直接调用真实实现。
import assert from 'node:assert/strict'

import {
  resolveGeneratedPreviewPlan,
  buildArtifactDownloadUrl,
} from '../../frontend/src/utils/generatedFilePreviewPlan.ts'

// --- Office 6 种：只传鉴权 URL，交给 file-viewer ---
for (const name of ['a.docx', 'a.doc', 'a.xlsx', 'a.xls', 'a.xlsm', 'a.pptx']) {
  assert.deepEqual(
    resolveGeneratedPreviewPlan(name),
    { kind: 'url-document', canvasType: 'document' },
    `${name} 应走 url-document/document`,
  )
}

// --- PDF 独立走画布 pdf 分支（同样只传 URL）---
assert.deepEqual(
  resolveGeneratedPreviewPlan('a.pdf'),
  { kind: 'url-document', canvasType: 'pdf' },
  '.pdf 应走 url-document/pdf',
)

// --- 图片与 CSV：取 Blob（画布自绘）---
for (const name of ['a.png', 'a.jpg', 'a.jpeg', 'a.webp', 'a.gif']) {
  assert.deepEqual(
    resolveGeneratedPreviewPlan(name),
    { kind: 'blob', canvasType: 'image' },
    `${name} 应走 blob/image`,
  )
}
assert.deepEqual(resolveGeneratedPreviewPlan('a.csv'), { kind: 'blob', canvasType: 'csv' }, '.csv 应走 blob/csv')

// --- 文本与代码：取文本；HTML 走画布 sandbox 预览 ---
assert.deepEqual(resolveGeneratedPreviewPlan('a.html'), { kind: 'text', canvasType: 'html' }, '.html 应走 text/html')
assert.deepEqual(resolveGeneratedPreviewPlan('a.htm'), { kind: 'text', canvasType: 'html' }, '.htm 应走 text/html')
for (const name of ['a.md', 'a.markdown', 'a.txt', 'a.json', 'a.py', 'a.ts', 'a.sql', 'a.yaml']) {
  assert.deepEqual(
    resolveGeneratedPreviewPlan(name),
    { kind: 'text', canvasType: 'code' },
    `${name} 应走 text/code（.md 靠 title 后缀命中画布 Markdown 渲染器）`,
  )
}

// --- 不可预览：.ppt 与未知扩展名一律 download-only ---
assert.deepEqual(resolveGeneratedPreviewPlan('a.ppt'), { kind: 'download-only', canvasType: null }, '.ppt 只能下载')
assert.deepEqual(resolveGeneratedPreviewPlan('北京到上海高铁车次参考清单.zip'), { kind: 'download-only', canvasType: null }, '.zip 只能下载')
assert.deepEqual(resolveGeneratedPreviewPlan('README'), { kind: 'download-only', canvasType: null }, '无扩展名只能下载')
assert.deepEqual(resolveGeneratedPreviewPlan(''), { kind: 'download-only', canvasType: null }, '空串只能下载')

// --- 子串陷阱与大小写 ---
assert.deepEqual(resolveGeneratedPreviewPlan('模型对比.PPTX'), { kind: 'url-document', canvasType: 'document' }, '大写 .PPTX 应可预览')
assert.deepEqual(resolveGeneratedPreviewPlan('deck.PPT'), { kind: 'download-only', canvasType: null }, '大写 .PPT 只能下载')

// --- 下载地址：补 download=1，且不重复追加 ---
assert.equal(
  buildArtifactDownloadUrl('/api/v1/chat/generated-files/abc?token=t'),
  '/api/v1/chat/generated-files/abc?token=t&download=1',
  '已带 query 时用 & 追加',
)
assert.equal(
  buildArtifactDownloadUrl('/api/v1/chat/generated-files/abc'),
  '/api/v1/chat/generated-files/abc?download=1',
  '无 query 时用 ? 追加',
)
assert.equal(
  buildArtifactDownloadUrl('/api/v1/chat/generated-files/abc?token=t&download=1'),
  '/api/v1/chat/generated-files/abc?token=t&download=1',
  '已带 download=1 不重复追加',
)
assert.equal(
  buildArtifactDownloadUrl('https://host/api/v1/chat/generated-files/abc?token=t'),
  'https://host/api/v1/chat/generated-files/abc?token=t&download=1',
  '绝对地址同样处理',
)
assert.equal(buildArtifactDownloadUrl(''), '', '空地址保持空串')

console.log('产物预览判定测试全部通过')
```

- [x] **Step 2: 跑测试确认失败**

Run: `node --experimental-strip-types --no-warnings tests/frontend/generated_file_preview.mjs`
Expected: FAIL —— `ERR_MODULE_NOT_FOUND`（`generatedFilePreviewPlan.ts` 尚不存在）

- [x] **Step 3: 写最小实现**

Create `frontend/src/utils/generatedFilePreviewPlan.ts`：

```ts
/**
 * AI 产物（`/api/v1/chat/generated-files/...`）→ 画布数据形态的**纯判定**。
 *
 * 刻意保持零运行时依赖（只 import 同样零依赖的 ./documentPreviewFormats.ts，
 * 且带显式 .ts 扩展名），这样本模块能被 `node --experimental-strip-types` 直接加载，
 * 对判定表做真实行为断言——字符串子串断言抓不到「.pptx 被 .ppt 规则误伤」这类缺陷。
 * 取内容（axios）在 ./generatedFilePreview.ts，不要把它混进来。
 */
import {
  IMAGE_EXTENSIONS,
  OFFICE_PREVIEW_EXTENSIONS,
  TEXT_EXTENSIONS,
  getWorkspaceFileExtension,
  shouldDownloadInsteadOfPreview,
} from './documentPreviewFormats.ts'

export type GeneratedPreviewKind = 'url-document' | 'blob' | 'text' | 'download-only'

export type GeneratedPreviewCanvasType = 'document' | 'pdf' | 'image' | 'csv' | 'html' | 'code'

export interface GeneratedPreviewPlan {
  kind: GeneratedPreviewKind
  canvasType: GeneratedPreviewCanvasType | null
}

const HTML_EXTENSIONS = new Set(['.html', '.htm'])

/** 产物扩展名 → 画布数据形态；自上而下短路匹配。 */
export function resolveGeneratedPreviewPlan(name: string): GeneratedPreviewPlan {
  const ext = getWorkspaceFileExtension(name)

  // 旧版二进制 .ppt 与未知格式：既不能给 DocumentViewer，也没有画布渲染分支
  if (!ext || shouldDownloadInsteadOfPreview(name)) {
    return { kind: 'download-only', canvasType: null }
  }

  if (OFFICE_PREVIEW_EXTENSIONS.has(ext)) {
    return { kind: 'url-document', canvasType: 'document' }
  }
  if (ext === '.pdf') {
    return { kind: 'url-document', canvasType: 'pdf' }
  }
  if (IMAGE_EXTENSIONS.has(ext)) {
    return { kind: 'blob', canvasType: 'image' }
  }
  if (ext === '.csv') {
    return { kind: 'blob', canvasType: 'csv' }
  }
  if (HTML_EXTENSIONS.has(ext)) {
    return { kind: 'text', canvasType: 'html' }
  }
  if (TEXT_EXTENSIONS.has(ext)) {
    // 注意 .md 也走这里：画布的 Markdown 渲染靠 title 后缀命中 isMarkdownFile，
    // 归 'code' 才能拿到带 mermaid / ECharts 的画布渲染器。
    return { kind: 'text', canvasType: 'code' }
  }

  return { kind: 'download-only', canvasType: null }
}

/**
 * 产物下载地址：追加 `download=1` 强制后端 `Content-Disposition: attachment`。
 *
 * 后端对 html / pdf / 图片默认给 `inline`（chat.py:232-250），不追加这个参数时
 * 「下载」按钮会退化成新标签页预览。产物下载地址的构造以此处为**单一来源**。
 */
export function buildArtifactDownloadUrl(url: string): string {
  const raw = String(url || '')
  if (!raw) return ''
  if (/[?&]download=/.test(raw)) return raw
  return raw.includes('?') ? `${raw}&download=1` : `${raw}?download=1`
}
```

- [x] **Step 4: 跑测试确认通过**

Run: `node --experimental-strip-types --no-warnings tests/frontend/generated_file_preview.mjs`
Expected: PASS，stdout 含 `产物预览判定测试全部通过`

- [x] **Step 5: 写驱动它的 pytest 契约测试**

Create `tests/frontend/test_generated_file_preview_contract.py`（本任务先只写前两个用例，其余任务逐步追加）：

```python
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_generated_preview_plan_behaviour_via_node():
    script = Path("tests/frontend/generated_file_preview.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "产物预览判定测试全部通过" in proc.stdout


def test_plan_module_is_dependency_free_and_uses_explicit_ts_extension():
    source = _source("frontend/src/utils/generatedFilePreviewPlan.ts")

    # 零运行时依赖：判定模块不得引入 axios，否则 Node 无法直接加载（行为测试会静默失去覆盖）
    assert "from 'axios'" not in source
    assert "from '@/utils/axios'" not in source
    # 显式 .ts 扩展名是 Node ESM 解析内部依赖的前提（仓库已有 chatSessionExport.ts 先例）
    assert "from './documentPreviewFormats.ts'" in source
    assert "export function resolveGeneratedPreviewPlan(" in source
    assert "export function buildArtifactDownloadUrl(" in source
```

- [x] **Step 6: 跑契约测试**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_generated_file_preview_contract.py -v`
Expected: 2 passed

---

## Task 2: 取内容模块 `generatedFilePreview.ts`

**Files:**
- Create: `frontend/src/utils/generatedFilePreview.ts`
- Test: `tests/frontend/test_generated_file_preview_contract.py`（追加用例）

- [x] **Step 1: 写实现**

Create `frontend/src/utils/generatedFilePreview.ts`：

```ts
/**
 * AI 产物 → 画布内容的取数层。
 *
 * 判定在 ./generatedFilePreviewPlan.ts（零依赖、可行为测试）；本文件只负责
 * 「按判定去取内容」，因此允许依赖 axios。与 workspaceFilePreview.openWorkspaceFileInCanvas
 * 对称：那边处理工作区路径（/api/v1/chat/fs/preview），这边处理产物 token URL。
 */
import axios from '@/utils/axios'
import type { CanvasPanelData } from '@/types/canvas'
import { resolveDocumentViewerMime } from '@/utils/documentPreviewFormats'
import { resolveGeneratedPreviewPlan } from '@/utils/generatedFilePreviewPlan'

type ShowToast = (message: string, type?: 'success' | 'error' | 'warning' | 'info') => void

export interface OpenGeneratedFileOptions {
  /** 已绑定当前页面 Host 的产物鉴权地址 */
  url: string
  /** 产物文件名（用于展示、以及画布按扩展名选择渲染器） */
  name: string
  showToast: ShowToast
  onOpen: (data: CanvasPanelData) => void
  /** 画布的单槽对象 URL：由调用方（useWorkspaceCanvas）统一回收 */
  activeBlobUrlRef?: { value: string }
}

const asString = (value: unknown) => (typeof value === 'string' ? value : String(value ?? ''))

const describeError = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (error?.response?.status === 404) return '文件不存在或已过期'
  return error?.message || fallback
}

/**
 * 在画布中打开一个 AI 产物。
 *
 * @returns true = 已打开预览；false = 该类型不支持预览（调用方决定是否改走下载）
 */
export async function openGeneratedFileInCanvas(
  options: OpenGeneratedFileOptions,
): Promise<boolean> {
  const { url, name, showToast, onOpen, activeBlobUrlRef } = options
  const plan = resolveGeneratedPreviewPlan(name)

  if (!url) {
    showToast('该产出物缺少下载地址', 'warning')
    return false
  }

  if (plan.kind === 'download-only') {
    // 抽屉已在 emit 前拦掉这一类（见设计 4.1），此处是给其他调用方的防御性兜底
    showToast('该格式暂不支持预览，已为你下载原文件', 'info')
    return false
  }

  try {
    if (plan.kind === 'url-document') {
      // Office / PDF 只传鉴权 URL：Blob 由 DocumentViewer 用 axios 取，
      // 避免 Blob 进入响应式状态（与工作区预览同一口径）。
      onOpen({
        type: plan.canvasType === 'pdf' ? 'pdf' : 'document',
        title: name,
        content: url,
        documentMeta: {
          filename: name,
          mime: plan.canvasType === 'pdf' ? 'application/pdf' : resolveDocumentViewerMime(name),
        },
      })
      return true
    }

    if (plan.kind === 'blob') {
      const response = await axios.get(url, { responseType: 'blob' })
      const blobUrl = URL.createObjectURL(response.data as Blob)
      if (activeBlobUrlRef) activeBlobUrlRef.value = blobUrl
      onOpen({
        type: plan.canvasType === 'csv' ? 'csv' : 'image',
        title: name,
        content: blobUrl,
      })
      return true
    }

    const response = await axios.get(url, { responseType: 'text' })
    onOpen({
      type: plan.canvasType === 'html' ? 'html' : 'code',
      title: name,
      content: asString(response.data),
    })
    return true
  } catch (error: any) {
    console.error('加载 AI 产物失败:', error)
    showToast(describeError(error, '加载产出物失败'), 'error')
    return false
  }
}
```

- [x] **Step 2: 类型检查**

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=6144 npx vue-tsc -b --force`
Expected: 错误条数 = 既有基线（改动前先跑一次记录基线；本文件零命中）

- [x] **Step 3: 追加契约用例**

在 `tests/frontend/test_generated_file_preview_contract.py` 追加：

```python
def test_content_loader_passes_url_for_office_and_blob_for_images():
    source = _source("frontend/src/utils/generatedFilePreview.ts")

    assert "export async function openGeneratedFileInCanvas(" in source
    # Office / PDF 不预取 Blob：只把鉴权 URL 交给 DocumentViewer
    assert "responseType: 'blob'" in source
    assert "type: plan.canvasType === 'pdf' ? 'pdf' : 'document'" in source
    # 对象 URL 必须写回画布的单槽 ref，否则会泄漏
    assert "activeBlobUrlRef.value = blobUrl" in source
```

- [x] **Step 4: 跑契约测试**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_generated_file_preview_contract.py -v`
Expected: 3 passed

---

## Task 3: `useWorkspaceCanvas.handleGeneratedFilePreview`

**Files:**
- Modify: `frontend/src/composables/chat/useWorkspaceCanvas.ts`
- Test: `tests/frontend/test_generated_file_preview_contract.py`（追加用例）

- [x] **Step 1: 写实现**

在 `useWorkspaceCanvas.ts` 的 import 区加入：

```ts
import { openGeneratedFileInCanvas } from "@/utils/generatedFilePreview";
```

在 `handleWorkspaceFilePreview` 之后新增：

```ts
  /**
   * 打开 AI 产物（「我的产出」抽屉 / 消息正文里的产物链接）。
   *
   * **右侧钉住**（canvasFromWorkspace = false + showCanvas(true)），与消息正文点产物链接一致；
   * 调用方须在预览成功后收起产物抽屉（否则右侧抽屉 z-125 与画布 z-145 互挤）。
   * 传入的 url 必须已绑定当前页面 Host
   * （见 utils/generatedFileUrl.resolveGeneratedFileHref），跨站嵌入时相对路径会打到宿主域。
   */
  const handleGeneratedFilePreview = async (payload: { url: string; name: string }) => {
    canvasFromWorkspace.value = false;
    return openGeneratedFileInCanvas({
      url: payload.url,
      name: payload.name,
      showToast: options.showToast,
      activeBlobUrlRef: activeBlobUrl,
      onOpen: (data) => {
        // 对象 URL 的旧值回收在 openGeneratedFileInCanvas 内完成（先回收旧值再赋值），
        // 这里只负责采用新数据，避免把刚创建的对象 URL 又 revoke 掉。
        canvasData.value = data;
        // 钉住：桌面端与对话并排，移动端由 showCanvas 内部自动降级为全屏
        showCanvas(true);
      },
    });
  };
```

> 注意：产物预览是右侧钉住，**调用方（EmbedChat）必须在预览成功后收起抽屉**；取内容失败（404 / 断网）时不收，用户还能改点「下载」。

在返回对象中加入 `handleGeneratedFilePreview`：

```ts
  return {
    canvasVisible,
    canvasPinned,
    canvasFromWorkspace,
    canvasData,
    handleWorkspaceFilePreview,
    handleGeneratedFilePreview,
    handleOpenCanvas,
    closeCanvas,
    revokeActiveBlobUrl,
  };
```

- [x] **Step 2: （实现期已定案）对象 URL 的回收归属**

回收放在 `openGeneratedFileInCanvas` 的 blob 分支内：**先 `URL.revokeObjectURL` 旧值、再 `createObjectURL` 新值并写回 ref**（与 `openWorkspaceFileInCanvas:199-206` 同序）；`onOpen` 因此完全不碰 ref。未引入 `onBeforeBlobAdopt` 之类的回调 —— 单一写者更简单，也彻底避开「把刚创建的对象 URL 又 revoke 掉」这类顺序 bug（契约测试对这条顺序有断言）。

- [x] **Step 3: 追加契约用例**

```python
def test_workspace_canvas_exposes_generated_file_preview_on_right_dock():
    source = _source("frontend/src/composables/chat/useWorkspaceCanvas.ts")

    # 必须切片断言：showCanvas(false) 在同一文件的工作区预览里也有（否则改成不钉住会假绿）
    body = source[source.index("const handleGeneratedFilePreview = async (") : source.index("const handleOpenCanvas = async (")]
    assert "canvasFromWorkspace.value = false;" in body
    assert "showCanvas(true);" in body
    assert "showCanvas(false)" not in body
    assert "return openGeneratedFileInCanvas({" in body
    assert "handleGeneratedFilePreview," in source
```

- [x] **Step 4: 跑契约测试**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_generated_file_preview_contract.py -v`
Expected: 4 passed

---

## Task 4: 抽屉双入口

**Files:**
- Modify: `frontend/src/components/embed/MyArtifactsDrawer.vue:1-30`（脚本）、`:400-437`（列表模板）
- Test: `tests/frontend/test_generated_file_preview_contract.py`（追加用例）

- [x] **Step 1: 脚本区改造**

import 区加入：

```ts
import { buildArtifactDownloadUrl, resolveGeneratedPreviewPlan } from '@/utils/generatedFilePreviewPlan'
```

`defineEmits` 扩为：

```ts
const emit = defineEmits<{
  'select-reusable-result': [result: ReusableResultListItem]
  'preview-file': [item: ArtifactListItem]
}>()
```

把 `openArtifact`（`:175-182`）替换为：

```ts
/** 下载：强制 attachment，html/pdf/图片也真正下载而不是新标签页预览 */
const downloadArtifact = (it: ArtifactListItem) => {
  if (!it.download_url) {
    showToast('该产出物缺少下载地址', 'warning')
    return
  }
  const href = buildArtifactDownloadUrl(resolveGeneratedFileHref(it.download_url))
  window.open(href, '_blank', 'noopener,noreferrer')
}

const canPreviewArtifact = (it: ArtifactListItem) =>
  resolveGeneratedPreviewPlan(it.filename).kind !== 'download-only'

/** 预览：交给父组件在画布中打开（画布状态归 useWorkspaceCanvas） */
const previewArtifact = (it: ArtifactListItem) => {
  if (!it.download_url) {
    showToast('该产出物缺少下载地址', 'warning')
    return
  }
  if (!canPreviewArtifact(it)) {
    showToast('该格式暂不支持预览，已为你下载原文件', 'info')
    downloadArtifact(it)
    return
  }
  emit('preview-file', it)
}

/** 主体点击：可预览则预览，否则下载（不留"点了没反应"的死区） */
const activateArtifact = (it: ArtifactListItem) => {
  if (canPreviewArtifact(it)) previewArtifact(it)
  else downloadArtifact(it)
}
```

- [x] **Step 2: 模板区改造**

把列表项（`:400-437`）的 `<button>` 改为「主体按钮 + 右侧动作区」结构：

```vue
              <ul v-else class="flex flex-col gap-2">
                <li v-for="it in items" :key="it.id" class="group">
                  <div
                    class="w-full flex items-start gap-3 rounded-xl p-3 transition-colors border border-transparent hover:bg-gray-50 dark:hover:bg-gray-800/60 hover:border-gray-100 dark:hover:border-gray-700"
                  >
                    <button
                      type="button"
                      class="flex flex-1 min-w-0 items-start gap-3 text-left"
                      :title="canPreviewArtifact(it) ? `预览 ${it.filename}` : `下载 ${it.filename}`"
                      @click="activateArtifact(it)"
                    >
                      <span
                        :class="[
                          'flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center border mt-0.5 text-lg',
                          fileVisuals[it.id]?.iconBg || 'bg-gray-100 dark:bg-gray-800',
                        ]"
                      >
                        {{ fileVisuals[it.id]?.icon || '📎' }}
                      </span>
                      <span class="flex-1 min-w-0">
                        <span class="block text-sm font-semibold text-gray-800 dark:text-gray-100 truncate">
                          {{ it.filename }}
                        </span>
                        <span class="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-400 dark:text-gray-500">
                          <span class="px-1 py-px rounded bg-gray-100 dark:bg-gray-800 text-[10px] font-semibold">{{ typeLabel(it.artifact_type) }}</span>
                          <span>{{ formatSize(it.size) }}</span>
                          <span v-if="formatTime(it.created_at)">{{ formatTime(it.created_at) }}</span>
                          <span
                            class="rounded px-1 py-px text-[10px] font-semibold"
                            :class="it.trace_id === props.traceId ? 'bg-primary/10 text-primary' : 'bg-gray-100 dark:bg-gray-800'"
                            :title="artifactTraceTitle(it.trace_id)"
                          >
                            {{ artifactTraceLabel(it) }}
                          </span>
                        </span>
                      </span>
                    </button>
                    <span class="flex items-center gap-0.5 flex-shrink-0 self-center">
                      <button
                        v-if="canPreviewArtifact(it)"
                        type="button"
                        class="p-1.5 rounded-lg text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-800 transition-all opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                        :class="isMobile ? 'opacity-100' : ''"
                        :title="`预览 ${it.filename}`"
                        :aria-label="`预览 ${it.filename}`"
                        @click.stop="previewArtifact(it)"
                      >
                        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                        </svg>
                      </button>
                      <button
                        type="button"
                        class="p-1.5 rounded-lg text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-800 transition-all opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                        :class="isMobile ? 'opacity-100' : ''"
                        :title="`下载 ${it.filename}`"
                        :aria-label="`下载 ${it.filename}`"
                        @click.stop="downloadArtifact(it)"
                      >
                        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                        </svg>
                      </button>
                    </span>
                  </div>
                </li>
                <li v-if="loading" class="flex items-center justify-center py-4">
                  <svg class="h-5 w-5 text-primary animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                </li>
              </ul>
```

- [x] **Step 3: 追加契约用例**

```python
def test_artifacts_drawer_has_preview_and_download_actions():
    source = _source("frontend/src/components/embed/MyArtifactsDrawer.vue")

    assert "'preview-file': [item: ArtifactListItem]" in source
    assert "const previewArtifact = (it: ArtifactListItem)" in source
    assert "const downloadArtifact = (it: ArtifactListItem)" in source
    assert "const canPreviewArtifact = (it: ArtifactListItem)" in source
    assert "buildArtifactDownloadUrl(resolveGeneratedFileHref(it.download_url))" in source
    # 动作区必须在卡片主体之外，且不得把点击冒泡成预览
    assert '@click.stop="previewArtifact(it)"' in source
    assert '@click.stop="downloadArtifact(it)"' in source
    # 不可预览类型不得渲染预览按钮
    assert 'v-if="canPreviewArtifact(it)"' in source
```

- [x] **Step 4: 跑契约测试 + 类型检查**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_generated_file_preview_contract.py -v`
Expected: 5 passed

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=6144 npx vue-tsc -b --force`
Expected: 错误条数 = 基线，`MyArtifactsDrawer.vue` 零命中

---

## Task 5: `EmbedChat` 接线

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`（`:1491-1500` 模板、`:6124-6139` 解构处）
- Test: `tests/frontend/test_generated_file_preview_contract.py`（追加用例）

- [x] **Step 1: 解构新方法**

在 `useWorkspaceCanvas({...})` 的解构中加入 `handleGeneratedFilePreview`：

```ts
const {
  canvasVisible,
  canvasPinned,
  canvasFromWorkspace,
  canvasData,
  handleWorkspaceFilePreview,
  handleGeneratedFilePreview,
  handleOpenCanvas,
  closeCanvas,
  revokeActiveBlobUrl,
} = useWorkspaceCanvas({
  getConversationId: () => conversationId.value,
  resolveFileUrl,
  showToast,
  isMobile: () => isMobile.value,
});
```

- [x] **Step 2: 模板接线**

`MyArtifactsDrawer` 上追加事件（注意 `resolveGeneratedFileHref` 做当前页面 Host 绑定）：

```vue
    <MyArtifactsDrawer
      v-model="showMyArtifactsDrawer"
      :conversation-id="conversationId"
      :trace-id="focusedOutputTraceId"
      :initial-tab="myArtifactsInitialTab"
      :selected-result-id="selectedReusableResultId"
      :focused-result-id="focusedReusableResultId"
      :reused-result-id="reusedReusableResultId"
      @select-reusable-result="selectReusableResult"
      @preview-file="previewArtifactInCanvas"
    />
```

在 `<script setup>` 里（靠近 `openMessageArtifacts`）新增：

```ts
/**
 * 抽屉里的产物预览：把地址绑到当前页面 Host 再交给画布。
 * 跨站嵌入时 download_url 里是配置的 APP_PUBLIC_URL，直接当相对路径用会打到宿主域。
 */
const previewArtifactInCanvas = async (item: { download_url: string; filename: string }) => {
  const opened = await handleGeneratedFilePreview({
    url: resolveGeneratedFileHref(item.download_url),
    name: item.filename,
  });
  // 画布右侧钉住：预览成功才收起抽屉；失败（404 / 断网）保留，用户还能改点「下载」
  if (opened) showMyArtifactsDrawer.value = false;
};
```

- [x] **Step 3: 追加契约用例**

```python
def test_embed_chat_wires_drawer_preview_to_canvas_with_host_binding():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert '@preview-file="previewArtifactInCanvas"' in source
    assert "const previewArtifactInCanvas = async (item: { download_url: string; filename: string })" in source
    assert "if (opened) showMyArtifactsDrawer.value = false;" in source
    assert "resolveGeneratedFileHref(item.download_url)" in source
    assert "handleGeneratedFilePreview," in source
```

- [x] **Step 4: 跑契约测试 + 类型检查**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_generated_file_preview_contract.py -v`
Expected: 6 passed

---

## Task 6: 消息正文的预览 + 下载按钮

**Files:**
- Modify: `frontend/src/components/MessageRenderer.vue`（`:152-166` 后处理、`:312-380` 点击处理）
- Test: `tests/frontend/test_generated_file_preview_contract.py`（追加用例）

- [x] **Step 1: 注入「下载」按钮**

import 区加入：

```ts
import { buildArtifactDownloadUrl } from '@/utils/generatedFilePreviewPlan';
import { resolveDocumentViewerMime } from '@/utils/documentPreviewFormats';
```

在 `postProcessHtml` 里、`linkifyGeneratedFileUrls` 调用之后（`:236-237`）插入：

```ts
    // 产物链接后追加「下载」按钮：链接本体点击=画布预览，显式下载走这个属性，
    // 因此地址放在 data-* 上而不是 href（href 会被下面的预览分支截走）。
    textWithPlaceholders = appendGeneratedFileDownloadActions(textWithPlaceholders);
```

新增函数（放在 `linkifyGeneratedFileUrls` 相关的后处理函数附近）：

```ts
  const GENERATED_FILE_HREF_PATTERN = /(\/api\/v1\/chat\/generated-files\/[0-9a-f]{32}\?token=[A-Za-z0-9_-]+)/i;

  /** 给产物链接追加「下载」小按钮（用 data 属性承载地址，避免被预览分支截走） */
  const appendGeneratedFileDownloadActions = (html: string) =>
    html.replace(/<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, (match, href, inner) => {
      if (!GENERATED_FILE_HREF_PATTERN.test(href)) return match;
      if (match.includes('data-generated-download')) return match;
      const escaped = String(href).replace(/"/g, '&quot;');
      return `${match}<button type="button" class="generated-file-download-btn" data-generated-download="${escaped}" title="下载原文件" style="cursor: pointer;">下载</button>`;
    });
```

- [x] **Step 2: 点击处理**

在 `handleContentClick` 的 `const linkEl = target.closest('a')` **之前**插入：

```ts
  const downloadBtn = target.closest<HTMLElement>('[data-generated-download]');
  if (downloadBtn) {
    const url = downloadBtn.getAttribute('data-generated-download') || '';
    if (url) {
      const a = document.createElement('a');
      a.href = buildArtifactDownloadUrl(url);
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }
    event.preventDefault();
    event.stopPropagation();
    return;
  }
```

在 `<a>` 分支的扩展名判定里补 Office（`:336-343` 一带）：

```ts
        const isOfficeDoc = ['.docx', '.doc', '.xlsx', '.xls', '.xlsm', '.pptx']
          .some((ext) => lowerHref.endsWith(ext) || linkText.endsWith(ext));
```

并把 `if (isPdf || isCsv || isHtml || isImage || isCompare || isCanvasFile)` 扩为包含 `|| isOfficeDoc`，
在分支内部（`isHtml` 之前）加：

```ts
          } else if (isOfficeDoc) {
            // Office 交给画布的 file-viewer 渲染（此前这里没有分支，点击即被浏览器下载）
            type = 'document';
            filename = linkEl.textContent?.trim() || 'Office 文档';
          }
```

同时把该分支末尾的 emit 改为带上 documentMeta：

```ts
          emit('open-canvas', {
            type,
            title: filename,
            content: href,
            documentMeta: isOfficeDoc
              ? { filename, mime: resolveDocumentViewerMime(filename) }
              : undefined,
          });
```

- [x] **Step 3: 样式**

在 `MessageRenderer.vue` 的 `<style>` 里追加（与既有 `.generated-file-link` 同区）：

```css
.generated-file-download-btn {
  margin-left: 0.375rem;
  padding: 0 0.375rem;
  border-radius: 0.375rem;
  border: 1px solid rgb(191 219 254);
  background: rgb(239 246 255);
  color: rgb(37 99 235);
  font-size: 10.5px;
  font-weight: 700;
  vertical-align: middle;
}
.generated-file-download-btn:hover { background: rgb(219 234 254); }
.dark .generated-file-download-btn {
  border-color: rgb(30 58 138);
  background: rgb(30 58 138 / 0.25);
  color: rgb(147 197 253);
}
```

- [x] **Step 4: 追加契约用例**

```python
def test_message_renderer_previews_office_and_offers_explicit_download():
    source = _source("frontend/src/components/MessageRenderer.vue")

    # Office 链接此前没有任何分支，点击落到浏览器默认行为（即下载）
    assert "const isOfficeDoc = ['.docx', '.doc', '.xlsx', '.xls', '.xlsm', '.pptx']" in source
    assert "type = 'document';" in source
    assert "data-generated-download" in source
    assert "buildArtifactDownloadUrl(url)" in source
    # 下载按钮必须在 <a> 分支之前处理，否则会被预览逻辑截走
    assert source.index("target.closest<HTMLElement>('[data-generated-download]')") < source.index("const linkEl = target.closest('a')")
```

- [x] **Step 5: 跑契约测试 + 类型检查**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_generated_file_preview_contract.py -v`
Expected: 7 passed

---

## Task 7: 修 `ChatCanvas.downloadFile` 的直链兜底

**Files:**
- Modify: `frontend/src/components/embed/ChatCanvas.vue:111-150`
- Test: `tests/frontend/test_generated_file_preview_contract.py`（追加用例）

- [x] **Step 1: 写实现**

import 区加入：

```ts
import { buildArtifactDownloadUrl } from '@/utils/generatedFilePreviewPlan';
```

把 `:117-127` 的分支扩为：

```ts
  if (props.data.type === 'document' || props.data.type === 'pdf') {
    if (props.data.downloadPath) {
      downloadWorkspaceFile({
        path: props.data.downloadPath,
        name: props.data.documentMeta?.filename || props.data.title,
        conversationId: resolveConversationId(),
        showToast,
      });
      return;
    }
    // 产物预览（无工作区路径）走这里：content 是鉴权 URL。
    // 若放任它落到下面的「把 content 当文本做 Blob」兜底，用户会拿到一个
    // 内容是 URL 字符串的 .docx。产物地址不带扩展名，因此只能按 URL 形态判定。
    if (/^(https?:)?\/\//.test(content) || content.startsWith('/api/')) {
      const a = document.createElement('a');
      a.href = buildArtifactDownloadUrl(content);
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return;
    }
  }
```

- [x] **Step 2: 追加契约用例**

```python
def test_canvas_download_falls_back_to_direct_url_for_artifacts():
    source = _source("frontend/src/components/embed/ChatCanvas.vue")

    assert "buildArtifactDownloadUrl(content)" in source
    # 直链兜底必须落在「把 content 当文本下载」之前
    assert source.index("buildArtifactDownloadUrl(content)") < source.index("a.download = `canvas_export.${extension}`")
```

- [x] **Step 3: 跑契约测试 + 类型检查**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_generated_file_preview_contract.py -v`
Expected: 8 passed

---

## Task 8: 全量回归 + 登记

**Files:**
- Modify: `tests/CHECKLIST.md`

- [x] **Step 1: 前端契约全量**

Run: `pytest --confcutdir=tests/frontend`
Expected: 全绿（记录 passed 数；基线为 1689 passed）

- [x] **Step 2: 类型检查（真实门禁）**

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=6144 npx vue-tsc -b --force`
Expected: 错误条数 = 既有基线（62 条），且本次改动的 6 个文件零新增命中

- [x] **Step 3: 变异验证（每条断言都要有牙）**

逐条改坏实现 → 对应用例必须变红 → 按 md5 原样还原：

| 变异 | 期望变红 |
| --- | --- |
| M1 `resolveGeneratedPreviewPlan` 把 `.pptx` 判成 `download-only` | mjs 行为测试 |
| M2 `buildArtifactDownloadUrl` 不再追加 `download=1` | mjs 行为测试 + 契约 |
| M3 抽屉下载动作去掉 `buildArtifactDownloadUrl` | 抽屉契约 |
| M4 抽屉对不可预览类型也 emit 预览 | 抽屉契约 |
| M5 `MessageRenderer` 的下载按钮处理挪到 `<a>` 分支之后 | MessageRenderer 契约 |
| M6 `ChatCanvas` 直链兜底挪到文本 Blob 之后 | ChatCanvas 契约 |
| M7 产物预览退回「左侧停靠、不钉住」（`showCanvas(false)` + `canvasFromWorkspace = true`） | useWorkspaceCanvas 契约 |
| M8 预览成功后不收起抽屉 | EmbedChat 契约 |
| M9 预览失败也收起抽屉 | EmbedChat 契约 |

- [x] **Step 4: 登记 CHECKLIST**

在 `tests/CHECKLIST.md` 表格末尾追加一行，列：改动文件清单、需求原话、根因证据链（含行号）、实现要点、测试与变异验证结果、`vue-tsc` 基线对比、**「未执行 git commit / 未代跑 ./dev.sh」**、结论（✅ 一段话）、日期 `2026-10-11`。

- [x] **Step 5: 汇报**

向用户汇报：改动文件清单、验证结果、需要其自行执行的 `./dev.sh` 与实机验收清单（见 spec 第 8 节）。

---

## Self-Review

**Spec coverage：**

| Spec 章节 | 对应任务 |
| --- | --- |
| 4.1 抽屉双入口 / 不可预览类型 | Task 4 |
| 4.2 预览打开方式（右侧钉住 + 预览成功后收起抽屉） | Task 3、Task 5 |
| 5.1 判定模块（含 `.md`→code、`.csv`→csv 的理由） | Task 1 |
| 5.2 取内容模块（URL/Blob/文本三态、错误提示） | Task 2 |
| 6 接线（useWorkspaceCanvas / 抽屉 / EmbedChat / MessageRenderer / ChatCanvas） | Task 3-7 |
| 7.1 下载补 `download=1` | Task 1（`buildArtifactDownloadUrl`）+ Task 4 + Task 6 |
| 7.2 `ChatCanvas.downloadFile` 直链兜底 | Task 7 |
| 8 测试（行为测试 + 契约测试 + 手动清单） | Task 1、8 |
| 9 边界（只读、对象 URL 单槽、TTL 提示） | Task 2（不传 `sourcePath`、写回 ref）、Task 8（汇报） |

**Type consistency：** `resolveGeneratedPreviewPlan` / `buildArtifactDownloadUrl` / `openGeneratedFileInCanvas` / `handleGeneratedFilePreview` / `previewArtifactInCanvas` / `previewArtifact` / `downloadArtifact` / `canPreviewArtifact` / `activateArtifact` 在各任务中命名一致；`GeneratedPreviewPlan.canvasType` 的取值与 `CanvasPanelData.type` 的联合类型对齐（`document | pdf | image | csv | html | code` 均为其子集）。

**未决项（实现期已全部定案）：**
- 对象 URL 回收归属 → 见 Task 3 Step 2（回收在取数模块内、`onOpen` 不碰 ref）。
- 判定集合 → 实现期由行为测试抓出：必须用 `DOCUMENT_VIEWER_EXTENSIONS ∪ TEXT_EXTENSIONS`（单用 `TEXT_EXTENSIONS` 会漏掉 `.markdown`）。
- 「下载」按钮注入的幂等 → 跨层行为测试抓出：必须检查链接**紧邻后方**是否已有按钮（看 `<a>` 自身永远为假）。

**实测结果（2026-10-11 执行完毕）：**

| 项 | 结果 |
| --- | --- |
| 端口行为测试 `generated_file_preview.mjs` | 通过（RED → GREEN，中途抓出 `.markdown` 判定缺陷） |
| 契约测试 `test_generated_file_preview_contract.py` | 9 passed（含 1 项跨层行为验证） |
| 变异验证 | **9/9 命中且字节级还原** |
| 前端契约全量 | 1698 passed（基线 1689 + 新增 9） |
| `vue-tsc -b --force` | 63 条 = 既有基线，本次 6 个改动文件零命中 |
| 既有行为测试同步 | `test_chat_shared_helpers_behavior.py` 补 `@/utils/generatedFilePreview` 替身后 81 passed |
| 遗留 | 未跑 `./dev.sh`、未执行 `git commit`（均按仓库规约由用户执行） |


---

## 补记（2026-10-11）：正文侧文本类产物预览

用户实测发现正文里点 `.md` 产物直接下载（抽屉里却能预览）→ 原因是正文侧自维护了一份扩展名清单、漏了全部文本类格式。

修法与验证见 [`2026-10-11-artifact-preview-download-design.md`](../specs/2026-10-11-artifact-preview-download-design.md) 第 11 节（正文侧改为复用 `resolveGeneratedPreviewPlan`，并加"宿主未接线则退回下载"的兜底）。
