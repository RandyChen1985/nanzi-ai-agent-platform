# 画布文档预览接入 file-viewer 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让聊天画布能直接预览 Office 文档（docx/doc/xlsx/xls/xlsm/pptx）与 PDF，替换现有的「只能下载」行为。

**Architecture:** 新增隔离子组件 `DocumentViewer.vue` 承载 file-viewer 全部职责（懒加载、Blob 获取、主题桥接、错误降级）；类型定义收敛到 `frontend/src/types/canvas.ts`；分派层新增 `document` 类型并把 Office/PDF 从「预取 Blob」改为「只传鉴权 URL」。**后端零改动。**

**Tech Stack:** Vue 3 + TypeScript + Vite 7 + Tailwind 3；`@file-viewer/vue3` + `@file-viewer/preset-standard` + `@file-viewer/vite-plugin`；测试为 pytest 纯源码契约测试（`pytest --confcutdir=tests/frontend`）+ `vue-tsc -b` 类型检查（命令与判据见「执行环境注意」）。

**对应规格：** [`docs/superpowers/specs/2026-10-08-canvas-document-preview-design.md`](../specs/2026-10-08-canvas-document-preview-design.md)

---

## 文件结构

| 文件 | 职责 | 动作 |
| --- | --- | --- |
| `frontend/src/types/canvas.ts` | 画布类型唯一来源 | 新建 |
| `frontend/src/components/embed/DocumentViewer.vue` | 封装 file-viewer：懒加载 / Blob 获取 / 主题 / 降级 | 新建 |
| `frontend/src/utils/workspaceFilePreview.ts` | 扩展名分派与工作区预览入口 | 修改 |
| `frontend/src/composables/chat/useWorkspaceCanvas.ts` | `canvas://file` 解析与画布状态 | 修改 |
| `frontend/src/components/embed/ChatCanvas.vue` | 画布渲染分支与下载动作 | 修改 |
| `frontend/src/components/MessageRenderer.vue` | 消息链接派发 | 修改 |
| `frontend/package.json` / `frontend/vite.config.ts` | 依赖与资产插件 | 修改 |
| `tests/frontend/test_canvas_types_contract.py` | 类型唯一来源契约 | 新建 |
| `tests/frontend/test_document_viewer_contract.py` | 组件契约 | 新建 |
| `tests/frontend/test_workspace_document_preview_contract.py` | 分派层契约 | 新建 |

**提交约定：** 每个任务末尾是「检查点」而非自动提交。Agent **不得自行 `git commit`**（项目约定），只汇报改动与验证结果，提交由用户决定。

---

## 执行环境注意（每个执行者都要读）

- **类型检查必须用 `vue-tsc -b`，绝不能用 `vue-tsc --noEmit`。** `frontend/tsconfig.json` 是 solution 风格（`{"files": [], "references": [...]}`），**非 `-b` 模式不会跟随 references，会空跑、一个文件都不检查**。实测：`npx vue-tsc --noEmit` → EXIT=0、**0 行输出**——这是一个假的"通过"，会让人误以为类型没问题。统一使用：
  ```bash
  cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b
  ```
  `NODE_OPTIONS` 不可省略：默认 ~2GB 堆会 **OOM 崩溃**（实测 `FATAL ERROR: Ineffective mark-compacts near heap limit`、EXIT=134）。项目 `package.json` 的 build 脚本用的正是 `vue-tsc -b`，所以这是项目认可的检查方式。
- **类型检查的判据是错误条数，不是"输出为空"。** 项目既有基线 = **62 条错误**（分布：`views/EmbedChat.vue` 12、`utils/browserDetect.ts` 12、`components/personal/NotificationConfigs.vue` 10、`components/embed/BrowserPanel.vue` 7、`views/TaskCenter.vue` 5、`views/AgentDebug.vue` 4、`views/SystemConfig.vue` 2、`utils/processTimeline.ts` 2、`components/metadata/DatabaseImportModal.vue` 2，其余 6 个文件各 1）。这些**全部与本次改动无关**，不要试图去修。判断标准一律是**错误数是否回到 62**。
- **必须用项目虚拟环境跑 pytest。** PATH 上的 `pytest` 会解析到 Python 3.13 framework 版（无项目依赖），直接 `ModuleNotFoundError`。统一使用：
  ```bash
  .venv/bin/python -m pytest --confcutdir=tests/frontend <测试路径> -v
  ```
- **依赖已安装**：`frontend/node_modules/@file-viewer/` 下已存在 `vue3`、`preset-standard`、`vite-plugin`。
- **工作区存在其他任务的并发改动**：`frontend/src/components/system/ToolRegistry.vue`、`tests/CHECKLIST.md`、`tests/frontend/test_api_tool_business_group_contract.py` 属于**既有（非本计划）**改动，**不要触碰、不要回滚**。Task 7 往 `CHECKLIST.md` 追加内容时也只允许追加，不得改动既有行。
- 沙箱环境下 `pnpm install` 会把 store 落到项目根的 `.pnpm-store/`（用户级 store 不可写），属环境产物，与代码无关；**不要提交它**。

---

## Task 1: 建立画布类型唯一来源

**Files:**
- Create: `frontend/src/types/canvas.ts`
- Test: `tests/frontend/test_canvas_types_contract.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/frontend/test_canvas_types_contract.py`：

```python
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canvas_types_are_defined_in_single_source():
    source = _source("frontend/src/types/canvas.ts")

    assert "export type WorkspaceCanvasType" in source
    assert "'document'" in source
    assert "export interface CanvasDocumentMeta" in source
    assert "export interface CanvasPanelData" in source
    assert "sourcePath?: string" in source
    assert "documentMeta?: CanvasDocumentMeta" in source
    assert "downloadPath?: string" in source
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_canvas_types_contract.py -v`
Expected: FAIL —`FileNotFoundError`（`types/canvas.ts` 尚不存在）

- [ ] **Step 3: 创建类型文件**

创建 `frontend/src/types/canvas.ts`：

```ts
/**
 * 画布类型定义的唯一来源。
 *
 * 此前该类型分散在 ChatCanvas.vue、useWorkspaceCanvas.ts、
 * workspaceFilePreview.ts、MessageRenderer.vue 四处，且已互相漂移
 * （前三处把 mermaid/compare 纳入，workspaceFilePreview 则另用联合扩展），
 * 现统一到本文件，避免继续分叉。
 */

/** 画布面板可渲染的内容类型 */
export type WorkspaceCanvasType =
  | 'html'
  | 'code'
  | 'mermaid'
  | 'pdf'
  | 'csv'
  | 'image'
  | 'compare'
  | 'document'

/** 交给 DocumentViewer 渲染时所需的文件元信息 */
export interface CanvasDocumentMeta {
  filename: string
  mime?: string
  size?: number
}

/** 画布面板数据 */
export interface CanvasPanelData {
  type: WorkspaceCanvasType
  title: string
  content: string
  sourcePath?: string
  langName?: string
  runnable?: boolean
  compareContent?: string
  compareTitle?: string
  /** document / pdf 类型的渲染元信息 */
  documentMeta?: CanvasDocumentMeta
  /** 仅用于「下载原文件」，不参与 canSaveWorkspaceFile 判定 */
  downloadPath?: string
}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_canvas_types_contract.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 检查点**

汇报改动与测试结果。建议提交信息（**由你决定是否提交**）：

```
feat(canvas): 新增画布类型唯一来源 types/canvas.ts
```

---

## Task 2: 引入 file-viewer 依赖与 Vite 资产插件

**Files:**
- Modify: `frontend/package.json:11-36`（dependencies）、`:37-48`（devDependencies）
- Modify: `frontend/vite.config.ts:1-9`

- [ ] **Step 1: 添加运行时依赖**

在 `frontend/package.json` 的 `dependencies` 中，按字母序插入到 `"@codemirror/view"` 之后、`"@heroicons/vue"` 之前：

```json
    "@file-viewer/preset-standard": "^3.1.2",
    "@file-viewer/vue3": "^3.1.2",
```

- [ ] **Step 2: 添加构建期依赖**

在 `frontend/package.json` 的 `devDependencies` 中，作为第一项（字母序在 `@types/node` 之前）插入：

```json
    "@file-viewer/vite-plugin": "^3.1.2",
```

- [ ] **Step 3: 注册 Vite 插件**

将 `frontend/vite.config.ts` 全文替换为：

```ts
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import path from "path";
import { fileViewerRenderers } from "@file-viewer/vite-plugin";

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  return {
    // fileViewerRenderers 自动发现已安装的 @file-viewer/preset-*，
    // 并把 Worker / WASM / 字体资产复制到 public/file-viewer/（dev 与 build 均生效）
    plugins: [vue(), fileViewerRenderers({ copyAssets: true })],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    esbuild: {
      drop: mode === "production" ? ["console", "debugger"] : [],
    },
    server: {
      port: 5173,
      proxy: {
        // 开发环境：将所有 /api、/mcp、/docs、/openapi.json、/.well-known 请求代理到后端
        // 生产环境：前后端同端口 8001，不需要代理
        "^/(api|mcp|docs|openapi\\.json|\\.well-known)": {
          target: "http://localhost:8001",
          changeOrigin: true,
        },
      },
    },
  };
});
```

- [ ] **Step 4: 安装依赖**

**包管理器以项目现况为准：仓库跟踪的是 `frontend/package-lock.json`，即项目用的是 npm。**

Run: `cd frontend && npm install`
Expected: 安装成功；`package-lock.json` 更新并包含三个 `@file-viewer/*` 依赖；`frontend/node_modules/@file-viewer/` 下出现 `vue3`、`preset-standard`、`vite-plugin`。

⚠️ **不要用 pnpm。** 本计划初版误写为 `pnpm install`，副作用是工作区里多出 `frontend/pnpm-lock.yaml` 与 `frontend/pnpm-workspace.yaml` 两个**项目原本没有**的文件，并使 `frontend/node_modules` 变成 npm + pnpm 混合布局（顶层残留约 380 个 npm 扁平目录）。若已经误用 pnpm，请改用 npm 重装，并删除上述两个 pnpm 产物。

若该环境网络受限导致失败，请你在控制台执行同一条命令后再继续后续任务。

- [ ] **Step 5: 验证依赖可被解析**

Run: `cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b`
Expected: 错误数与既有基线一致（**62 条**，全部与本次改动无关）。本步只验证 `@file-viewer/*` 能被解析、`vite.config.ts` 的插件导入不报错——**不要**去修那 62 条既有错误。

- [ ] **Step 6: 检查点**

汇报依赖安装结果。建议提交信息（**由你决定是否提交**）：

```
feat(canvas): 引入 @file-viewer/vue3 + preset-standard 与 Vite 资产插件
```

---

## Task 3: 实现 DocumentViewer 组件

**Files:**
- Create: `frontend/src/components/embed/DocumentViewer.vue`
- Test: `tests/frontend/test_document_viewer_contract.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/frontend/test_document_viewer_contract.py`：

```python
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_document_viewer_lazy_loads_and_feeds_blob_instead_of_url():
    source = _source("frontend/src/components/embed/DocumentViewer.vue")

    assert "defineAsyncComponent" in source
    assert "import('@file-viewer/vue3')" in source
    assert ":file=" in source
    assert "responseType: 'blob'" in source


def test_document_viewer_avoids_object_urls_and_aborts_on_unmount():
    source = _source("frontend/src/components/embed/DocumentViewer.vue")

    assert "URL.createObjectURL" not in source
    assert "AbortController" in source
    assert "onUnmounted" in source
    assert "abort()" in source


def test_document_viewer_exposes_download_fallback_and_theme_bridge():
    source = _source("frontend/src/components/embed/DocumentViewer.vue")

    assert "fallback-download" in source
    assert "styleIsolation" in source
    assert "locale: 'auto'" in source
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_document_viewer_contract.py -v`
Expected: FAIL —`FileNotFoundError`

- [ ] **Step 3: 创建组件**

创建 `frontend/src/components/embed/DocumentViewer.vue`：

```vue
<script setup lang="ts">
import { computed, defineAsyncComponent, onUnmounted, ref, shallowRef, watch } from 'vue'
import axios from '@/utils/axios'
import type { CanvasDocumentMeta } from '@/types/canvas'

const props = defineProps<{
  /** 需要鉴权的文档地址；走 axios 获取，因此跨站嵌入场景同样可用 */
  url: string
  filename: string
  meta?: CanvasDocumentMeta
  theme: 'light' | 'dark'
}>()

const emit = defineEmits<{
  (e: 'error', message: string): void
  (e: 'fallback-download'): void
}>()

/**
 * 懒加载组件本体：file-viewer 及其 renderer / Worker / WASM 资产
 * 不进入首屏包，仅在真正打开文档时才加载。
 */
const FileViewer = defineAsyncComponent(async () => {
  const mod = await import('@file-viewer/vue3')
  return mod.FileViewer
})

type ViewerStatus = 'loading' | 'ready' | 'error'

const status = ref<ViewerStatus>('loading')
const errorMessage = ref('')
/** 用 shallowRef 保存 Blob：无需深层响应式，也避免 Vue 代理二进制对象 */
const fileBlob = shallowRef<Blob | null>(null)

let controller: AbortController | null = null

async function loadBlob() {
  controller?.abort()
  controller = new AbortController()

  status.value = 'loading'
  errorMessage.value = ''
  fileBlob.value = null

  if (!props.url) {
    status.value = 'error'
    errorMessage.value = '缺少文件地址'
    emit('error', errorMessage.value)
    return
  }

  try {
    const response = await axios.get(props.url, {
      responseType: 'blob',
      signal: controller.signal,
    })
    // 直接把 Blob 交给组件渲染：不调用 URL.createObjectURL，从根上避免对象 URL 泄漏
    fileBlob.value = response.data as Blob
    status.value = 'ready'
  } catch (err: any) {
    const canceled = err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED'
    if (canceled) return
    status.value = 'error'
    errorMessage.value = err?.response?.data?.detail || err?.message || '文档加载失败'
    emit('error', errorMessage.value)
  }
}

const viewerOptions = computed(() => ({
  theme: props.theme,
  locale: 'auto',
  // 默认 auto 会解析为 Shadow DOM，组件样式不会污染画布
  styleIsolation: 'auto' as const,
}))

const resolvedFilename = computed(() => props.meta?.filename || props.filename || 'document')
const resolvedType = computed(() => props.meta?.mime)

watch(() => props.url, loadBlob, { immediate: true })

onUnmounted(() => {
  controller?.abort()
  controller = null
})
</script>

<template>
  <div class="w-full h-full min-h-[500px] relative">
    <div
      v-if="status === 'loading'"
      class="flex h-full min-h-[500px] w-full flex-col items-center justify-center gap-3 text-xs text-gray-400 dark:text-gray-500"
    >
      <div class="h-6 w-6 animate-spin rounded-full border-2 border-gray-200 border-t-primary dark:border-gray-700"></div>
      <span>正在加载文档…</span>
    </div>

    <div
      v-else-if="status === 'error'"
      class="flex h-full min-h-[500px] w-full flex-col items-center justify-center gap-3 px-6 text-center"
    >
      <p class="text-xs text-gray-500 dark:text-gray-400">{{ errorMessage }}</p>
      <button
        type="button"
        class="rounded-lg bg-primary px-3 py-1.5 text-xs font-bold text-white hover:bg-primary/90"
        @click="emit('fallback-download')"
      >
        下载原文件
      </button>
    </div>

    <FileViewer
      v-else-if="fileBlob"
      :file="fileBlob ?? undefined"
      :filename="resolvedFilename"
      :type="resolvedType"
      :options="viewerOptions"
      class="h-full w-full"
    />
  </div>
</template>
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_document_viewer_contract.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 类型检查**

Run: `cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b`
Expected: **仍为 62 条既有错误、零新增**（本任务只新增组件，不改既有文件）。
实测结论：`:type="resolvedType"`（类型为 `string | undefined`）**不会**报错——`FileViewerComponentProps.type?: string` 本就是可选的，因此**无需**改成 `resolvedType ?? undefined`。

- [ ] **Step 6: 检查点**

汇报改动与类型检查结果。建议提交信息（**由你决定是否提交**）：

```
feat(canvas): 新增 DocumentViewer 组件封装 file-viewer
```

---

## Task 4: 分派层改造（util + composable）

> ⚠️ **必须与 Task 5 由同一执行者连续完成。**
>
> 本任务把 `CanvasPanelData` 换成含 `'document'` 的共享类型（`@/types/canvas`），而 `ChatCanvas.vue` 的 `props.data` 此刻仍是旧的**内联**联合类型（不含 `'document'`）。中间态下 `vue-tsc` 会因 props 赋值不兼容而报错——这是**已知且预期**的，由 Task 5 Step 1 消除。
>
> 因此：要么由一个 subagent 连续做完 Task 4 + Task 5，要么接受该中间态错误直到 Task 5 结束。**不要**把 Task 4 与 Task 5 交给两个并行 agent（后者的中间态会互相干扰判断）。
>
> 同理，Task 5 需要 `DocumentViewer.vue` 已存在（Task 3 完成）。

**Files:**
- Modify: `frontend/src/utils/workspaceFilePreview.ts:1-25, 63-70, 219-244`
- Modify: `frontend/src/composables/chat/useWorkspaceCanvas.ts:3-19, 127-180`
- Test: `tests/frontend/test_workspace_document_preview_contract.py`
- Modify: `tests/frontend/test_chat_shared_helpers_behavior.py:1113-1117`

- [ ] **Step 1: 写失败测试**

创建 `tests/frontend/test_workspace_document_preview_contract.py`：

```python
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_document_viewer_extensions_exclude_legacy_ppt():
    source = _source("frontend/src/utils/workspaceFilePreview.ts")

    assert "DOCUMENT_VIEWER_EXTENSIONS" in source
    # 6 种走预览的格式
    for ext in (".docx", ".doc", ".xlsx", ".xls", ".xlsm", ".pptx"):
        assert f"'{ext}'" in source, ext
    # 旧版二进制 .ppt 必须被排除在预览之外（需水印包，未装配）
    assert "DOCUMENT_VIEWER_EXTENSIONS.has" in source
    assert source.index("DOCUMENT_VIEWER_EXTENSIONS") < source.index("OFFICE_EXTENSIONS.has(ext)")


def test_office_and_pdf_no_longer_prefetch_blob_in_dispatch_layer():
    preview = _source("frontend/src/utils/workspaceFilePreview.ts")

    # document 类型只传鉴权 URL，不再预取 Blob
    assert "type: 'document'" in preview
    assert "documentMeta" in preview
    assert "downloadPath" in preview
    # PDF 分支不再走 blob 预取
    assert "payload.type === 'image' || payload.type === 'csv'" in preview


def test_workspace_canvas_payload_reuses_shared_types():
    composable = _source("frontend/src/composables/chat/useWorkspaceCanvas.ts")

    assert "from '@/types/canvas'" in composable or 'from "@/types/canvas"' in composable
    # canvas://file 分支里重复的 Office 下载逻辑已移除
    assert "link.download = filename" not in composable
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_workspace_document_preview_contract.py -v`
Expected: FAIL（`DOCUMENT_VIEWER_EXTENSIONS` 不存在）

- [ ] **Step 3: 改造 util 的类型与常量**

在 `frontend/src/utils/workspaceFilePreview.ts` 中，把第 1-25 行替换为：

```ts
import axios from '@/utils/axios'
import { copyToClipboard } from './clipboard'
import type { CanvasPanelData, WorkspaceCanvasType } from '@/types/canvas'

export type { CanvasPanelData, WorkspaceCanvasType } from '@/types/canvas'

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif'])
const TEXT_EXTENSIONS = new Set([
  '.txt', '.md', '.csv', '.json', '.sql', '.py', '.js', '.ts',
  '.sh', '.xml', '.html', '.css', '.yaml', '.yml', '.ini', '.conf',
  '.log', '.env', '.htm',
])
const OFFICE_EXTENSIONS = new Set([
  '.docx', '.doc', '.xlsx', '.xls', '.xlsm', '.pptx', '.ppt',
])

/**
 * 交给 DocumentViewer 渲染的格式。
 * 刻意不含旧版二进制 `.ppt`：它需要 @file-viewer/renderer-ppt，
 * 而该链会引入带内置水印、许可为 SEE LICENSE IN LICENSE 的 @file-viewer/ppt。
 */
export const DOCUMENT_VIEWER_EXTENSIONS = new Set([
  '.docx', '.doc', '.xlsx', '.xls', '.xlsm', '.pptx',
])

/** 交给 DocumentViewer 渲染时使用的 MIME，用于组件内部选择 renderer */
const DOCUMENT_VIEWER_MIME: Record<string, string> = {
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.doc': 'application/msword',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.xls': 'application/vnd.ms-excel',
  '.xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}

export function resolveDocumentViewerMime(name: string): string | undefined {
  const ext = getWorkspaceFileExtension(name)
  return DOCUMENT_VIEWER_MIME[ext]
}
```

- [ ] **Step 4: 让类型分派识别 document**

在 `frontend/src/utils/workspaceFilePreview.ts` 中，把 `resolveWorkspaceCanvasType`（原第 63-70 行）替换为：

```ts
export function resolveWorkspaceCanvasType(name: string): WorkspaceCanvasType {
  const lower = name.toLowerCase()
  if (lower.endsWith('.csv')) return 'csv'
  if (lower.endsWith('.pdf')) return 'pdf'
  if (/\.(jpe?g|png|gif|webp)$/.test(lower)) return 'image'
  if (lower.endsWith('.html') || lower.endsWith('.htm')) return 'html'
  if (DOCUMENT_VIEWER_EXTENSIONS.has(getWorkspaceFileExtension(name))) return 'document'
  return 'code'
}
```

- [ ] **Step 5: 改 openWorkspaceFileInCanvas 的 Office / PDF 分支**

在 `frontend/src/utils/workspaceFilePreview.ts` 中，把原第 219-244 行（Office 下载分支 + pdf/image/csv Blob 分支）替换为：

```ts
    if (DOCUMENT_VIEWER_EXTENSIONS.has(ext)) {
      // 只传鉴权 URL：Blob 由 DocumentViewer 用 axios 获取，
      // 避免 Blob 进入响应式状态，也避免在此处创建对象 URL。
      onOpen({
        type: 'document',
        title: name,
        content: resolvedUrl,
        documentMeta: { filename: name, mime: resolveDocumentViewerMime(name) },
        downloadPath: path,
      })
      return
    }

    // 旧版二进制 .ppt 仍需走下载（见 DOCUMENT_VIEWER_EXTENSIONS 注释）
    if (OFFICE_EXTENSIONS.has(ext)) {
      const response = await axios.get(resolvedUrl, { responseType: 'blob' })
      const filename = name || 'download'
      const blobUrl = URL.createObjectURL(response.data)
      const link = document.createElement('a')
      link.href = blobUrl
      link.download = filename
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(blobUrl)
      showToast(`已开始下载 ${filename}`, 'success')
      return
    }

    if (payload.type === 'pdf') {
      // PDF 同样交给 DocumentViewer，只传 URL，不预取 Blob
      onOpen({
        type: 'pdf',
        title: name,
        content: resolvedUrl,
        documentMeta: { filename: name, mime: 'application/pdf' },
        downloadPath: path,
      })
      return
    }

    if (payload.type === 'image' || payload.type === 'csv') {
      const response = await axios.get(resolvedUrl, { responseType: 'blob' })
      const blobUrl = URL.createObjectURL(response.data)
      if (activeBlobUrlRef) activeBlobUrlRef.value = blobUrl
      onOpen({
        type: payload.type,
        title: payload.title,
        content: blobUrl,
      })
      return
    }
```

- [ ] **Step 6: 改造 composable 的类型与 canvas://file 分支**

在 `frontend/src/composables/chat/useWorkspaceCanvas.ts` 中，把第 3-19 行替换为：

```ts
import {
  openWorkspaceFileInCanvas,
  isSameWorkspacePreviewPath,
  resolveWorkspaceScriptLanguage,
  resolveWorkspaceCanvasType,
  resolveDocumentViewerMime,
  shouldAttachWorkspaceSourcePath,
  DOCUMENT_VIEWER_EXTENSIONS,
} from "@/utils/workspaceFilePreview";
import type { CanvasPanelData } from "@/types/canvas";

export type { WorkspaceCanvasType } from "@/types/canvas";

/** 打开画布的输入负载：与画布面板数据同构 */
export type WorkspaceCanvasPayload = CanvasPanelData;
```

- [ ] **Step 7: 移除 canvas://file 分支中重复的 Office 下载逻辑**

在 `frontend/src/composables/chat/useWorkspaceCanvas.ts` 的 `canvas://file` 分支内，把原第 136-155 行（`isOfficeFile` 下载块 + pdf/image/csv Blob 块）替换为：

```ts
        const filename = payload.title || filePath.split("/").pop() || "文件预览";
        const canvasType = resolveWorkspaceCanvasType(filename);
        if (DOCUMENT_VIEWER_EXTENSIONS.has(getWorkspaceFileExtension(filename)) || canvasType === "pdf") {
          // Office 与 PDF 只传鉴权 URL，Blob 交给 DocumentViewer 获取
          canvasData.value = {
            type: canvasType === "pdf" ? "pdf" : "document",
            title: filename,
            content: resolvedUrl,
            documentMeta: {
              filename,
              mime:
                canvasType === "pdf"
                  ? "application/pdf"
                  : resolveDocumentViewerMime(filename),
            },
            downloadPath: filePath,
          };
        } else if (canvasType === "image" || canvasType === "csv") {
          const response = await axios.get(resolvedUrl, { responseType: "blob" });
          const blobUrl = URL.createObjectURL(response.data);
          activeBlobUrl.value = blobUrl;
          canvasData.value = { type: canvasType, title: filename, content: blobUrl };
        } else {
          const content = await axios.get(resolvedUrl).then((response) => response.data);
          const scriptLanguage = resolveWorkspaceScriptLanguage(filename);
          canvasData.value = {
            type: canvasType,
            title: filename,
            content,
            sourcePath: shouldAttachWorkspaceSourcePath(filePath, filename) ? filePath : undefined,
            langName: scriptLanguage || undefined,
            runnable: !!scriptLanguage,
          };
        }
```

同时在该文件顶部补齐 `getWorkspaceFileExtension` 的引入——把它加入第 3-11 行的 import 列表：

```ts
  getWorkspaceFileExtension,
```

- [ ] **Step 8: 补全行为测试的模块 mock**

`tests/frontend/test_chat_shared_helpers_behavior.py` 的 mock 缺少新引入的导出，会让该测试在不完整 mock 下运行。把原第 1113-1117 行替换为：

```python
  if (id === '@/utils/workspaceFilePreview') return {
    isSameWorkspacePreviewPath: (left, right) => left === right,
    shouldAttachWorkspaceSourcePath: () => true,
    resolveWorkspaceScriptLanguage: () => null,
    resolveWorkspaceCanvasType: () => 'code',
    resolveDocumentViewerMime: () => undefined,
    getWorkspaceFileExtension: name => {
      const parts = name.split('.');
      return parts.length < 2 ? '' : '.' + parts.pop().toLowerCase();
    },
    DOCUMENT_VIEWER_EXTENSIONS: new Set(['.docx', '.xlsx', '.pptx']),
    openWorkspaceFileInCanvas: async options => options.onOpen({ type: 'code', title: options.name, content: 'preview' })
  };
```

- [ ] **Step 9: 运行测试，确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_workspace_document_preview_contract.py tests/frontend/test_chat_shared_helpers_behavior.py -v`
Expected: PASS

- [ ] **Step 10: 类型检查（预期出现中间态错误，属正常）**

Run: `cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b`
Expected: 错误数从 62 条**升到 64 条**。新增的 2 条就是本任务的中间态错误，且报在**调用方**而非 ChatCanvas 自身（已实测确认）：

- `src/views/AgentDebug.vue(5079,10)`
- `src/views/EmbedChat.vue(1439,8)`

内容均为 `Type 'WorkspaceCanvasType' is not assignable to type '"code" | "html" | "image" | "mermaid" | "csv" | "pdf" | "compare"'`——因为这两个视图把 `canvasData` 传给了 `ChatCanvas` 的旧内联 props 类型。该错误由 Task 5 Step 1 消除。

**除这 2 条外不应有其他新增错误**——若出现其他错误，必须在本步立即修正。

- [ ] **Step 11: 检查点**

建议提交信息（**由你决定是否提交**）：

```
feat(canvas): Office/PDF 改为传鉴权 URL 并由 DocumentViewer 渲染
```

---

## Task 5: ChatCanvas 渲染分支与下载动作

**Files:**
- Modify: `frontend/src/components/embed/ChatCanvas.vue:1-13, 18-30, 117-143, 1159-1191`
- Modify: `tests/frontend/test_code_execution_canvas_contract.py:64`

- [ ] **Step 1: 改类型引入与 props**

在 `frontend/src/components/embed/ChatCanvas.vue` 中，把第 11 行之后新增两行 import：

```ts
import DocumentViewer from '@/components/embed/DocumentViewer.vue';
import type { CanvasPanelData } from '@/types/canvas';
import { useDarkThemeFlag } from '@/composables/useDarkThemeFlag';
```

并把第 20-30 行的 `data` 内联类型替换为：

```ts
    data: CanvasPanelData | null;
```

- [ ] **Step 2: 补主题与文档预览的 computed**

在 `frontend/src/components/embed/ChatCanvas.vue` 中，紧接 `resolvedContent` computed（原第 641-663 行）之后新增：

```ts
const isDarkTheme = useDarkThemeFlag();
/** file-viewer 的主题取值，跟随项目明暗主题 */
const canvasTheme = computed<'light' | 'dark'>(() => (isDarkTheme.value ? 'dark' : 'light'));

/** Office 文档与 PDF 由 DocumentViewer 统一渲染 */
const isDocumentPreview = computed(
  () => props.data?.type === 'document' || props.data?.type === 'pdf',
);
```

（已核实：`useDarkThemeFlag()` 直接返回 `Ref<boolean>`，内部以 `MutationObserver` 跟随 `<html class="dark">`，见 `frontend/src/composables/useDarkThemeFlag.ts:14`。）

- [ ] **Step 3: 调整下载动作**

在 `frontend/src/components/embed/ChatCanvas.vue` 的 `downloadFile` 中，把原第 121-131 行的判断块替换为：

```ts
  // 文档类优先按工作区路径重新取 Blob 下载：
  // 其 content 是鉴权 URL（PDF 为 inline），直接 a.href 会退化成新窗口打开
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
  }

  if (props.data.type === 'image' || props.data.type === 'csv') {
    const a = document.createElement('a');
    a.href = content;
    a.download = props.data.title || 'download';
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    return;
  }
```

同时把 `downloadWorkspaceFile` 加入第 11 行的 import 列表。

（已核实：会话 id 由 `ChatCanvas.vue:529-533` 的 `resolveConversationId()` 解析——`props.conversationId` → `localStorage` 兜底 → `''`。该函数是箭头函数常量，`downloadFile` 在调用时引用它不存在先后顺序问题。）

- [ ] **Step 4: 插入渲染分支**

在 `frontend/src/components/embed/ChatCanvas.vue` 中，把原第 1160 行的：

```html
        <template v-if="isHtmlContent">
```

替换为：

```html
        <!-- Office 文档 / PDF：由 DocumentViewer 统一渲染 -->
        <template v-if="isDocumentPreview">
          <DocumentViewer
            :url="resolvedContent"
            :filename="data?.documentMeta?.filename || data?.title || 'document'"
            :meta="data?.documentMeta"
            :theme="canvasTheme"
            @fallback-download="downloadFile"
          />
        </template>

        <template v-else-if="isHtmlContent">
```

- [ ] **Step 5: 迁移被搬家的类型断言**

`"sourcePath?: string"` 已从 ChatCanvas 移到 `types/canvas.ts`。修改 `tests/frontend/test_code_execution_canvas_contract.py` 的第 57-64 行：

```python
def test_workspace_source_files_keep_script_execution_metadata():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")
    preview = _source("frontend/src/utils/workspaceFilePreview.ts")
    canvas_types = _source("frontend/src/types/canvas.ts")

    assert "props.data.runnable === true" in canvas
    assert "resolveWorkspaceScriptLanguage" in preview
    assert "runnable: !!scriptLanguage" in preview
    # sourcePath 已收敛到类型唯一来源
    assert "sourcePath?: string" in canvas_types
    assert "CanvasPanelData" in canvas
```

- [ ] **Step 6: 运行相关测试**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_code_execution_canvas_contract.py tests/frontend/test_canvas_content_save_contract.py -v`
Expected: PASS

- [ ] **Step 7: 类型检查（本步是 Task 4+5 的真正验收点）**

Run: `cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b`
Expected: **错误数回到 62 条**，且 `AgentDebug.vue` / `EmbedChat.vue` 那两条 `WorkspaceCanvasType` 错误消失。
- 若仍为 64 条 → 本任务没改干净，继续排查 `ChatCanvas.vue` 的 props 类型；
- 若出现**第三种**错误（既不属于那 62 条基线、也不是上述 2 条）→ 是本次改动引入的，必须修掉。

- [ ] **Step 8: 检查点**

建议提交信息（**由你决定是否提交**）：

```
feat(canvas): 画布新增 Office/PDF 预览分支并修正文档下载
```

---

## Task 6: ChatCanvas 观感修正与死代码清理

> **本任务是 Task 4+5 执行时发现的计划缺口**（执行者报告，我已现场逐条复核确认）：引入 `document` 类型后，`ChatCanvas.vue` 里三处按旧类型枚举写死的分支没有跟上，会让 Office 文档在界面上显示**错误信息**；另有一处 PDF 分支被新的前置分支遮蔽成了死代码。
> 这四处**没有任何测试依赖**（已 grep 确认），但用户能直接看到，必须修。

**Files:**
- Modify: `frontend/src/components/embed/ChatCanvas.vue`（徽标配色、徽标文案、下载按钮文案、删除死代码分支）
- Test: `tests/frontend/test_canvas_document_badges_contract.py`（新建）

- [ ] **Step 1: 写失败测试**

创建 `tests/frontend/test_canvas_document_badges_contract.py`：

```python
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canvas_badge_labels_document_type_instead_of_falling_back_to_code():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    # 徽标文案：document 必须有自己的标签，不能落到 'Code' 兜底
    assert "data?.type === 'document' ? 'Office'" in canvas
    # 徽标配色：document 必须有自己的色，不能落到灰色兜底
    assert "data?.type === 'document' ? 'bg-teal-100" in canvas


def test_canvas_download_button_treats_document_as_file_download():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    # Office 文档要显示「下载文件」，而不是「下载数据」
    assert (
        "data?.type === 'image' || data?.type === 'pdf' || data?.type === 'document'"
        in canvas
    )


def test_canvas_has_no_shadowed_pdf_iframe_branch():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    # DocumentViewer 已接管 pdf，旧的 iframe 分支被前置条件遮蔽，属死代码
    assert "PDF Viewer Sandbox" not in canvas
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_canvas_document_badges_contract.py -v`
Expected: FAIL（3 条断言均不满足）

- [ ] **Step 3: 给徽标补 document 分支**

在 `frontend/src/components/embed/ChatCanvas.vue` 中：

**配色** `:class` 链里，在 `data?.type === 'compare' ? 'bg-purple-100 ...' :` 这一行**之后**插入一行：

```js
                data?.type === 'document' ? 'bg-teal-100 dark:bg-teal-900/30 text-teal-600 dark:text-teal-400' :
```

**文案**插值里，在 `data?.type === 'compare' ? 'File Diff' :` 这一行**之后**插入一行：

```js
                data?.type === 'document' ? 'Office' :
```

（`teal` 是唯一未被占用的主色——现有分支已用 blue/html、emerald/image、rose/pdf、amber/csv、indigo/mermaid、purple/compare、sky/markdown、gray/兜底。）

- [ ] **Step 4: 修下载按钮文案**

把这一行：

```html
          <span>{{ data?.type === 'image' || data?.type === 'pdf' ? '下载文件' : '下载数据' }}</span>
```

替换为：

```html
          <span>{{ data?.type === 'image' || data?.type === 'pdf' || data?.type === 'document' ? '下载文件' : '下载数据' }}</span>
```

- [ ] **Step 5: 删除被遮蔽的 PDF 死代码分支**

把这一整段删除：

```html
        <!-- PDF Viewer Sandbox -->
        <template v-else-if="data?.type === 'pdf'">
          <div class="w-full h-full bg-white dark:bg-gray-950 rounded-xl overflow-hidden shadow-inner border border-gray-100 dark:border-gray-800 min-h-[500px]">
            <iframe
              :src="resolvedContent"
              class="w-full h-full border-none"
            ></iframe>
          </div>
        </template>
```

**理由**：Task 5 已在渲染链最前面插入 `<template v-if="isDocumentPreview">`（`isDocumentPreview` = `document || pdf`），所以这个 `v-else-if="data?.type === 'pdf'"` 永远不可达。已 grep 确认无任何测试依赖 `PDF Viewer Sandbox` 或该 iframe。

- [ ] **Step 6: 运行测试，确认通过**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_canvas_document_badges_contract.py -v`
Expected: PASS（3 passed）

- [ ] **Step 7: 类型检查**

Run: `cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b`
Expected: **仍为 62 条既有错误、零新增**。

- [ ] **Step 8: 检查点**

建议提交信息（**由你决定是否提交**）：

```
fix(canvas): Office 文档徽标与下载文案不再回落，并清理被遮蔽的 PDF 分支
```

---

## Task 7: 收敛剩余定义并收尾验证

**Files:**
- Modify: `frontend/src/components/MessageRenderer.vue:84-90`
- Modify: `tests/frontend/test_canvas_types_contract.py`
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 收敛 MessageRenderer 的类型**

在 `frontend/src/components/MessageRenderer.vue` 中，把第 84-90 行的内联 `open-canvas` 类型替换为引用共享类型。先在 `<script setup>` 的 import 区加入：

```ts
import type { CanvasPanelData } from '@/types/canvas';
```

再把 emit 声明改为（保留 `sourcePath` 为可选的既有行为）：

```ts
  (e: 'open-canvas', payload: Omit<CanvasPanelData, 'sourcePath'> & { sourcePath?: string }): void;
```

- [ ] **Step 2: 补消费者一致性断言**

在 `tests/frontend/test_canvas_types_contract.py` 末尾追加：

```python
def test_canvas_consumers_import_types_instead_of_redeclaring():
    consumers = {
        "ChatCanvas.vue": "frontend/src/components/embed/ChatCanvas.vue",
        "useWorkspaceCanvas.ts": "frontend/src/composables/chat/useWorkspaceCanvas.ts",
        "workspaceFilePreview.ts": "frontend/src/utils/workspaceFilePreview.ts",
        "MessageRenderer.vue": "frontend/src/components/MessageRenderer.vue",
    }

    for label, path in consumers.items():
        source = _source(path)
        assert (
            "from '@/types/canvas'" in source or 'from "@/types/canvas"' in source
        ), label
```

- [ ] **Step 3: 运行前端契约测试全量**

Run: `pytest --confcutdir=tests/frontend tests/frontend -q`
Expected: 全部通过。若有既有失败，先确认是否为本次改动引入（用 `git stash` 前后对比）。

- [ ] **Step 4: 类型检查（全流程最终类型验收点）**

Run: `cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b`
Expected: **62 条既有错误、零新增**——即与本次改动开始前完全一致。若多于 62 条，说明本次改动引入了类型错误，必须修掉。

- [ ] **Step 5: 登记验证清单**

`tests/CHECKLIST.md` 是**表格**（列为：特性名 / 涉及文件 / 验证与设计说明 / 状态 / 日期），**不是 checkbox 列表**。在表格末尾追加一行：

| 画布文档预览接入 file-viewer (Canvas Document Preview) | `frontend/src/types/canvas.ts`（新增）、`frontend/src/components/embed/DocumentViewer.vue`（新增）、`frontend/src/utils/workspaceFilePreview.ts`、`frontend/src/composables/chat/useWorkspaceCanvas.ts`、`frontend/src/components/embed/ChatCanvas.vue`、`frontend/src/components/MessageRenderer.vue`、`frontend/package.json`、`frontend/vite.config.ts`、`tests/frontend/test_canvas_types_contract.py`（新增）、`tests/frontend/test_document_viewer_contract.py`（新增）、`tests/frontend/test_workspace_document_preview_contract.py`（新增）、`docs/superpowers/specs/2026-10-08-canvas-document-preview-design.md`（新增）、`docs/superpowers/plans/2026-10-08-canvas-document-preview.md`（新增）、`tests/CHECKLIST.md` | **用户需求**：画布能直接预览 Office 文档与 PDF，而不再只能下载。**关键澄清**：并非「所有文档预览」都换组件——保留 `code`（可编辑 + 可执行）、`html`（效果/源码双 tab 编辑）、`csv`（透视表）、`mermaid`、`compare`（AI 分析差异）这些该组件做不到的自研能力，只把「目前看不了的格式」交给它。**选型**：`@file-viewer/vue3` + `@file-viewer/preset-standard`；**不用 `preset-office`**（其传递依赖 `renderer-presentation → renderer-ppt → @file-viewer/ppt` 带内置水印且许可为 `SEE LICENSE IN LICENSE`），**不用 `preset-all`**（引入 `renderer-cad → @flyfish-dev/cad-viewer → dwf-viewer`，AGPL-3.0-only）。**实现**：组件以 `File \| Blob \| ArrayBuffer` 为入参，故由 `DocumentViewer.vue` 用 axios 取 Blob 后直接喂给组件——不创建 object URL（避免泄漏）、卸载即 `abort`。**后端零改动**的四条依据：Office 扩展名本就在独立的 `OFFICE_PREVIEW_EXTENSIONS` 中（不走被 `/write` 复用的文本白名单）、Blob 全量获取不需要 Range（绕开 GZip 污染 206 的问题）、`Content-Disposition: attachment` 不影响 axios、跨站嵌入的鉴权由 axios 自动携带凭据（绕开 `SameSite=lax` Cookie 不下发的 401）。**已知取舍**：PDF 走 Blob 故拿不到 Range 分片，大文件仍为全量加载；旧版二进制 `.ppt` 需要水印包，故保持下载。**验证**：后端零改动；`pytest --confcutdir=tests/frontend` 全绿；`NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b` 错误数回到 **62 条基线**（零新增）。**未执行** `./dev.sh`、未提交 git。**手动验证清单（需用户在控制台跑 `./dev.sh` 后确认）**：`.docx`/`.xlsx`/`.pptx` 画布内渲染含工具栏、`.doc`/`.xls`/`.xlsm` 可渲染、`.ppt` 走下载不报错、`.pdf` 不劣于浏览器原生、`.csv`/`.png`/`.py`/`.md`/mermaid/compare 无回归、`.py` 仍可执行且 `.html` 仍可编辑保存（确认未误开编辑态）、暗色主题跟随、资产缺失时出现降级提示与下载按钮、跨站嵌入 iframe 可渲染、下载按钮对 Office/PDF 真实触发下载。 | ✅ 画布内可直接预览 Office 6 种格式与 PDF，`.ppt` 保持下载，既有自研渲染能力无回归；纯前端改动、后端零改动 | 2026-10-08 |

按本项目 `CHECKLIST.md` 的既有惯例，对关键守卫逐条做**变异反向验证**（例如：把 `.ppt` 加回 `DOCUMENT_VIEWER_EXTENSIONS`、把 `:file` 改回 `:url`、删掉 `abort()`、把 `document` 分支去掉），确认对应契约立刻失败，随后字节级还原并复跑通过。

> ⚠️ **变异验证陷阱（Task 3 执行时已实际踩到）**：项目契约测试是**纯源码子串匹配，注释也算被测内容**。若在 `DocumentViewer.vue` 的注释里写出字面量 `URL.createObjectURL`，会直接让 `test_document_viewer_avoids_object_urls_and_aborts_on_unmount` 失败，而代码里其实并没有调用它。做变异与代码评审时注意区分「注释里提到该 API」与「代码里调用该 API」——守卫只认字符串。这也是 Task 3 把注释写成「不创建对象 URL」而非「不调用 `URL.createObjectURL`」的原因。

- [ ] **Step 6: 汇报（不提交）**

汇报内容需含：
1. 改动文件清单与每处改动摘要；
2. `pytest --confcutdir=tests/frontend` 与 `NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b` 的结果（后者须为 **62 条既有错误、零新增**）；
3. 需要用户在控制台执行的验证：`./dev.sh` 后按 Step 5 的清单逐项确认；
4. 明确说明**未执行** `git commit` 与 `./dev.sh`。

---

## Task 8: 修复审查中发现的同类缺陷（执行中增补）

> 本任务**不在原始计划中**，是 6 个任务完成后**代码审查阶段发现的新缺陷**。我按「按旧类型枚举写死、没跟上新类型」这一模式系统排查，共发现 3 处遗漏（根因分析见文末「遗留架构问题」）。

**Files:**
- Modify: `frontend/src/utils/workspaceFilePreview.ts`（新增 `shouldDownloadInsteadOfPreview`）
- Modify: `frontend/src/composables/chat/useWorkspaceCanvas.ts`（`canvas://file` 分支补 `.ppt` 下载拦截）
- Modify: `frontend/src/components/embed/ChatCanvas.vue`（复制按钮文案、`resolvedContent` 一致性）
- Test: `tests/frontend/test_canvas_legacy_ppt_download_contract.py`（新建，3 条）

**⚠️ 关键约束（此约束是踩坑后补的）**：`.ppt` 的下载**必须复用已有的 `downloadWorkspaceFile()` helper**，**不得**在 composable 内联 `link.download = filename`。后者会违反 Task 4 的既有契约——`test_workspace_document_preview_contract.py` 中「canvas://file 分支里重复的 Office 下载逻辑已移除」这条断言。计划初稿误要求内联，执行时被并发任务实测报错，随后改为复用 helper（方案 b），两条契约同时满足。

**结果**：3 处缺陷按 TDD 修复；期间发现并解决一次契约互斥。

---

## 自检结果

**1. 规格覆盖**

| 规格章节 | 对应任务 |
| --- | --- |
| 4.1 `DocumentViewer.vue` | Task 3 |
| 4.2 `types/canvas.ts` 类型唯一来源 | Task 1、Task 5 Step 1、Task 7 |
| 4.3 `vite.config.ts` 插件 | Task 2 |
| 5 数据流（Office/PDF 只传 URL、不建对象 URL、`.ppt` 排除） | Task 4 Step 3-7、Task 5 Step 4 |
| 6 错误处理与三层降级 | Task 3 Step 3（`loading/ready/error` 三态 + `fallback-download`） |
| 6 下载路径连带调整 | Task 5 Step 3 |
| 7 实现点文件清单 | 全部任务 |
| 8 新增契约测试 | Task 1、3、4、6 |
| 8 现有测试同步 | Task 4 Step 8、Task 5 Step 5 |
| 8 手动验证清单 | Task 7 Step 5 |
| 6 错误处理与降级（界面回落） | Task 6（徽标/文案不再回落 + 死代码清理，执行中新发现） |
| 9 风险（PDF 无 Range、体积、`.ppt` 体验） | 已在规格记录，无实现动作 |

**2. 占位符扫描：** 无 TODO / TBD /「类似 Task N」/「添加适当错误处理」等表述，每个改动步骤均给出完整代码。

**3. 类型一致性核对：**

- `DOCUMENT_VIEWER_EXTENSIONS` / `resolveDocumentViewerMime` / `getWorkspaceFileExtension` 在 Task 4 定义，Task 4 Step 6-7 与 Task 4 Step 8 的 mock 中名称一致；
- `CanvasPanelData.documentMeta` / `downloadPath` 在 Task 1 定义，Task 4、Task 5 使用同名属性；
- `DocumentViewer` 的 props（`url` / `filename` / `meta` / `theme`）与 emits（`error` / `fallback-download`）在 Task 3 定义、Task 5 Step 4 调用一致；
- `canvasTheme` / `isDocumentPreview` 在 Task 5 Step 2 定义、Step 4 使用一致；
- `downloadWorkspaceFile({ path, name, conversationId, showToast })` 与既有签名（`workspaceFilePreview.ts:274-279`）一致。

**4. 关键接口均已按现场核实（非推测）：**

- `useDarkThemeFlag()` 直接返回 `Ref<boolean>`，用 `MutationObserver` 跟随 `<html class="dark">`（`frontend/src/composables/useDarkThemeFlag.ts:14`）；
- `ChatCanvas` 的会话 id 由 `resolveConversationId()` 解析（`ChatCanvas.vue:529-533`）；
- `FileViewerComponentProps.type?: string` 为可选，传 `undefined` 合法；
- 组件具名导出为 `FileViewer`（`@file-viewer/vue3` 的 `index.d.ts` 含 `export { FileViewer }`）；
- 组件事件列表**不含 `error`**（只有 `load-*` / `unload-*` / `operation-*` / `search-change` / `location-change` / `zoom-change` / `view-state-change` / `fit-change` / `theme-change`），故错误处理只覆盖 Blob 获取层——与规格第 6 节一致；
- `@file-viewer/preset-standard` 的依赖树已逐包核对，无 AGPL、无 `SEE LICENSE IN LICENSE`。

---

## 遗留架构问题（执行中发现，建议后续单独处理）

### 问题：画布类型分派逻辑在两处重复实现，已导致行为漂移

同一套「文件名 → 画布类型 / 加载方式」的分派逻辑存在**两份独立实现**：

| | 路径 A | 路径 B |
| --- | --- | --- |
| 入口 | `openWorkspaceFileInCanvas()`（`frontend/src/utils/workspaceFilePreview.ts:221`） | `handleOpenCanvas()` 的 `canvas://file` 分支（`frontend/src/composables/chat/useWorkspaceCanvas.ts:124`） |
| 触发场景 | 工作区文件浏览器里点击文件 | 聊天消息里的 `canvas://file` 链接 |
| 类型判定方式 | 直接用 `OFFICE_EXTENSIONS` / `DOCUMENT_VIEWER_EXTENSIONS` 判断 | 走 `resolveWorkspaceCanvasType()` |

**已由此产生的缺陷**（均在本次特性开发中暴露）：

1. **`.ppt` 乱码（严重）**——路径 A 保留了 `OFFICE_EXTENSIONS` 下载分支，路径 B 改造时没有对应分支，使 `.ppt` 掉进文本兜底分支、被当作文本读取。→ Task 8 修复。
2. **界面文案回落**——`ChatCanvas.vue` 内多处按旧类型枚举写死的三元链未跟上新 `document` 类型：徽标显示「Code」、下载按钮显示「下载数据」、复制按钮显示「复制代码」。→ Task 6 + Task 8 修复。
3. **`canPreviewWorkspaceFile()` 守卫缺失（本次未修，待决策）**——路径 A 在 `:224` 用该守卫拦截 `.zip` / `.exe` 等不可预览类型；路径 B 没有守卫，任何 `canvas://file` 链接都会进入加载流程，未知扩展名会落到文本分支。**未修的原因**：加守卫属于行为变更（原本无扩展名的文本文件也能打开），需在实测后再决定，不宜与止血修复混在一起。
4. **契约守卫漏洞：`.ppt` 可被静默加回预览集合（已修）**——`test_document_viewer_extensions_exclude_legacy_ppt` 名字说的是「排除 `.ppt`」，实际只断言 6 个格式存在，且用的是 `f"'{ext}'" in source`：检查的是**整个文件**，而 `.ppt` 本来就在 `OFFICE_EXTENSIONS` 里。变异反向验证实测：把 `.ppt` 加回 `DOCUMENT_VIEWER_EXTENSIONS` 后 **83 passed、零失败**——即带内置水印、许可受限的 `@file-viewer/ppt` 渲染链可被静默引入而无人报警。**已修**：先切出 `DOCUMENT_VIEWER_EXTENSIONS` 的 Set 字面量再检查——`source.split("export const DOCUMENT_VIEWER_EXTENSIONS = new Set([")[1].split("])")[0]`，断言 `"'.ppt'" not in preview_block`（用引号界定，避免命中 `.pptx`）。修复后重做变异：立刻变红（`:30` AssertionError）；字节级还原（sha256 `bc47fd8478cebac4` 一致）后复跑 6 passed。

**为什么不再继续逐处打补丁**：这符合「同一根因在不同位置反复出现」的架构问题特征——**每修一处，就会在另一处发现同类遗漏**。本次已修 2 轮（Task 6 修 4 处、Task 8 修 3 处），继续打补丁无法阻止下次新增类型时再现。

**建议的收敛方案（Task 9，待实测通过后再做）**：把分派逻辑收敛为**单一纯函数**，两条路径都只调用它：

```ts
export type WorkspacePreviewPlan =
  | { kind: 'document'; type: 'document' | 'pdf'; url: string; mime?: string }
  | { kind: 'download' }
  | { kind: 'blob'; type: 'image' | 'csv' }
  | { kind: 'text'; type: WorkspaceCanvasType }

export function planWorkspacePreview(name: string): WorkspacePreviewPlan | null
```

由它统一负责「是否可预览（守卫）→ 走哪种加载方式」，路径 A / B 只保留各自的副作用（`onOpen` vs 写 `canvasData`）。这样新增格式时只改一处，两条路径不可能再不一致。

**暂不执行的理由**：本特性的**基本渲染能力尚未经过任何真实运行验证**（`DocumentViewer` 能否真的解析 Office 文件、资产是否正确复制、跨站鉴权是否走通，全部未知）。在基本功能未验证前重构分派层，会让「重构引入的问题」与「原有未验证的问题」混在一起、难以定位。

**建议顺序**：用户用 `./dev.sh` 实测通过 → 再做 Task 9 收敛。
