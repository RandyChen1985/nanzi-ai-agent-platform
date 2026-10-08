# 画布文本格式默认走 file-viewer 预览 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让画布/工作空间里的文本与代码格式（`.json`/`.ts`/`.txt`/`.vue`/`.log` 等）默认由 file-viewer 渲染，并保留一个「源代码」标签页用于编辑，从而让只读文本也能获得 file-viewer 的搜索/打印/缩放能力。

**Architecture:** 判定用**零依赖纯函数** `shouldPreviewWithFileViewerInCanvas(name)`（只看扩展名，静态可预测），放在既有的 `documentPreviewFormats.ts` 里以便 Node 直跑行为测试。渲染上给 `DocumentViewer` 增加可选 `content` 属性，使其能直接接收文本而不发网络请求——画布对文本格式本来就已取到内容，因此不增加任何请求。画布复用既有的 `activeTab`（`preview`/`code`）机制，把当前只服务于 HTML/Markdown 的标签页扩展到文本格式。

**Tech Stack:** Vue 3 + TypeScript + Vite 7；file-viewer v3.1.2（`@file-viewer/vue3`）；pytest 前端契约测试 + `node --experimental-strip-types` 行为测试。

---

## 背景与硬约束（实施者必读）

### 为什么不是所有格式都交给 file-viewer

有三类文本格式**必须在画布侧渲染**，各有硬原因（均已实证）：

| 格式 | 为什么不能交给 file-viewer |
|---|---|
| `.md` / `.markdown` | 画布 markdown 渲染器带 **mermaid + ECharts**；file-viewer 的 markdown 依赖只有 `marked` + `dompurify`，**没有 ECharts 能力**，mermaid 也需注册 `registerFileViewerMermaidLoader`（全仓当前未注册）。交出去会让 AI 生成的图表报告退化成静态文字 |
| `.html` / `.htm` | 画布用 `sandbox="allow-scripts"` iframe 渲染**片段**；file-viewer 把 `.html` 当 `code` 渲染器，**只显示源码** |
| `.py` / `.sh` / `.bash` | 画布支持**脚本执行 + 实时输出**（`useCodeExecution`），file-viewer 只读 |

### 已有常量语义（不要混用）

- `OFFICE_PREVIEW_EXTENSIONS`（6 种）：画布认为「文档预览」= Office，走 `'document'` 类型。
- `DOCUMENT_VIEWER_EXTENSIONS`（56 种）：**RAG 抽屉 / 知识库弹窗**的语义（Office + markdown + 代码）。**画布分派禁止直接使用它**（历史事故：直接用它导致 `.md`/`.txt` 徽章显示 `OFFICE`）。
- 本次新增的 `shouldPreviewWithFileViewerInCanvas` 是**画布语义**，内部基于 `DOCUMENT_VIEWER_EXTENSIONS` 做减法。

### 测试与类型检查命令

```bash
# 前端契约测试（必须带 --confcutdir）
.venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend

# 单个文件
.venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend/test_xxx.py

# 行为测试直跑（调试用）
node --experimental-strip-types --no-warnings tests/frontend/canvas_text_preview.mjs

# 类型检查（必须用 -b；--noEmit 是空操作）
cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b
```

**基线：`vue-tsc -b` 全仓 62 条错误，必须保持 62 条且逐行相同。**

### 不要做的事

- 不要执行 `git commit`（由用户决定）。计划里的 commit 步骤仅作占位，除非用户在当次对话明确要求。
- 不要运行 `./dev.sh` 或任何构建/部署脚本。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `frontend/src/utils/documentPreviewFormats.ts` | 修改 | 新增 `CANVAS_NATIVE_TEXT_EXTENSIONS` 与 `shouldPreviewWithFileViewerInCanvas()`；零依赖，保持可被 Node 直载 |
| `frontend/src/components/embed/DocumentViewer.vue` | 修改 | 新增可选 `content` 属性：给出内容时跳过 axios，直接包装 `File` |
| `frontend/src/components/embed/ChatCanvas.vue` | 修改 | 新增 `isFileViewerTextPreview` / `isFileViewerRendered`；默认标签页改为 `preview`；新增预览分支；扩展标签页与下载图标条件 |
| `tests/frontend/canvas_text_preview.mjs` | 新建 | Node 行为测试：扩展名 → 是否走 file-viewer |
| `tests/frontend/test_canvas_text_preview_contract.py` | 新建 | 前端契约：helper 零依赖、画布复用、分支顺序、DocumentViewer 支持 content |
| `tests/CHECKLIST.md` | 修改 | 按项目约定登记 |

---

### Task 1: 新增画布文本预览判定纯函数

**Files:**
- Modify: `frontend/src/utils/documentPreviewFormats.ts`（在 `resolveHighlightLanguage` 之后追加）
- Test: `tests/frontend/canvas_text_preview.mjs`（新建）

- [ ] **Step 1: 写失败的行为测试**

新建 `tests/frontend/canvas_text_preview.mjs`：

```js
// 用 Node 真跑纯函数：判定必须只看扩展名，且 .md/.html/.py/.sh 这三类
// 「画布专属渲染」的格式绝不能被误判为可交给 file-viewer。
import assert from 'node:assert/strict'

import { shouldPreviewWithFileViewerInCanvas } from '../../frontend/src/utils/documentPreviewFormats.ts'

// --- 默认交给 file-viewer 预览的文本/代码格式 ---
const PREVIEW_CASES = [
  'config.json', 'data.jsonc', 'settings.json5', 'nb.ipynb',
  'app.yaml', 'app.yml', 'Cargo.toml', 'setup.ini',
  'main.js', 'm.mjs', 'c.cjs', 'x.jsx',
  'a.ts', 'b.tsx', 'Comp.vue',
  'schema.sql', 'Main.java', 'main.go', 'lib.rs', 'x.rb', 'i.php',
  'a.c', 'a.h', 'a.cpp', 'a.hpp', 'Program.cs', 'a.swift', 'a.kt',
  'notes.txt', 'crash.log', 'style.css', 'api.http', 'fix.diff', 'change.patch',
  'CONFIG.JSON', // 大写扩展名
]

// --- 必须在画布侧渲染，绝不能交给 file-viewer ---
const CANVAS_NATIVE_CASES = [
  // markdown：画布渲染器带 mermaid + ECharts，file-viewer 无 ECharts
  'README.md', 'doc.markdown',
  // HTML：画布做 sandbox 片段渲染，file-viewer 只显示源码
  'index.html', 'page.htm',
  // 可执行脚本：画布支持运行 + 实时输出
  'deploy.sh', 'run.bash', 'main.py', 'app.py',
]

// --- 既非 Office/PDF（另有分派），也不该由本函数接管 ---
const OTHER_CASES = [
  'a.docx', 'a.doc', 'a.xlsx', 'a.xls', 'a.xlsm', 'a.pptx', // Office
  'a.ppt',                                                  // 下载
  'a.pdf',                                                  // pdf 类型
  'data.csv',                                               // 画布 PivotTable
  'a.png', 'a.jpg',                                          // 图片
  '.env', 'a.conf', 'a.rst', 'a.xyz',                        // file-viewer 不支持
  '', 'noextension',                                         // 无扩展名
]

for (const name of PREVIEW_CASES) {
  assert.equal(
    shouldPreviewWithFileViewerInCanvas(name),
    true,
    `${name} 应默认交给 file-viewer`,
  )
}

for (const name of CANVAS_NATIVE_CASES) {
  assert.equal(
    shouldPreviewWithFileViewerInCanvas(name),
    false,
    `${name} 必须在画布侧渲染（mermaid/ECharts、sandbox、脚本执行）`,
  )
}

for (const name of OTHER_CASES) {
  assert.equal(
    shouldPreviewWithFileViewerInCanvas(name),
    false,
    `${name} 不应由本函数接管`,
  )
}

// --- 非字符串输入不得抛错 ---
for (const value of [null, undefined]) {
  assert.equal(shouldPreviewWithFileViewerInCanvas(value), false, `${String(value)} 应返回 false`)
}

console.log('画布文本预览判定测试全部通过')
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --experimental-strip-types --no-warnings tests/frontend/canvas_text_preview.mjs`
Expected: FAIL，报 `does not provide an export named 'shouldPreviewWithFileViewerInCanvas'`

- [ ] **Step 3: 实现纯函数**

在 `frontend/src/utils/documentPreviewFormats.ts` 的 `resolveHighlightLanguage` 之后追加：

```ts
/**
 * 画布必须自己渲染、不可交给 file-viewer 的文本格式。三类，各有硬原因：
 *  - `.md` / `.markdown`：画布的 markdown 渲染器带 mermaid 与 ECharts；
 *    file-viewer 的 markdown 依赖只有 marked + dompurify，没有 ECharts 能力。
 *  - `.html` / `.htm`：画布用 sandbox iframe 渲染 HTML 片段；
 *    file-viewer 把 `.html` 交给 code 渲染器，只显示源码。
 *  - `.py` / `.sh` / `.bash`：画布支持脚本执行与实时输出，file-viewer 只读。
 */
export const CANVAS_NATIVE_TEXT_EXTENSIONS = new Set([
  '.md', '.markdown',
  '.html', '.htm',
  '.py', '.sh', '.bash',
])

/**
 * 画布 / 工作空间里，该文本格式是否默认交给 DocumentViewer 预览。
 *
 * 这是**静态**规则：只看扩展名，不看文件来源或可写性，保证同一文件每次打开行为一致。
 * 返回 true 的格式在画布中仍可通过「源代码」标签页切回 code 视图编辑。
 *
 * 注意边界：
 *  - Office 与 PDF 不走本函数（它们已由 `isDocumentPreview` 分派给 DocumentViewer）；
 *  - `.csv` 走画布 PivotTable，不在 DOCUMENT_VIEWER_EXTENSIONS 里，天然为 false；
 *  - `.ppt` 由 `shouldDownloadInsteadOfPreview` 拦成下载。
 */
export function shouldPreviewWithFileViewerInCanvas(name: string): boolean {
  const ext = getWorkspaceFileExtension(name)
  if (!ext) return false
  if (CANVAS_NATIVE_TEXT_EXTENSIONS.has(ext)) return false
  if (OFFICE_EXTENSIONS.has(ext)) return false
  return DOCUMENT_VIEWER_EXTENSIONS.has(ext)
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `node --experimental-strip-types --no-warnings tests/frontend/canvas_text_preview.mjs`
Expected: PASS，输出 `画布文本预览判定测试全部通过`

- [ ] **Step 5: 提交（仅在用户明确要求提交时执行）**

```bash
git add frontend/src/utils/documentPreviewFormats.ts tests/frontend/canvas_text_preview.mjs
git commit -m "feat(canvas): 新增文本格式走 file-viewer 预览的扩展名判定"
```

---

### Task 2: DocumentViewer 支持直接接收内容

**Files:**
- Modify: `frontend/src/components/embed/DocumentViewer.vue:8-14`（props）、`:39-76`（loadBlob）、`:88`（watch）

**背景：** 现在 `DocumentViewer` 只接受 `url` 并用 `axios.get(url, {responseType:'blob'})` 自己拉取。画布的文本分支**只把取到的文本存进 `content`，没有存 URL**，因此文本格式无法传 `url`。与其新增 URL 字段（会导致同一文件被请求两次），不如让组件直接接收内容。

- [ ] **Step 1: 给 props 增加可选 content，url 改为可选**

把 `DocumentViewer.vue` 的 props 定义替换为：

```ts
const props = defineProps<{
  /** 需要鉴权的文档地址；走 axios 获取，因此跨站嵌入场景同样可用 */
  url?: string
  /**
   * 直接给出文本内容时使用：跳过网络请求，由本组件包装成 File 交给渲染器。
   * 画布的文本格式已经取到内容，不必再由本组件重复请求一次。
   */
  content?: string
  filename: string
  meta?: CanvasDocumentMeta
  theme: 'light' | 'dark'
}>()
```

- [ ] **Step 2: loadBlob 增加内容短路分支**

把 `loadBlob` 开头到 axios 调用之前的部分替换为下面内容（保留既有的 axios 分支与 catch 不变）：

```ts
async function loadBlob() {
  controller?.abort()
  controller = new AbortController()

  status.value = 'loading'
  errorMessage.value = ''
  fileBlob.value = null

  const resolvedName = props.meta?.filename || props.filename || 'document'

  // 直接给出内容：不请求网络（画布文本格式走这条）。
  // 空字符串是合法内容（空文件），因此用 undefined/null 判断而不是真值判断。
  if (props.content !== undefined && props.content !== null) {
    fileBlob.value = new File([props.content], resolvedName, { type: props.meta?.mime || '' })
    status.value = 'ready'
    return
  }

  if (!props.url) {
```

（其后保持原有的 `status.value = 'error'` / `errorMessage.value = '缺少文件地址'` / `emit('error', ...)` / `return` 与 `try { const response = await axios.get(props.url, ...` 不变。）

- [ ] **Step 3: watch 同时监听 content**

把第 88 行替换为：

```ts
watch(() => [props.url, props.content], loadBlob, { immediate: true })
```

- [ ] **Step 4: 类型检查确认无新增错误**

Run: `cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b 2>&1 | grep "error TS" | sort > /tmp/tsc-t2.log; wc -l < /tmp/tsc-t2.log`
Expected: `62`（等于基线），且 `grep -c DocumentViewer /tmp/tsc-t2.log` 为 `0`

- [ ] **Step 5: 提交（仅在用户明确要求提交时执行）**

```bash
git add frontend/src/components/embed/DocumentViewer.vue
git commit -m "feat(embed): DocumentViewer 支持直接接收文本内容，跳过网络请求"
```

---

### Task 3: 画布接入预览分支与标签页

**Files:**
- Modify: `frontend/src/components/embed/ChatCanvas.vue`

**注意：** 以下每处都要精确替换。改完后 `ChatCanvas.vue` 中**不得**出现 `DOCUMENT_VIEWER_EXTENSIONS`（既有契约测试 `test_canvas_types_contract.py` 会检查，且历史上因此连出三个缺陷）。

- [ ] **Step 1: 引入共享判定**

把第 11 行的 import 改为（在既有列表中按字母序插入 `shouldPreviewWithFileViewerInCanvas`）：

```ts
import { buildGeneratedWorkspaceFilename, canWriteWorkspaceFile, createWorkspaceEntry, downloadWorkspaceFile, isDirectRenderableUrl, resolveHighlightLanguage, resolvePublicUploadsPreviewUrl, saveWorkspaceFileContent, shouldPreviewWithFileViewerInCanvas } from '@/utils/workspaceFilePreview';
```

- [ ] **Step 2: 新增两个 computed（插在 `isDocumentPreview` 之后）**

`activeTab` 在第 398 行声明，但 `isDocumentPreview` 在第 665 行才声明。因此**必须把新代码插在 `isDocumentPreview` 的 computed 之后**（约第 668 行，即 `// Watchers & Lifecycles` 注释之前）；插在 `activeTab` 后面会因 `isDocumentPreview` 尚未定义而触发 TDZ 报错。

```ts
/**
 * 文本/代码格式是否默认交给 file-viewer 预览。
 * 判定是静态的（只看扩展名），.md/.html/.py/.sh 等画布专属格式被排除在外。
 */
const isFileViewerTextPreview = computed(() => {
  if (!props.data || props.data.type !== 'code') return false;
  return shouldPreviewWithFileViewerInCanvas(props.data.title);
});

/**
 * 当前是否由 file-viewer 承担渲染。
 * 用途：file-viewer 工具栏自带「下载」，此时隐藏画布顶部的下载图标避免重复。
 */
const isFileViewerRendered = computed(
  () => isDocumentPreview.value || (isFileViewerTextPreview.value && activeTab.value === 'preview'),
);
```

- [ ] **Step 3: 默认标签页改为预览**

在 watcher（搜索 `if (props.data.type === 'html' || isMarkdownContent.value)`）中，把条件改为：

```ts
    if (props.data.type === 'html' || isMarkdownContent.value || isFileViewerTextPreview.value) {
      activeTab.value = 'preview';
    } else {
      activeTab.value = 'code';
    }
```

- [ ] **Step 4: 新增预览分支（位置很关键）**

在 `<!-- Office 文档 / PDF：由 DocumentViewer 统一渲染 -->` 那个 `<template v-if="isDocumentPreview">` 块**之后**、`<template v-else-if="isHtmlContent">` **之前**插入：

```html
        <!-- 文本格式：默认由 file-viewer 预览，可切到「源代码」编辑 -->
        <template v-else-if="isFileViewerTextPreview && activeTab === 'preview'">
          <DocumentViewer
            :content="data?.content || ''"
            :filename="data?.documentMeta?.filename || data?.title || 'document'"
            :meta="data?.documentMeta"
            :theme="canvasTheme"
            @fallback-download="downloadFile"
          />
        </template>
```

放在 `isHtmlContent` 之前是刻意的：`isHtmlContent` 会按**内容**嗅探（含 `<div`/`<img` 即判为 HTML），扩展名已知且非 HTML 时不应让内容嗅探把文件改道。

- [ ] **Step 5: 扩展标签页条件**

把 `<!-- HTML / Markdown Preview/Code Tabs Selector -->` 那个 div 的 `v-if` 改为：

```html
      <div v-if="isHtmlContent || isMarkdownContent || isFileViewerTextPreview" class="px-4 py-2 border-b border-gray-100/50 dark:border-gray-700/50 bg-slate-50/50 dark:bg-gray-900/10 flex-shrink-0 flex items-center justify-center gap-2">
```

- [ ] **Step 6: 下载图标改用新条件**

把下载按钮的 `v-if="!isDocumentPreview"` 改为 `v-if="!isFileViewerRendered"`，并把上方注释块整段替换为：

```html
          <!--
            file-viewer（Office/PDF/文本预览）工具栏自带「下载」，故此时隐藏该图标避免重复。
            切到「源代码」编辑时 file-viewer 不再渲染，图标重新出现。
            .ppt 不会走到 document 类型（已在 useWorkspaceCanvas 里被
            shouldDownloadInsteadOfPreview 拦成下载），因此它始终保留该图标。

            用 computed 而不是内联的字面量 type 比较：内联比较会让 TS 对本按钮的子节点
            收窄 data.type，导致按钮内的 pdf/document 比较报 TS2367（本文件 +2 条）。
          -->
```

- [ ] **Step 7: 类型检查**

Run: `cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b 2>&1 | grep "error TS" | sort > /tmp/tsc-t3.log; wc -l < /tmp/tsc-t3.log`
Expected: `62`（等于基线），且 `grep -c ChatCanvas /tmp/tsc-t3.log` 为 `0`

- [ ] **Step 8: 提交（仅在用户明确要求提交时执行）**

```bash
git add frontend/src/components/embed/ChatCanvas.vue
git commit -m "feat(canvas): 文本格式默认走 file-viewer 预览，保留源代码标签页编辑"
```

---

### Task 4: 前端契约测试

**Files:**
- Test: `tests/frontend/test_canvas_text_preview_contract.py`（新建）
- Modify: `tests/frontend/test_canvas_types_contract.py`（第 88/90/144 行：判据字面量 `!isDocumentPreview` 同步为 `!isFileViewerRendered`）

**背景（Task 3 执行时发现的计划缺口）：** Task 3 的 Step 6 把下载图标判据从 `v-if="!isDocumentPreview"` 改为 `v-if="!isFileViewerRendered"`，而 `test_canvas_types_contract.py` 有 **3 处硬断言旧字面量**（第 88、90、144 行），因此 Task 3 之后该文件必然出现 2 条失败。这是**有意的行为变更**：新判据是旧判据的**严格超集**（Office/PDF 仍隐藏，并新增「文本格式在预览标签页下也隐藏」），原测试守护的性质「file-viewer 渲染时不重复出现下载入口」不仅仍成立而且更强。**不得回退 Step 6** —— Task 4 的新测试反过来要求新判据。

- [ ] **Step 1: 同步既有契约测试的判据字面量**

修改 `tests/frontend/test_canvas_types_contract.py`。

（a）在 `test_chat_canvas_hides_duplicate_download_button_for_document_viewer` 中，把

```python
    # isDocumentPreview 的定义保持 document || pdf
    assert "() => props.data?.type === 'document' || props.data?.type === 'pdf'" in canvas
    assert "v-if=\"!isDocumentPreview\"" in canvas
    # 该 v-if 必须恰好挂在 header 中 @click="downloadFile" 的按钮上（.ppt 仍走此按钮）
    download_button = canvas.split("v-if=\"!isDocumentPreview\"")[1].split("</button>")[0]
```

替换为

```python
    # isDocumentPreview 的定义保持 document || pdf
    assert "() => props.data?.type === 'document' || props.data?.type === 'pdf'" in canvas
    # 判据升级为 isFileViewerRendered：除 Office/PDF 外，文本格式在「预览」标签页下也由
    # file-viewer 渲染，同样要隐藏画布下载图标；切到「源代码」后图标恢复。
    assert "v-if=\"!isFileViewerRendered\"" in canvas
    assert "isDocumentPreview.value ||" in canvas
    assert "isFileViewerTextPreview.value && activeTab.value === 'preview'" in canvas
    # 该 v-if 必须恰好挂在 header 中 @click="downloadFile" 的按钮上（.ppt 仍走此按钮）
    download_button = canvas.split("v-if=\"!isFileViewerRendered\"")[1].split("</button>")[0]
```

该测试的 docstring 也需同步：把「用 `!isDocumentPreview` 而不是内联的 `data?.type !== ...`」那段改为说明**现在用 `!isFileViewerRendered`**，而**仍然不能内联** `data?.type` 比较 —— 原因不变（内联会比较 `data.type`，使按钮内 pdf/document 比较报 TS2367，本文件 +2 条）。

（b）在 `test_chat_canvas_actions_render_as_header_icon_buttons` 末尾，把

```python
    # Office/PDF 仍隐藏下载图标（file-viewer 工具栏自带下载）：判据 = isDocumentPreview
    download_button = canvas.split('v-if="!isDocumentPreview"')[1].split("</button>")[0]
```

替换为

```python
    # file-viewer 渲染时隐藏下载图标（其工具栏自带下载）：判据 = isFileViewerRendered
    download_button = canvas.split('v-if="!isFileViewerRendered"')[1].split("</button>")[0]
```

- [ ] **Step 2: 确认既有测试恢复全绿**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend/test_canvas_types_contract.py`
Expected: 全绿（Task 3 之后此处曾有 2 条红）

- [ ] **Step 3: 写契约测试**

新建 `tests/frontend/test_canvas_text_preview_contract.py`：

```python
"""画布文本格式默认走 file-viewer 的契约。

用户诉求：画布里默认都应该是 file-viewer 预览，只有要编辑时才切 code。
落地为**静态**扩展名规则（见 documentPreviewFormats.shouldPreviewWithFileViewerInCanvas），
但三类格式必须在画布侧渲染：.md（mermaid + ECharts）、.html（sandbox 片段渲染）、
.py/.sh（脚本执行），否则是能力倒退。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_canvas_text_preview_behaviour_via_node():
    script = Path("tests/frontend/canvas_text_preview.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "画布文本预览判定测试全部通过" in proc.stdout


def test_helper_is_exported_and_dependency_free():
    source = _source("frontend/src/utils/documentPreviewFormats.ts")

    assert "export function shouldPreviewWithFileViewerInCanvas(" in source
    assert "export const CANVAS_NATIVE_TEXT_EXTENSIONS" in source
    # 零运行时依赖：该模块必须能继续被 node --experimental-strip-types 直接加载
    assert "from '@/utils/axios'" not in source
    assert "from 'axios'" not in source
    # 三类画布专属格式必须在排除集合里
    for ext in ("'.md'", "'.markdown'", "'.html'", "'.htm'", "'.py'", "'.sh'", "'.bash'"):
        assert ext in source, f"{ext} 应出现在 CANVAS_NATIVE_TEXT_EXTENSIONS 中"


def test_canvas_uses_shared_helper_and_never_document_viewer_set():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    assert "shouldPreviewWithFileViewerInCanvas" in canvas, "画布未复用共享判定"
    # 历史事故：画布直接用 DOCUMENT_VIEWER_EXTENSIONS 会让 .md/.txt 显示 OFFICE 徽章
    assert "DOCUMENT_VIEWER_EXTENSIONS" not in canvas


def test_canvas_preview_branch_precedes_code_branch():
    """预览分支必须排在 type === 'code' 分支之前，否则永远命中不了。

    锚点必须用模板里的完整字面量 `<template v-else-if="data?.type === 'code'">`：
    裸的 `data?.type === 'code'` 在脚本区第 446 行（isRunnableCode 内）也会命中，
    用它会锚到错误位置，让正确实现也被判失败。
    """
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    preview_at = canvas.index("isFileViewerTextPreview && activeTab === 'preview'")
    code_at = canvas.index("""<template v-else-if="data?.type === 'code'">""")
    assert preview_at < code_at, "file-viewer 文本预览分支必须排在 code 分支之前"

    # 也必须排在 isHtmlContent 之前，避免内容嗅探把扩展名已知的文件改道
    html_at = canvas.index('<template v-else-if="isHtmlContent">')
    assert preview_at < html_at, "文本预览分支必须排在 isHtmlContent 分支之前"


def test_document_viewer_accepts_content_without_network():
    viewer = _source("frontend/src/components/embed/DocumentViewer.vue")

    assert "content?: string" in viewer, "DocumentViewer 应支持直接接收内容"
    assert "props.content !== undefined" in viewer, "空字符串也是合法内容，不得用真值判断"
    # 必须保留了 url 这条路径（RAG 抽屉 / 知识库弹窗仍在用）
    assert "props.url" in viewer


def test_download_icon_follows_file_viewer_rendering():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    assert "isFileViewerRendered" in canvas
    assert 'v-if="!isFileViewerRendered"' in canvas
```

- [ ] **Step 4: 运行契约测试**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend/test_canvas_text_preview_contract.py`
Expected: `6 passed`

- [ ] **Step 5: 回归全量前端契约**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend`
Expected: 全绿（上一轮基线为 **1590 passed / 1 skipped**，本轮新增 6 条后应为 **1596 passed / 1 skipped**）

- [ ] **Step 6: 提交（仅在用户明确要求提交时执行）**

```bash
git add tests/frontend/test_canvas_text_preview_contract.py tests/frontend/test_canvas_types_contract.py
git commit -m "test(canvas): 补文本格式 file-viewer 默认预览的契约测试"
```

---

### Task 5: 变异反向验证

**目的：** 证明新测试真的能抓住回归，而不是永远为绿。任一步骤发现测试没变红，说明测试无效，必须补强后再继续。

- [ ] **Step 1: 变异 M1——从排除集合里删掉 `.md`**

注意：字面量 `  '.md', '.markdown',` 在文件中出现 **2 次**（第 50 行 `DOCUMENT_VIEWER_EXTENSIONS`、第 212 行新的 `CANVAS_NATIVE_TEXT_EXTENSIONS`），所以**必须先定位新集合的声明**再在其内部替换，不能直接全局 assert 唯一。

```bash
cd frontend && cp src/utils/documentPreviewFormats.ts /tmp/dp2.bak
python3 -c "
p='src/utils/documentPreviewFormats.ts'; s=open(p).read()
marker='export const CANVAS_NATIVE_TEXT_EXTENSIONS = new Set(['
assert s.count(marker)==1, '新集合声明锚点不唯一'
head, tail = s.split(marker, 1)
old=\"  '.md', '.markdown',\"
assert tail.count(old)==1, '新集合内部的锚点不唯一'
open(p,'w').write(head + marker + tail.replace(old, '', 1))
"
node --experimental-strip-types --no-warnings ../tests/frontend/canvas_text_preview.mjs 2>&1 | grep -E "AssertionError|必须在画布侧" | head -2
cp /tmp/dp2.bak src/utils/documentPreviewFormats.ts
cd ..
```

Expected: 报 `README.md 必须在画布侧渲染...`，即行为测试捕获 `.md` 被放行

- [ ] **Step 2: 变异 M2——让预览分支条件恒假**

```bash
cd frontend && cp src/components/embed/ChatCanvas.vue /tmp/cv2.bak
python3 -c "
p='src/components/embed/ChatCanvas.vue'; s=open(p).read()
old='v-else-if=\"isFileViewerTextPreview && activeTab === \'preview\'\"'
assert s.count(old)==1, '锚点不唯一，检查实现是否与计划一致'
open(p,'w').write(s.replace(old, 'v-else-if=\"false\"'))
"
cd .. && .venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend/test_canvas_text_preview_contract.py 2>&1 | tail -3
cp /tmp/cv2.bak frontend/src/components/embed/ChatCanvas.vue
```

Expected: `test_canvas_preview_branch_precedes_code_branch` 变红

- [ ] **Step 3: 变异 M3——DocumentViewer 用真值判断 content**

```bash
cd frontend && cp src/components/embed/DocumentViewer.vue /tmp/dv2.bak
python3 -c "
p='src/components/embed/DocumentViewer.vue'; s=open(p).read()
old='if (props.content !== undefined && props.content !== null) {'
assert s.count(old)==1, '锚点不唯一，检查实现是否与计划一致'
open(p,'w').write(s.replace(old, 'if (props.content) {'))
"
cd .. && .venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend/test_canvas_text_preview_contract.py 2>&1 | tail -3
cp /tmp/dv2.bak frontend/src/components/embed/DocumentViewer.vue
```

Expected: `test_document_viewer_accepts_content_without_network` 变红

- [ ] **Step 4: 三处还原后确认字节级一致并复跑全绿**

在**仓库根目录**执行（注意 `--confcutdir` 是相对当前目录解析的）：

```bash
shasum -a 256 frontend/src/utils/documentPreviewFormats.ts /tmp/dp2.bak
shasum -a 256 frontend/src/components/embed/ChatCanvas.vue /tmp/cv2.bak
shasum -a 256 frontend/src/components/embed/DocumentViewer.vue /tmp/dv2.bak
.venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend
node --experimental-strip-types --no-warnings tests/frontend/canvas_text_preview.mjs
```

Expected: 三组 sha256 各自**成对相同**；全量测试全绿；行为测试输出 `画布文本预览判定测试全部通过`

---

### Task 6: 登记测试清单

**Files:**
- Modify: `tests/CHECKLIST.md`（在文件末尾追加一行）

- [ ] **Step 1: 追加登记行**

表格为 5 列，**行内不得出现裸 `|`**（用顿号或「与」代替）。新行格式：

```
| 画布文本格式默认走 file-viewer 预览，保留源代码标签页编辑 (Canvas Text FileViewer Default Preview) | `frontend/src/utils/documentPreviewFormats.ts`、`frontend/src/components/embed/DocumentViewer.vue`、`frontend/src/components/embed/ChatCanvas.vue`、`tests/frontend/canvas_text_preview.mjs`（新增）、`tests/frontend/test_canvas_text_preview_contract.py`（新增）、`tests/CHECKLIST.md` | （背景、设计、排除理由、验证数据） | ✅ （结论） | 2026-10-08 |
```

背景栏需覆盖：用户诉求原文（「应该默认都是 fileviewer 吧，除非要编辑的才走 code」）、三类必须排除的格式及硬原因、静态规则的取舍、DocumentViewer 增加 content 属性的原因（画布文本分支不存 URL，避免二次请求）、默认标签页变更、下载图标条件变更、2.8MB 首开代价、变异验证结果、全量测试与类型检查数字、无后端与数据库改动。

- [ ] **Step 2: 校验行格式**

Run: `sed -n "$p" tests/CHECKLIST.md | awk '{print gsub(/\|/,"|")}'`
Expected: `6`（5 列 = 6 个管道符）

- [ ] **Step 3: 重新跑全量测试确认清单变更无副作用**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend`
Expected: 全绿

---

## 已知代价与后续可选项（写进交付说明）

1. **首开体积（已实测更正）**：最初判断「预览任何文本都会拉约 2.8MB」**不准确**。查 `frontend/dist/index.html` 与入口 chunk 证实：`@file-viewer/vite-plugin` 的 `inject` 默认为 true，会把虚拟渲染器模块注入 HTML 入口，因此 `file-viewer-preset-standard` 是入口的**静态 import**，11 个 file-viewer chunk（合计 **2.78MB**）全部带 `modulepreload` —— 即**应用启动就加载**，与是否预览、预览什么格式**无关**（入口中对 file-viewer 的动态 `import(` 次数为 0）。所以**本方案不增加任何网络或解析成本**；但该代价确实早已存在，且 `DocumentViewer.vue` 注释所称「不进入首屏包」仅对 `@file-viewer/vue3` 组件本体成立，占大头的 10 个渲染器其实在首屏包里。缓存层面：`/assets` 由 FastAPI `StaticFiles` 挂载且未显式设置 `Cache-Control`，预期为条件请求 + 304（**未实测响应头**）；无 Service Worker，故每次页面加载仍需重新解析执行这 2.78MB。后续可优化（经用户确认**本轮不做**）：① `inject: false` + 应用代码动态 `import('virtual:file-viewer-renderers')`，改为真正的按需加载；② 插件**支持** `formats` / `renderers` 选项，可裁掉用不到的 archive/email/image/media/ofd（约 0.72MB）。**更正**：此前记录的「插件没有自定义渲染器列表选项」是错的 —— 它同时支持 `preset` / `autoPresets` / `formats` / `renderers` / `inject` / `moduleId` / `chunkStrategy` / `stabilizeInteropChunks` / `scan`。
2. **mermaid 对齐**：注册 `registerFileViewerMermaidLoader` 后，file-viewer 的 markdown 也能渲染 mermaid，届时 `.md` 排除的唯一硬原因只剩 ECharts。
3. **未接线的能力**：我们仍未调用 file-viewer 的任何公开 API（0 方法 / 0 事件），`searchDocument()` / `scrollToAnchor()` / 缩放 / 打印均依赖其自带工具栏，未与画布联动。

## 验收清单（交给用户实机确认）

- [ ] 画布打开工作区 `.json` / `.ts` / `.txt` / `.vue` → 默认 **file-viewer** 渲染，有语法高亮与工具栏（搜索/打印/缩放）
- [ ] 同一文件点「源代码」→ 切回画布代码视图，可编辑、可保存
- [ ] 工作区 `.md` → 仍是画布 markdown 渲染，**mermaid 图与 ECharts 图正常**
- [ ] `.html` → 仍是 sandbox 片段渲染（不是源码）
- [ ] `.py` / `.sh` → 仍是 code 视图，**「运行」按钮与实时输出正常**
- [ ] AI 生成的内容（无工作区来源）→ 按扩展名同样默认 file-viewer，空文件不报错
- [ ] 顶部下载图标：file-viewer 渲染时隐藏；切到「源代码」后重新出现
- [ ] `.ppt` 仍为点击下载；`.csv` 仍是 PivotTable；`.docx`/`.pdf` 行为不变
- [ ] RAG 引用抽屉与知识库弹窗行为**完全不变**
