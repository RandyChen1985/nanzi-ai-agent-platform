# RAG 链路 Office 文档预览接入 file-viewer —— 设计文档

- 日期：2026-10-08
- 状态：设计已确认，待实现
- 关联：[画布文档预览设计](./2026-10-08-canvas-document-preview-design.md)（同名组件的上一处接入，本设计复用它）

---

## 1. 背景与目标

### 1.1 现状

平台有两处「原档预览」入口涉及 RAGFlow 知识库文档，二者都直接用 `<iframe :src="fileUrl">` 渲染：

| 入口 | 文件 | 触发方式 |
| --- | --- | --- |
| RAG 引用预览抽屉 | `frontend/src/components/RagPreviewDrawer.vue` | 聊天消息里的引用，打开侧边抽屉 |
| 知识库管理页预览弹窗 | `frontend/src/views/KnowledgeBaseManagement.vue` | 知识库管理页「📄 预览原文件」按钮 |

iframe 对 PDF 是有效的（浏览器原生支持，抽屉还借 `#page=N` 实现跳到引用页），但对 **Office 文档无法渲染**：

- RAG 抽屉：`isOfficeDocument` 为真时直接显示「Office 文档暂不支持在线预览」+ 下载按钮。
- 知识库弹窗：没有判定，直接塞 iframe —— 表现为空白，或触发浏览器下载。

### 1.2 目标

让这两处的 Office 文档**在页面内直接预览**，复用画布那版已接入的 `DocumentViewer.vue`。

### 1.3 非目标（明确不做）

- **不改 PDF 的现有实现**。PDF 继续走 iframe，保住 `#page=N` 跳页能力，零回归。
- **不支持旧版二进制 `.ppt`**。组件官方能力声明（`presentation-openxml`）只列 `pptx/pptm/potx/potm/ppsx/ppsm`，不含 `ppt`；且 `.ppt` 需要 `@file-viewer/renderer-ppt`，该链会引入带内置水印、许可为 `SEE LICENSE IN LICENSE` 的 `@file-viewer/ppt`。
- **不做文件定位（页码高亮）**。组件虽有 `scrollToAnchor()` / `collectDocumentAnchors()` 且 `FileViewerDocumentAnchor` 含 `page?: number`，但该字段是可选、Office 文档能否稳定取得页码未经实测，本次不纳入。
- **不改后端**（依据见 §5）。

---

## 2. 需求澄清结论

设计前逐项与用户确认，结论如下：

| 议题 | 结论 | 理由 |
| --- | --- | --- |
| 改动范围 | **只把 Office 换成组件，PDF 保持 iframe 不动** | 改动最小，PDF 的 `#page` 跳页能力零回归 |
| 抽屉副标题文案 | **按格式区分** | 现在写死「第 N 页 …」，Office 不跳页，不改会误导用户 |
| 格式边界 | **仅组件官方支持的格式走预览**，`.ppt` 等保持现状 | 语义清晰，避免「点了预览却报错/空白」 |
| 判定逻辑归属 | **抽屉内部自判**（方案 A） | 判定逻辑从 4 处收敛到 1 处，两个宿主净减代码 |
| 知识库弹窗 | **纳入本次范围** | 与 RAG 抽屉同一个后端接口、同一个组件，边际成本极低 |
| 共享判定函数 | **新增 `canPreviewWithDocumentViewer()`** | 接入点已达 3~4 处，避免重演画布那版「多份实现漂移」的坑 |

---

## 3. 可复用的现有资产

画布那版已建立的资产本次几乎全部可直接复用：

| 资产 | 位置 | 用途 |
| --- | --- | --- |
| `DocumentViewer.vue` | `frontend/src/components/embed/DocumentViewer.vue` | 预览组件（懒加载、axios 取 Blob、`new File` 包装、`AbortController` 卸载中断、错误态 + 下载兜底） |
| `DOCUMENT_VIEWER_EXTENSIONS` | `frontend/src/utils/workspaceFilePreview.ts:22` | 6 种可预览格式，已导出 |
| `resolveDocumentViewerMime()` | `workspaceFilePreview.ts:36` | 扩展名 → MIME |
| `shouldDownloadInsteadOfPreview()` | `workspaceFilePreview.ts:46` | 命中「是 Office 但不能预览」（目前只有 `.ppt`） |
| `getWorkspaceFileExtension()` | `workspaceFilePreview.ts:63` | 取扩展名 |
| `useDarkThemeFlag()` | `frontend/src/composables/useDarkThemeFlag.ts:14` | 返回 `Ref<boolean>`，供主题跟随 |

**本次唯一新增的工具**是一个语义中性的判定函数（见 §6.1）。

---

## 4. 分派矩阵

| 格式 | RAG 抽屉（改后） | 知识库弹窗（改后） |
| --- | --- | --- |
| `.docx` `.doc` `.xlsx` `.xls` `.xlsm` `.pptx` | **`DocumentViewer` 内联预览** | **`DocumentViewer` 内联预览** |
| `.ppt` | 保持「暂不支持 + 下载」提示 | 保持 iframe（该入口原本就无提示，不新增） |
| `.pdf` | 保持 iframe（含 `#page=N`） | 保持 iframe |
| 其他（图片/文本等） | 保持 iframe | 保持 iframe |

说明：抽屉保留原有提示分支是为了**不退化**既有行为；弹窗只做最小插入，不为 `.ppt` 新增提示（YAGNI）。

---

## 5. 后端零改动的依据

两处入口用的是**同一个后端接口**：

```
GET /api/portal/ragflow/datasets/{dataset_id}/documents/{document_id}/file
```

实现见 `app/api/portal/endpoints/ragflow.py:774-798`（`_build_ragflow_document_file_response`）。它已经满足组件的全部输入要求：

1. **`Content-Type` 被强力纠正** —— `mimetypes.guess_type(filename)` 猜到类型就覆盖，`.pdf` 再强制为 `application/pdf`。
2. **`Content-Disposition: inline`** —— 不是 `attachment`，且带 `filename*=UTF-8''…`（中文文件名安全）。
3. **返回原始文件字节** —— 由 `RagFlowClient.download_document()` 直接取回原档，不经过解析产物。

鉴权链路同样无需改动：

4. **前端 axios 无 `baseURL`**（`frontend/src/utils/axios.ts:9-16`），`fileUrl` 是同源相对路径 `/api/portal/ragflow/...`，开发态由 Vite 代理转发，生产态直打同源后端。
5. **`withCredentials: true`** —— 同源 HttpOnly Cookie（`portal_session`）自动携带；嵌入场景由调用方按需注入 Bearer。

> 与画布那次的差异：画布走的是工作区文件接口，本次走 RAGFlow 代理接口。两者都被证实满足需求，因此**后端同样零改动**。

---

## 6. 实现设计

### 6.1 新增共享判定函数

`frontend/src/utils/workspaceFilePreview.ts` 新增导出：

```ts
/**
 * 该文件能否交给 DocumentViewer 预览。
 * 各接入点（画布、RAG 抽屉、知识库预览弹窗）统一调用，
 * 避免同一判定出现多份实现后彼此漂移。
 */
export function canPreviewWithDocumentViewer(name: string): boolean {
  return DOCUMENT_VIEWER_EXTENSIONS.has(getWorkspaceFileExtension(name))
}
```

### 6.2 RAG 抽屉（`RagPreviewDrawer.vue`）

**Props 变更**：删除 `isOfficeDocument: boolean`（改由内部自判）。

**新增内部判定**：

```ts
import { canPreviewWithDocumentViewer, shouldDownloadInsteadOfPreview,
         resolveDocumentViewerMime } from '@/utils/workspaceFilePreview'
import { useDarkThemeFlag } from '@/composables/useDarkThemeFlag'
import DocumentViewer from '@/components/embed/DocumentViewer.vue'
import { computed } from 'vue'

// 6 种能预览的格式
const canPreview = computed(() => canPreviewWithDocumentViewer(props.docName))
// 只有 .ppt 会命中：是 Office 但不能预览
const isDownloadOnlyOffice = computed(() => shouldDownloadInsteadOfPreview(props.docName))
// 抽屉跟随全局暗色主题（该组件已有 21 处 dark: 适配）
// 注意：composable 必须在 setup 顶层调用一次，不能写在 computed 内部
const isDark = useDarkThemeFlag()
const theme = computed(() => (isDark.value ? 'dark' : 'light'))
```

**模板分支**（顺序关键——`DocumentViewer` 必须排在下载提示之前，否则 6 种 Office 会被提示分支截走）：

```vue
<DocumentViewer
  v-if="canPreview && modelValue && fileUrl"
  :url="fileUrl"
  :filename="docName"
  :meta="{ filename: docName, mime: resolveDocumentViewerMime(docName) }"
  :theme="theme"
  @fallback-download="downloadOriginalFile"
/>
<div v-else-if="isDownloadOnlyOffice" …>现有的「暂不支持 + 下载」</div>
<iframe v-else-if="modelValue && fileUrl" :src="`${fileUrl}#page=${pageNo}`" … />
```

**副标题按格式区分**：

```ts
const subtitle = computed(() =>
  canPreview.value ? 'RAG 关联原档预览' : `第 ${props.pageNo} 页 RAG 关联原档智能高亮预览`)
```

### 6.3 知识库预览弹窗（`KnowledgeBaseManagement.vue`）

在既有 `Modal`（`:2865`）内插入，**保留 iframe 作为 else 分支**：

```vue
<div class="h-[70vh] w-full">
  <DocumentViewer
    v-if="canPreviewSelectedDocument && showDocPreview && previewDocUrl"
    :url="previewDocUrl"
    :filename="selectedDocument?.name"
    :meta="{ filename: selectedDocument?.name, mime: resolveDocumentViewerMime(selectedDocument?.name || '') }"
    theme="light"
    @fallback-download="downloadPreviewDocument"
  />
  <iframe v-else-if="showDocPreview && previewDocUrl" :src="previewDocUrl" … />
</div>
```

```ts
const canPreviewSelectedDocument = computed(() =>
  canPreviewWithDocumentViewer(selectedDocument.value?.name || ''))

// 组件内部下载兜底的接收方；沿用本文件既有的「临时 a 标签」写法
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

**主题固定 `light`**：该页面全文 **0 处 `dark:` 适配**，跟随全局暗色会让弹窗与页面观感割裂。

### 6.4 两个宿主的清理

`EmbedChat.vue`（删 `:8078` 的 computed、`:1261` 的传参）与 `AgentDebug.vue`（删 `:3031` 的 computed、`:5349` 的传参）各自移除重复的 `isOfficeDocument` 判定。

### 6.5 必须处理的细节

1. **抽屉关闭要真正卸载**。抽屉用 `v-show` + `translate-x-full` 隐藏，内容仍留在 DOM；而 `DocumentViewer` **挂载即发起请求**。因此 `v-if` 必须带 `modelValue`，否则会出现「抽屉已关，文件仍在后台加载」。卸载时组件内部的 `AbortController` 会中断请求。
2. **分支顺序**。见 §6.2 —— `DocumentViewer` 分支必须先于下载提示与 iframe。
3. **`meta.mime` 的作用**。组件靠文件名扩展名分派 renderer（`new File([...], name)` 已在画布版修复），`mime` 是辅助信号，两者都传。

---

## 7. 测试策略

沿用项目现有的**纯源码契约测试**风格（`tests/frontend/`，`pytest --confcutdir=tests/frontend`），新增一个契约测试文件，覆盖：

1. `canPreviewWithDocumentViewer` 已导出，且实现基于 `DOCUMENT_VIEWER_EXTENSIONS`。
2. `RagPreviewDrawer.vue`：引用了 `DocumentViewer`；使用 `canPreviewWithDocumentViewer`；`.ppt` 仍走下载提示（`shouldDownloadInsteadOfPreview` 仍存在）；副标题按格式区分；`v-if` 带 `modelValue`。
3. `KnowledgeBaseManagement.vue`：弹窗内引用 `DocumentViewer`；保留 iframe 作为 else 分支；主题固定 `light`。
4. `EmbedChat.vue` / `AgentDebug.vue`：不再存在重复的 `isOfficeDocument` 判定。

**⚠️ 测试能力的诚实声明**：以上全部是**源码子串匹配**，能证明「代码写对了」，但**完全无法发现「API 用错了」**——画布那版就因此漏掉了「传裸 Blob 导致 renderer 无法分派」的真实缺陷。**真实渲染效果只能靠 `./dev.sh` 人工实测。**

---

## 8. 风险与未验证项

| 风险 | 等级 | 说明与缓解 |
| --- | --- | --- |
| 组件真实渲染效果未经任何运行时验证 | **高** | 与画布共用同一条渲染链路；**画布若渲染不通，本改动同样不通**。前置依赖：先完成画布的 `./dev.sh` 实测 |
| RAGFlow 代理返回的是否为标准 Office 二进制 | 低 | 后端 `download_document()` 取回原档，代码可确认；但未端到端实测 |
| `.ppt` 在知识库弹窗仍是空白/下载 | 低 | 已知并接受；该入口原本无提示，行为不变 |
| Office 文档拿不到页码，无法定位到引用页 | 中 | 本次明确不做；抽屉文案已按格式区分，不会误导 |
| 组件内部样式/资产缺失 | 低 | `DocumentViewer` 已引入 `file-viewer3.css`；资产由 vite 插件复制 |

---

## 9. 验收标准

**静态验收**（我完成）：

- [ ] 共享判定函数存在且被两个入口调用
- [ ] 两个入口在 Office 格式下渲染 `DocumentViewer`
- [ ] `.ppt` 行为不变（抽屉仍是提示 + 下载）
- [ ] PDF 与其余格式的 iframe 分支未被改动
- [ ] 两个宿主的重复判定已删除
- [ ] 前端契约测试全绿、`vue-tsc -b` 错误数不超过基线（62 条）

**运行时验收**（需用户在控制台执行 `./dev.sh`，我**不代跑**）：

- [ ] RAG 抽屉：打开一个 `.docx` 引用，文档内容正常渲染
- [ ] RAG 抽屉：`.ppt` 仍显示「暂不支持 + 下载」
- [ ] RAG 抽屉：`.pdf` 仍能跳到引用页
- [ ] RAG 抽屉：副标题对 Office 显示中性文案
- [ ] 知识库管理页：「预览原文件」对 `.docx` 能渲染
- [ ] 知识库管理页：`.pdf` 预览无回归
- [ ] 关闭抽屉后，Network 面板无残留的文档请求

---

## 10. 后续（本次不做）

1. **文件定位**：抽屉恢复「跳到引用页」能力（`collectDocumentAnchors()` + `scrollToAnchor()`），需先实测 Office 的 `anchor.page` 是否被填充。
2. **其余入口收敛**：聊天附件列表（`EmbedChat.vue:612`）的 Office 仍只能新标签页下载，可按同一模式接入。
3. **分派逻辑收敛**：画布侧的 `openWorkspaceFileInCanvas` 与 `useWorkspaceCanvas.ts` 的 `canvas://file` 分支仍是两份独立实现，建议收敛为单一 `planWorkspacePreview()` 纯函数（详见画布计划文档「遗留架构问题」）。
