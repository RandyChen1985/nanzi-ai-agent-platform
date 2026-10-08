# RAG 链路 Office 文档预览接入 file-viewer 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 RAG 引用预览抽屉与知识库管理页的「预览原文件」弹窗，能内联预览 Office 文档（6 种格式），PDF 及其余格式保持现有 iframe 实现不变。

**Architecture:** 复用画布那版已建成的 `DocumentViewer.vue`（懒加载 + axios 取 Blob + `new File` 包装 + 卸载中断 + 错误兜底）。把「哪些格式能交给预览组件」的判定抽成**零运行时依赖**的独立模块 `documentPreviewFormats.ts`，供所有接入点共用——既消除判定漂移，也让该纯函数能被 Node 直接跑行为测试。两个入口各自内联组件，PDF 分支原样保留。

**Tech Stack:** Vue 3 + TypeScript + Vite 7 + Tailwind 3；`@file-viewer/vue3` + `@file-viewer/preset-standard`（画布那版已安装）；pytest（前端契约测试）+ Node 24 的 `--experimental-strip-types`（纯函数行为测试）。

---

## 执行环境注意（先读这一节，能省掉大量返工）

**1. 类型检查命令（关键陷阱）**

```bash
cd frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b
```

- **必须用 `-b`**：本项目 tsconfig 是 solution-style，`npx vue-tsc --noEmit` 是**空操作**（不检查任何文件，永远退出 0）。
- **必须带 `NODE_OPTIONS`**：默认堆内存下 `-b` 会 OOM（退出码 134）。
- **基线是 62 条错误**（`error TS` 行数）。这不是 0，属既有问题，**与本计划无关**。判据是「总数仍为 62 且不落在本计划改动的文件上」。

**2. 测试命令**

```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend <paths> -v
```

- 必须用 `.venv/bin/python`：PATH 里的 `pytest` 是 Python 3.13 且未装依赖。
- 必须带 `--confcutdir=tests/frontend`。

**3. 包管理器是 npm**，不是 pnpm。仓库只跟踪 `frontend/package-lock.json`。

**4. 提交约定**：本计划的每个 Task 末尾给出 commit message 草稿，但**不代为执行 `git commit`**——项目 `AGENTS.md` 明确禁止，由用户自行提交。

**5. 契约测试的性质**：`tests/frontend/` 下的 Python 测试绝大多数是**源码子串匹配**，连注释都算被测内容。这类测试**抓不到「API 用错」**（画布那版就因此漏掉了「传裸 Blob 导致 renderer 无法分派」的真实缺陷）。因此本计划对纯函数改用 Node 行为测试，并在任务里注明哪些风险只能靠人工实测。

---

## 文件结构

| 文件 | 职责 | 动作 |
| --- | --- | --- |
| `frontend/src/utils/documentPreviewFormats.ts` | **零运行时依赖**的格式判定：扩展名集合、MIME 映射、判定函数 | 新建 |
| `frontend/src/utils/workspaceFilePreview.ts` | 工作区预览编排（依赖 axios），改为 re-export 上述模块 | 修改（顶部） |
| `tests/frontend/document_preview_formats.mjs` | 用 Node 真跑纯函数的**行为**测试 | 新建 |
| `tests/frontend/test_document_preview_formats_contract.py` | 包装上面的 .mjs，并做静态断言 | 新建 |
| `frontend/src/components/RagPreviewDrawer.vue` | RAG 引用预览抽屉 | 修改 |
| `frontend/src/views/KnowledgeBaseManagement.vue` | 知识库管理页（预览弹窗） | 修改 |
| `frontend/src/views/EmbedChat.vue` | 嵌入聊天宿主 | 修改（删重复判定） |
| `frontend/src/views/AgentDebug.vue` | 智能体调试宿主 | 修改（删重复判定） |
| `tests/frontend/test_rag_document_preview_contract.py` | 两个入口的契约测试 | 新建 |

**为什么要把判定抽成独立模块**：`workspaceFilePreview.ts` 已有 422 行且顶部 `import axios from '@/utils/axios'`，Node 无法直接加载（`@/` 别名 + axios 依赖）。抽出零依赖模块后，`frontend/src/utils/documentPreviewFormats.ts` 可以直接被 `node --experimental-strip-types` 执行，从而对格式判定做**真实行为断言**——这正是能抓住 `.ppt` / `.pptx` 子串陷阱的手段。

---

## Task 1: 抽离零依赖格式判定模块，并新增共享判定函数

**Files:**
- Create: `frontend/src/utils/documentPreviewFormats.ts`
- Create: `tests/frontend/document_preview_formats.mjs`
- Create: `tests/frontend/test_document_preview_formats_contract.py`
- Modify: `frontend/src/utils/workspaceFilePreview.ts:7-49`（扩展名集合与三个函数移出，改为 re-export）

- [ ] **Step 1: 写失败测试（Node 行为测试脚本）**

创建 `tests/frontend/document_preview_formats.mjs`：

```js
// 用 Node 真跑纯函数：静态字符串断言看不出「.pptx 会不会被 .ppt 规则误伤」，
// 这里直接调用真实实现做行为验证。
import assert from 'node:assert/strict'

import {
  canPreviewWithDocumentViewer,
  shouldDownloadInsteadOfPreview,
  resolveDocumentViewerMime,
  getWorkspaceFileExtension,
} from '../../frontend/src/utils/documentPreviewFormats.ts'

// --- 6 种组件官方支持的格式，必须判为可预览 ---
for (const name of ['a.docx', 'a.doc', 'a.xlsx', 'a.xls', 'a.xlsm', 'a.pptx']) {
  assert.equal(canPreviewWithDocumentViewer(name), true, `${name} 应可预览`)
}

// --- 旧版二进制 .ppt 必须不可预览（需带水印、许可受限的 @file-viewer/ppt）---
assert.equal(canPreviewWithDocumentViewer('a.ppt'), false, '.ppt 不应可预览')

// --- 子串陷阱：.pptx / .pptm 含 ".ppt" 前缀，绝不能被 .ppt 规则误伤 ---
assert.equal(canPreviewWithDocumentViewer('模型各平台对比.pptx'), true, '.pptx 不能被 .ppt 规则误伤')
assert.equal(canPreviewWithDocumentViewer('deck.PPTX'), true, '大写 .PPTX 应可预览')
assert.equal(canPreviewWithDocumentViewer('deck.PPT'), false, '大写 .PPT 不应可预览')

// --- 带路径与多点的文件名 ---
assert.equal(canPreviewWithDocumentViewer('/tmp/a.b.docx'), true, '带路径与多点应正确取扩展名')
assert.equal(canPreviewWithDocumentViewer('无扩展名'), false, '无扩展名不应可预览')
assert.equal(canPreviewWithDocumentViewer(''), false, '空串不应可预览')

// --- .ppt 走下载而不是预览 ---
assert.equal(shouldDownloadInsteadOfPreview('a.ppt'), true, '.ppt 应走下载')
assert.equal(shouldDownloadInsteadOfPreview('a.pptx'), false, '.pptx 不应走下载')
assert.equal(shouldDownloadInsteadOfPreview('a.txt'), false, '非 Office 不应命中下载规则')

// --- MIME 映射 ---
assert.equal(
  resolveDocumentViewerMime('a.docx'),
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.docx 的 MIME',
)
assert.equal(resolveDocumentViewerMime('a.ppt'), undefined, '.ppt 无 MIME 映射')
assert.equal(resolveDocumentViewerMime('a.txt'), undefined, '未知格式无 MIME 映射')

// --- 扩展名提取 ---
assert.equal(getWorkspaceFileExtension('a.tar.gz'), '.gz', '多点取最后一段')
assert.equal(getWorkspaceFileExtension('noext'), '', '无扩展名返回空串')

console.log('文档预览格式判定测试全部通过')
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && node --experimental-strip-types --no-warnings tests/frontend/document_preview_formats.mjs
```
Expected: FAIL —— `Cannot find module '.../frontend/src/utils/documentPreviewFormats.ts'`

- [ ] **Step 3: 创建零依赖判定模块**

创建 `frontend/src/utils/documentPreviewFormats.ts`：

```ts
/**
 * 文档预览格式判定 —— 刻意保持零运行时依赖（无 axios、无别名导入）。
 *
 * 这样本模块能被 `node --experimental-strip-types` 直接加载，
 * 从而对格式判定做真实行为断言（见 tests/frontend/document_preview_formats.mjs）。
 * 字符串子串断言抓不到「.pptx 被 .ppt 规则误伤」这类问题，行为测试可以。
 */

export const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif'])

export const TEXT_EXTENSIONS = new Set([
  '.txt', '.md', '.csv', '.json', '.sql', '.py', '.js', '.ts',
  '.sh', '.xml', '.html', '.css', '.yaml', '.yml', '.ini', '.conf',
  '.log', '.env', '.htm',
])

export const OFFICE_EXTENSIONS = new Set([
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

export function getWorkspaceFileExtension(name: string): string {
  const parts = String(name || '').split('.')
  if (parts.length < 2) return ''
  return `.${parts.pop()!.toLowerCase()}`
}

export function resolveDocumentViewerMime(name: string): string | undefined {
  const ext = getWorkspaceFileExtension(name)
  return DOCUMENT_VIEWER_MIME[ext]
}

/**
 * 处于 OFFICE_EXTENSIONS 但不在 DOCUMENT_VIEWER_EXTENSIONS 的格式：目前只有旧版二进制 `.ppt`。
 * 它能被识别为 Office 文件，但因需要带内置水印、许可为 SEE LICENSE IN LICENSE 的
 * @file-viewer/ppt，刻意不交给 DocumentViewer —— 这些格式保持「点击即下载」。
 */
export function shouldDownloadInsteadOfPreview(name: string): boolean {
  const ext = getWorkspaceFileExtension(name)
  return OFFICE_EXTENSIONS.has(ext) && !DOCUMENT_VIEWER_EXTENSIONS.has(ext)
}

/**
 * 该文件能否交给 DocumentViewer 预览。
 *
 * 画布、RAG 引用抽屉、知识库预览弹窗统一调用本函数，
 * 避免同一判定出现多份实现后彼此漂移（画布那版曾因两份分派实现漂移，连出三个缺陷）。
 */
export function canPreviewWithDocumentViewer(name: string): boolean {
  return DOCUMENT_VIEWER_EXTENSIONS.has(getWorkspaceFileExtension(name))
}
```

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && node --experimental-strip-types --no-warnings tests/frontend/document_preview_formats.mjs
```
Expected: PASS，输出 `文档预览格式判定测试全部通过`

- [ ] **Step 5: 让 workspaceFilePreview.ts 改为 re-export（保持既有导入路径不变）**

修改 `frontend/src/utils/workspaceFilePreview.ts`：**删除**第 7-49 行原有的 `IMAGE_EXTENSIONS`、`TEXT_EXTENSIONS`、`OFFICE_EXTENSIONS`、`DOCUMENT_VIEWER_EXTENSIONS`、`DOCUMENT_VIEWER_MIME`、`resolveDocumentViewerMime`、`shouldDownloadInsteadOfPreview`、`getWorkspaceFileExtension` 定义，替换为一行 re-export。文件开头变为：

```ts
import axios from '@/utils/axios'
import { copyToClipboard } from './clipboard'
import type { CanvasPanelData, WorkspaceCanvasType } from '@/types/canvas'

// 格式判定集中在零依赖模块，便于 Node 行为测试；此处 re-export 保持既有导入路径不变
export * from './documentPreviewFormats'

export type { CanvasPanelData, WorkspaceCanvasType } from '@/types/canvas'

// 以下为依赖 axios / clipboard 的工作区编排逻辑，保持原样（normalizeWorkspacePath 起）
```

⚠️ **注意**：`normalizeWorkspacePath`（原第 51 行）**不要动**，它属于编排层。

- [ ] **Step 6: 验证 re-export 未破坏既有消费者**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform/frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b 2>&1 | grep -c "error TS"
```
Expected: `62`（与基线一致；`documentPreviewFormats.ts` 与 `workspaceFilePreview.ts` 不应出现在错误列表中）

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend/test_workspace_document_preview_contract.py tests/frontend/test_canvas_types_contract.py tests/frontend/test_document_viewer_contract.py tests/frontend/test_canvas_legacy_ppt_download_contract.py
```
Expected: 全部 PASS（re-export 后这些契约仍成立）

> ⚠️ **注意**：`test_workspace_document_preview_contract.py` 里有一条断言检查 `DOCUMENT_VIEWER_EXTENSIONS` 的 Set 字面量切片。re-export 后该字面量已移出 `workspaceFilePreview.ts`，**这条断言会失败**。若失败，把该测试的 `_source(...)` 目标从 `frontend/src/utils/workspaceFilePreview.ts` 改为 `frontend/src/utils/documentPreviewFormats.ts`（该断言的本意就是校验那个集合的成员，换文件后语义不变）。

- [ ] **Step 7: 提交（由用户执行）**

```bash
git add frontend/src/utils/documentPreviewFormats.ts frontend/src/utils/workspaceFilePreview.ts \
        tests/frontend/document_preview_formats.mjs tests/frontend/test_document_preview_formats_contract.py
git commit -m "refactor(frontend): 抽出零依赖的文档预览格式判定模块

- 新增 documentPreviewFormats.ts：扩展名集合、MIME 映射与判定函数
- 新增 canPreviewWithDocumentViewer()，供各预览入口共用同一判定
- workspaceFilePreview.ts 改为 re-export，既有导入路径不变
- 新增 Node 行为测试，锁住 .ppt/.pptx 子串陷阱"
```

---

## Task 2: RAG 引用预览抽屉接入组件（并清理两个宿主的重复判定）

> ⚠️ **本任务必须一次完成**：`RagPreviewDrawer.vue` 删掉 `isOfficeDocument` prop 后，两个宿主仍在传 `:is-office-document`，`vue-tsc` 会报 prop 不存在的错误。所以「删 prop」「删宿主传参」必须在同一个 Task 内完成，否则中间态类型检查不干净。

**Files:**
- Modify: `frontend/src/components/RagPreviewDrawer.vue`
- Modify: `frontend/src/views/EmbedChat.vue:8078-8083`（删 computed）、`:1261`（删传参）
- Modify: `frontend/src/views/AgentDebug.vue:3031-3036`（删 computed）、`:5349`（删传参）

- [ ] **Step 1: 先精确读取待改区域的当前内容**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform/frontend && sed -n '131,145p' src/components/RagPreviewDrawer.vue && echo "--- 抽屉副标题 ---" && sed -n '13,16p' src/components/RagPreviewDrawer.vue && echo "--- 模板分支 ---" && sed -n '56,90p' src/components/RagPreviewDrawer.vue
```
Expected: 看到 `props` 定义（含 `isOfficeDocument: boolean`）、副标题行、`v-if="isOfficeDocument"` 分支与 `<iframe v-else-if=...>`。

> 以实际读到的行号为准调整后续替换。**不要凭本计划的记忆行号直接改**。

- [ ] **Step 2: 修改抽屉 script 部分**

在 `frontend/src/components/RagPreviewDrawer.vue` 的 `<script setup lang="ts">` 内：

删除 `props` 中的 `isOfficeDocument: boolean;` 一行，并在 `import { ref, watch } from "vue";` 处补全导入，最终导入区与新增 computed 如下：

```ts
import { computed, ref, watch } from "vue";
import DocumentViewer from "@/components/embed/DocumentViewer.vue";
import { useDarkThemeFlag } from "@/composables/useDarkThemeFlag";
import {
  canPreviewWithDocumentViewer,
  shouldDownloadInsteadOfPreview,
  resolveDocumentViewerMime,
} from "@/utils/workspaceFilePreview";

const modelValue = defineModel<boolean>({ default: false });

const props = defineProps<{
  docName: string;
  pageNo: string | number;
  fileUrl: string;
  content: string;
}>();

/** 6 种组件支持的 Office 格式：交给 DocumentViewer 内联预览 */
const canPreview = computed(() => canPreviewWithDocumentViewer(props.docName));
/** 只有旧版二进制 .ppt 会命中：保持「暂不支持 + 下载」 */
const isDownloadOnlyOffice = computed(() => shouldDownloadInsteadOfPreview(props.docName));
/** 抽屉跟随全局暗色主题（本组件已有 dark: 适配）；composable 必须在 setup 顶层调用一次 */
const isDark = useDarkThemeFlag();
const viewerTheme = computed<'light' | 'dark'>(() => (isDark.value ? 'dark' : 'light'));
/** Office 不跳页，文案不能声称「第 N 页」 */
const subtitle = computed(() =>
  canPreview.value ? 'RAG 关联原档预览' : `第 ${props.pageNo} 页 RAG 关联原档智能高亮预览`,
);
```

- [ ] **Step 3: 修改抽屉模板部分**

**（a）副标题**改为绑定 computed。找到原来写死的那行（内容形如 `第 {{ pageNo }} 页 RAG 关联原档智能高亮预览`），替换为：

```vue
          <p class="text-[11px] text-gray-400 mt-0.5">
            {{ subtitle }}
          </p>
```

**（b）预览分支**：在 `v-if="isOfficeDocument"` 那个 `<div>` **之前**插入 `DocumentViewer` 分支，并把原分支的 `v-if` 改为 `v-else-if`：

```vue
            <DocumentViewer
              v-if="canPreview && modelValue && fileUrl"
              :url="fileUrl"
              :filename="docName"
              :meta="{ filename: docName, mime: resolveDocumentViewerMime(docName) }"
              :theme="viewerTheme"
              @fallback-download="downloadOriginalFile"
            />
            <div v-else-if="isDownloadOnlyOffice" class="p-8 text-center max-w-sm space-y-4 flex flex-col items-center">
              <!-- 原有的「暂不支持在线预览」提示与下载按钮，内容保持不变 -->
            </div>
```

⚠️ **顺序至关重要**：`DocumentViewer` 分支必须排在下载提示与 iframe **之前**，否则 6 种 Office 会被后面的分支截走。

- [ ] **Step 4: 清理两个宿主的重复判定**

在 `frontend/src/views/EmbedChat.vue`：

删除这段 computed（约在第 8078-8083 行，即从 `const isOfficeDocument = computed(() => {` 到对应的 `});`）：

```ts
const isOfficeDocument = computed(() => {
  const name = ragPreviewDocName.value.toLowerCase();
  return name.endsWith(".doc") || name.endsWith(".docx") || 
         name.endsWith(".xls") || name.endsWith(".xlsx") || 
         name.endsWith(".ppt") || name.endsWith(".pptx");
});
```

并删除 `<RagPreviewDrawer>` 上的传参行（约第 1261 行）：

```vue
      :is-office-document="isOfficeDocument"
```

在 `frontend/src/views/AgentDebug.vue`：做完全相同的两处删除（computed 约在 3031-3036 行，传参约在 5349 行）。

> **已核实（无需再查）**：删除 `isOfficeDocument` 后 `ragPreviewDocName` **仍然被使用**——`EmbedChat.vue:1257` 把它绑到 `:doc-name`、`:8094` 给它赋值；`AgentDebug.vue:5345` / `:3047` 同理。因此它不会变成未使用变量，不需要额外清理。

- [ ] **Step 5: 类型检查**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform/frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b 2>&1 | grep "error TS" | sort > /tmp/tsc-task2.log; wc -l < /tmp/tsc-task2.log; grep -E "RagPreviewDrawer|EmbedChat|AgentDebug" /tmp/tsc-task2.log
```
Expected: 总数 `62`；`RagPreviewDrawer` 与 `AgentDebug` **无输出**。`EmbedChat` 若出现，须核对是否与本次改动相关（基线中 `EmbedChat` 有 11 条既有错误）。

- [ ] **Step 6: 跑既有契约测试，确认没破坏画布那版**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend/test_workspace_document_preview_contract.py tests/frontend/test_canvas_legacy_ppt_download_contract.py tests/frontend/test_document_viewer_contract.py tests/frontend/test_canvas_document_badges_contract.py
```
Expected: 全部 PASS

- [ ] **Step 7: 提交（由用户执行）**

```bash
git add frontend/src/components/RagPreviewDrawer.vue frontend/src/views/EmbedChat.vue frontend/src/views/AgentDebug.vue
git commit -m "feat(rag): RAG 引用预览抽屉支持 Office 文档内联预览

- 抽屉内部自判格式，删除两个宿主里重复的 isOfficeDocument
- 6 种 Office 格式交给 DocumentViewer，.ppt 保持下载
- PDF 与其余格式的 iframe 分支原样保留
- 副标题按格式区分，避免 Office 不跳页却声称『第 N 页』"
```

---

## Task 3: 知识库管理页预览弹窗接入组件

**Files:**
- Modify: `frontend/src/views/KnowledgeBaseManagement.vue`（script 增加 computed 与下载兜底；模板 `:2865` 起的 Modal 内插入组件）

- [ ] **Step 1: 先精确读取待改区域**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform/frontend && echo "--- 导入区 ---" && grep -n "^import" src/views/KnowledgeBaseManagement.vue | head -20 && echo "--- previewDocUrl ---" && sed -n '1271,1278p' src/views/KnowledgeBaseManagement.vue && echo "--- 预览弹窗 ---" && sed -n '2864,2878p' src/views/KnowledgeBaseManagement.vue
```
Expected: 看到既有 import、`previewDocUrl` computed、以及含 `<iframe>` 的 `Modal`。

- [ ] **Step 2: 在 script 中新增 computed 与下载兜底**

在 `previewDocUrl` 定义之后（约第 1278 行后）插入：

```ts
/** 6 种 Office 格式交给 DocumentViewer；其余（含 PDF）继续走 iframe */
const canPreviewSelectedDocument = computed(() =>
  canPreviewWithDocumentViewer(selectedDocument.value?.name || ''),
)

/** 组件内部下载兜底的接收方；沿用本文件既有的「临时 a 标签」写法 */
const downloadPreviewDocument = () => {
  if (!previewDocUrl.value) return
  const link = document.createElement('a')
  link.href = previewDocUrl.value
  link.download = selectedDocument.value?.name || ''
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}
```

并在文件顶部 import 区补上：

```ts
import DocumentViewer from '@/components/embed/DocumentViewer.vue'
import { canPreviewWithDocumentViewer, resolveDocumentViewerMime } from '@/utils/workspaceFilePreview'
```

⚠️ 若该文件已从 `vue` 导入 `computed`，不要重复导入。

- [ ] **Step 3: 在预览弹窗内插入组件**

找到 `Modal` 内包着 iframe 的 `<div class="h-[70vh] w-full">`，改为：

```vue
      <div class="h-[70vh] w-full">
        <DocumentViewer
          v-if="canPreviewSelectedDocument && showDocPreview && previewDocUrl"
          :url="previewDocUrl"
          :filename="selectedDocument?.name || ''"
          :meta="{ filename: selectedDocument?.name || '', mime: resolveDocumentViewerMime(selectedDocument?.name || '') }"
          theme="light"
          @fallback-download="downloadPreviewDocument"
        />
        <iframe
          v-else-if="showDocPreview && previewDocUrl"
          :src="previewDocUrl"
          class="w-full h-full rounded-xl border border-gray-200 bg-white"
          title="文档原文件预览"
        ></iframe>
      </div>
```

⚠️ **主题固定 `light`**：本文件全文 **0 处 `dark:` 适配**（已实测），跟随全局暗色会让弹窗与页面观感割裂。且 `DocumentViewer` 的 `theme` prop 只接受 `'light' | 'dark'`，**没有 `'system'`**。

- [ ] **Step 4: 类型检查**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform/frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b 2>&1 | grep "error TS" | sort > /tmp/tsc-task3.log; wc -l < /tmp/tsc-task3.log; grep "KnowledgeBaseManagement" /tmp/tsc-task3.log
```
Expected: 总数 `62`；`KnowledgeBaseManagement` **无输出**

- [ ] **Step 5: 提交（由用户执行）**

```bash
git add frontend/src/views/KnowledgeBaseManagement.vue
git commit -m "feat(knowledge): 知识库预览弹窗支持 Office 文档内联预览

- 6 种 Office 格式交给 DocumentViewer，PDF 等保持 iframe
- 主题固定 light：该页面无暗色适配"
```

---

## Task 4: 补契约测试并做全量验证

**Files:**
- Create: `tests/frontend/test_rag_document_preview_contract.py`

- [ ] **Step 1: 写契约测试（静态断言 + 包装 Node 行为测试）**

创建 `tests/frontend/test_rag_document_preview_contract.py`：

```python
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 行为验证：静态断言看不出「.pptx 会不会被 .ppt 规则误伤」，这里用 Node 真跑纯函数
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_document_preview_formats_behaviour_via_node():
    """覆盖静态断言覆盖不到的核心风险：扩展名判定（含 .ppt/.pptx 子串陷阱）。"""
    script = Path("tests/frontend/document_preview_formats.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "文档预览格式判定测试全部通过" in proc.stdout


def test_shared_helper_is_exported_and_dependency_free():
    source = _source("frontend/src/utils/documentPreviewFormats.ts")

    assert "export function canPreviewWithDocumentViewer(" in source
    assert "DOCUMENT_VIEWER_EXTENSIONS.has(" in source
    # 零运行时依赖：不得引入 axios 或 @/ 别名导入（否则 Node 无法直接加载）
    assert "from '@/utils/axios'" not in source
    assert "from 'axios'" not in source


def test_workspace_utils_reexports_formats_module():
    source = _source("frontend/src/utils/workspaceFilePreview.ts")

    assert "export * from './documentPreviewFormats'" in source


def test_rag_drawer_uses_document_viewer_and_self_judges():
    source = _source("frontend/src/components/RagPreviewDrawer.vue")

    assert "DocumentViewer" in source
    assert "canPreviewWithDocumentViewer" in source
    # 下载提示分支仍在（承接 .ppt），否则 .ppt 会退化为空白
    assert "shouldDownloadInsteadOfPreview" in source
    # 关闭抽屉必须真正卸载组件，否则会在后台继续加载文件
    assert "canPreview && modelValue && fileUrl" in source
    # 副标题按格式区分
    assert "subtitle" in source
    # 不再依赖父组件传入判定
    assert "isOfficeDocument" not in source


def test_rag_drawer_keeps_iframe_for_pdf_and_others():
    source = _source("frontend/src/components/RagPreviewDrawer.vue")

    assert "<iframe" in source
    assert "#page=" in source


def test_knowledge_modal_uses_document_viewer_and_keeps_iframe():
    source = _source("frontend/src/views/KnowledgeBaseManagement.vue")

    assert "DocumentViewer" in source
    assert "canPreviewWithDocumentViewer" in source
    assert "canPreviewSelectedDocument && showDocPreview && previewDocUrl" in source
    # iframe 仍是 else 分支，PDF 不受影响
    assert "v-else-if=\"showDocPreview && previewDocUrl\"" in source
    # 该页面无暗色适配，主题固定 light
    assert 'theme="light"' in source


def test_duplicated_office_judgement_removed_from_hosts():
    for path in ("frontend/src/views/EmbedChat.vue", "frontend/src/views/AgentDebug.vue"):
        source = _source(path)
        assert "isOfficeDocument" not in source, path
        assert ":is-office-document" not in source, path
```

- [ ] **Step 2: 跑新契约测试**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend/test_rag_document_preview_contract.py tests/frontend/test_document_preview_formats_contract.py -v
```
Expected: 全部 PASS

> ⚠️ `test_document_preview_formats_contract.py` 是本计划 Task 1 的 Step 1 声明的文件。若在 Task 1 中尚未创建，请在此补上（内容包装 `document_preview_formats.mjs`，与上面 `test_document_preview_formats_behaviour_via_node` 相同即可）。

- [ ] **Step 3: 跑全量前端契约测试**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && .venv/bin/python -m pytest --confcutdir=tests/frontend -q tests/frontend 2>&1 | tail -5
```
Expected: `0 failed`。**总数会浮动**（有并发任务在新增用例），判据是**没有 failures**。

- [ ] **Step 4: 最终类型检查**

Run:
```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform/frontend && NODE_OPTIONS="--max-old-space-size=6144" npx vue-tsc -b 2>&1 | grep "error TS" | sort > /tmp/tsc-final.log; wc -l < /tmp/tsc-final.log; grep -E "documentPreviewFormats|workspaceFilePreview|RagPreviewDrawer|KnowledgeBaseManagement" /tmp/tsc-final.log
```
Expected: 总数 `62`；四个文件**无输出**

- [ ] **Step 5: 记录到 tests/CHECKLIST.md**

`tests/CHECKLIST.md` 是 **5 列表格**（不是复选框列表），在表格**末尾追加一行**，列结构为：
`| 特性名 | 涉及核心文件 | 方案设计与测试闭环说明 | 验收状态 | 交付日期 |`

⚠️ 单元格内如出现竖线必须转义为 `\|`。追加后校验列数：

```bash
cd /Users/chenxiaolong/workspace/nanzi-ai-agent-platform && grep "RAG 链路 Office 预览" tests/CHECKLIST.md | perl -ne 'print scalar(() = /(?<!\\)\|/g), "\n"'
```
Expected: `6`（= 5 列）

- [ ] **Step 6: 提交（由用户执行）**

```bash
git add tests/frontend/test_rag_document_preview_contract.py tests/frontend/test_document_preview_formats_contract.py tests/CHECKLIST.md
git commit -m "test(rag): 补 RAG 与知识库 Office 预览的契约与行为测试"
```

---

## 自检结果

**1. Spec 覆盖检查**

| Spec 章节 | 对应任务 |
| --- | --- |
| §6.1 新增共享判定函数 | Task 1 |
| §6.2 RAG 抽屉（props 变更、自判、分支、副标题、主题）| Task 2 |
| §6.3 知识库弹窗（组件 + iframe else + light 主题 + 下载兜底）| Task 3 |
| §6.4 两个宿主清理 | Task 2 Step 4 |
| §6.5 三个细节（卸载、分支顺序、meta.mime）| Task 2 Step 3、Task 3 Step 3 |
| §7 测试策略 | Task 1、Task 4 |
| §9 静态验收标准 | Task 2 Step 5、Task 3 Step 4、Task 4 Step 3/4 |

**2. 占位符扫描**：无 TBD / TODO / 「类似 Task N」；每个代码步骤都给出了完整代码。

**3. 类型一致性核对**

- `canPreviewWithDocumentViewer(name: string): boolean` —— Task 1 定义，Task 2/3 调用，签名一致。
- `DocumentViewer` props 为 `{ url: string; filename: string; meta?: CanvasDocumentMeta; theme: 'light' | 'dark' }` —— Task 2/3 的用法与之一致（`theme` 无 `'system'`）。
- `CanvasDocumentMeta` 的字段是 `{ filename: string; mime?: string; size?: number }` —— `meta` 传参字段名一致。
- `viewerTheme` 与 `theme`、`subtitle` 与 `canPreview` 在 Task 2 内部命名前后一致。

**4. 已知偏差与风险**

- Task 1 Step 6 已预警：re-export 会让 `test_workspace_document_preview_contract.py` 中针对 `workspaceFilePreview.ts` 的 Set 字面量切片断言失败，需把该测试的目标文件改为 `documentPreviewFormats.ts`。
- 本计划所有 Python 契约测试都是**源码子串匹配**，无法覆盖「API 用错」。组件真实渲染、资产复制、跨站鉴权**均无运行时验证**，且**依赖画布那版先通过 `./dev.sh` 实测**（同一条渲染链路、同一个组件）。
- **未执行**：`./dev.sh`、`npm run build`、任何部署脚本（由用户在控制台执行）。
