# 画布文档预览接入 file-viewer

**日期：** 2026-10-08
**需求（用户原话）：** 「那我们集成吧，是不是集成后，所有文档预览都可以走这个组件？」

---

## 1. 目标

聊天画布的「工作区文件打开」链路目前对 Office 文档（docx/xlsx/pptx 等）**只能下载、无法预览**：`openWorkspaceFileInCanvas` 与 `useWorkspaceCanvas` 各有一份 Office 分支，命中后只弹提示并触发下载。本设计在画布内引入浏览器原生文档预览能力，让 Office 与 PDF 在工作区画布中直接可读，并统一到同一个预览外壳。

**关键澄清（回答需求原话）：不是「所有文档预览」都改走该组件。** 画布现有自研渲染中有若干该组件不具备的能力，必须保留：

| 类型 | 保留原因 |
| --- | --- |
| `code`（含 Markdown） | 具备**编辑 + 执行**（`runnable`，真跑 Python/Shell）与回写工作区能力，组件为只读 |
| `html` | 具备「效果预览 / 源代码」双 tab 与保存编辑能力 |
| `csv` | 具备自研表格与**透视表**分析，组件为只读表格 |
| `mermaid` | 已有专用渲染器与拖拽缩放，组件为只读 |
| `compare` | 具备「AI 分析差异」项目特有动作 |

因此本设计的准确表述是：**「目前无法预览的文档格式」改走该组件**（Office 6 种 + PDF），而非全量替换。

## 2. 范围

**做：**

- 画布内渲染 Office 6 种格式：`.docx` `.doc` `.xlsx` `.xls` `.xlsm` `.pptx`；
- 画布内 PDF 改用同一组件渲染（替换现有 `<iframe :src="blobUrl">`）；
- 新增隔离子组件 `DocumentViewer.vue` 承载该组件全部职责；
- 收敛分散在 5 处的画布类型定义到单一来源；
- 新增前端契约测试。

**明确不做（已与用户逐项确认）：**

- **旧版二进制 `.ppt`**：渲染它需要 `@file-viewer/renderer-ppt`，该包传递依赖带内置水印、许可为 `SEE LICENSE IN LICENSE` 的 `@file-viewer/ppt`。本期不引入，`.ppt` 保持「下载」行为。
- **后端零改动**：不改 `/preview` 扩展名白名单、不拆「可预览 / 可写」常量、不给 `GZipMiddleware` 加路径跳过、不改 Office 的 `Content-Disposition`。原因见第 5 节——组件以 Blob 为入参后，这四个问题在本期链路上都不触发。
- **知识库 / RAG 原档预览**（`RagPreviewDrawer`、`KnowledgeBaseManagement` Modal）、**检索测试页**、**门户侧边栏**、**消息附件气泡**、**产物抽屉**：本期不动，留待后续复用本次的 `DocumentViewer`。
- **PDF Range 分片优化**：见第 9 节已知取舍。
- 暗色主题的自定义配色、工具栏按钮定制、打印/下载前权限预检（`operation-before`）。

## 3. 选型依据

**依赖（3 个包）：**

```
@file-viewer/vue3              （运行时，peer vue >=3.3 <4）
@file-viewer/preset-standard   （渲染能力，纯 Apache-2.0）
@file-viewer/vite-plugin       （devDependency，复制 Worker/WASM/字体资产）
```

**为何不用 `preset-office`（重要）：** 其直接依赖看似干净，但**传递依赖**会引入水印包：

```
preset-office → renderer-presentation → renderer-ppt → @file-viewer/ppt
                                                        ↑ SEE LICENSE IN LICENSE + 内置水印
```

`preset-standard` 走 `renderer-pptx → @file-viewer/pptx`（Apache-2.0），全树无该包。

**为何不用 `preset-all` / `vue3-full`：** 会引入 `renderer-cad → @flyfish-dev/cad-viewer → dwf-viewer`（**AGPL-3.0-only**），对本项目构成许可风险。

**许可结论：** `preset-standard` 全树为 Apache-2.0，第三方依赖为 MIT / ISC / BSD / OFL-1.1 / (MPL-2.0 OR Apache-2.0)，无 AGPL、无「SEE LICENSE IN LICENSE」。已覆盖 `renderer-word`（含 `.doc`）、`renderer-spreadsheet`（含 `.xls`）、`renderer-pptx`、`renderer-pdf`，正好满足本期所需。

**兼容性已核实：** 组件 peer 要求 `vue >=3.3 <4`，项目为 `^3.5.24` ✅；组件声明支持 Vite 5–7，项目为 Vite 7 ✅；项目无自定义 `manualChunks`，与该插件的 chunk 分组不冲突 ✅。

## 4. 架构与组件边界

### 4.1 新增 `frontend/src/components/embed/DocumentViewer.vue`

单一职责：把「一个需要鉴权的文档 URL」渲染成可读预览。

```ts
interface DocumentViewerProps {
  url: string             // /api/v1/chat/fs/preview?path=...（走 axios，自动带鉴权）
  filename: string        // 判断格式 + 展示
  mime?: string
  theme: 'light' | 'dark'
}
// emits: error(message) / fallback-download
```

内部三件事：

1. `defineAsyncComponent` 懒加载 `@file-viewer/vue3`——该组件及其 renderer **不得进入首屏包**；
2. 自持 Blob 获取：`axios.get(url, { responseType: 'blob', signal })`，`onUnmounted` 时 `abort()`（现有 `AttachmentImageThumb.vue` 在 `await` 后无条件 `createObjectURL`、卸载钩子已先执行，是已知泄漏点，此处不重蹈）；
3. 以 `file: Blob` 入参交给 file-viewer，`options` 传 `{ theme, locale: 'auto', styleIsolation: 'auto' }`。

**全程不调用 `URL.createObjectURL`**：组件原生接受 `File | Blob | ArrayBuffer`（`FileViewerComponentProps.file`），Blob 直接传入即可，从根上规避对象 URL 泄漏。

`styleIsolation` 默认 `auto` 会解析为 **Shadow DOM**，因此组件样式不会污染画布，也无需为 Tailwind 适配；主题只需按项目 `useDarkThemeFlag` 传 `theme`。

### 4.2 新增 `frontend/src/types/canvas.ts`（类型唯一来源）

```ts
export type WorkspaceCanvasType =
  | 'html' | 'code' | 'mermaid' | 'pdf' | 'csv' | 'image' | 'compare' | 'document'

export interface CanvasDocumentMeta { filename: string; mime?: string; size?: number }

export interface CanvasPanelData {
  type: WorkspaceCanvasType
  title: string
  content: string
  sourcePath?: string
  langName?: string
  runnable?: boolean
  compareContent?: string
  compareTitle?: string
  documentMeta?: CanvasDocumentMeta   // 新增：组件渲染所需元信息
  downloadPath?: string               // 新增：仅用于下载，不参与 canSaveWorkspaceFile
}
```

原先分散的 5 处定义（`ChatCanvas.vue:22`、`useWorkspaceCanvas.ts:10`、`workspaceFilePreview.ts:4` 与 `:16-25`、`MessageRenderer.vue:84-90`）全部改为从本文件 import。

**为何统一成单一 union：** 现有定义已实际漂移——`ChatCanvas.vue:22`、`useWorkspaceCanvas.ts:10`、`MessageRenderer.vue:84` 三处把 `mermaid`/`compare` 纳入类型，而 `workspaceFilePreview.ts:4` 排除二者、另在 `:17` 用 `CanvasPanelData.type = WorkspaceCanvasType | 'compare' | 'mermaid'` 扩展。统一为单一 union 取多数实现的语义，`CanvasPanelData.type` 随之简化为直接引用；`resolveWorkspaceCanvasType()` 仍只返回其子集，类型合法。

`downloadPath` 独立于 `sourcePath` 的原因：`ChatCanvas` 的 `canSaveWorkspaceFile` 由 `effectiveSourcePath` 驱动，进而影响 `isCodeEditing`；若为文档复用 `sourcePath`，可能误开编辑态。用独立字段彻底规避该副作用。

### 4.3 `frontend/vite.config.ts`

追加 `fileViewerRenderers({ copyAssets: true })`，自动发现已安装 preset 并复制 Worker / WASM / 字体资产到 `public/file-viewer/`，dev 与 build 均生效。

## 5. 数据流

```
点击 .docx
  → WorkspaceBrowserDrawer 预览动作
  → emit('preview') → handleWorkspaceFilePreview
  → useWorkspaceCanvas.handleWorkspaceFilePreview
  → openWorkspaceFileInCanvas({ path, name })
       ├─ resolvedUrl = resolveFsPreviewUrl(path, cid)     ← 只计算 URL，不取 Blob
       └─ onOpen({ type: 'document', title: name, content: resolvedUrl,
                   documentMeta: { filename: name, mime },
                   downloadPath: path })
  → canvasData = payload
  → ChatCanvas 新分支（置于 :1160 的 isHtmlContent 分支之前）
       <DocumentViewer :url="data.content" :filename="data.documentMeta.filename"
                       :theme="canvasTheme"
                       @fallback-download="downloadFile" />
  → axios 取 Blob → <file-viewer :file="blob" :filename :type :options />
```

其中 `canvasTheme` 为**新增绑定**：`ChatCanvas.vue` 目前没有任何主题判断（`isDark` / `useDarkThemeFlag` 零命中），需接入既有的 `frontend/src/composables/useDarkThemeFlag.ts` 并映射为 `'light' | 'dark'`。

**四个关键决定：**

1. **Office / PDF 不再预取 Blob，只传鉴权 URL。** Blob 获取内聚到 `DocumentViewer`，避免 Blob 进入 Vue 响应式系统，也**不改动**现有单槽 `activeBlobUrl` 机制。
2. **不创建对象 URL**，理由见 4.1。
3. **`image` / `csv` / 文本类保持原样**（继续走现有 Blob URL 单槽），不碰已正常工作的代码。
4. **`.ppt` 不进预览分支**：在 `resolveWorkspaceCanvasType` 层即排除，直接走原有下载路径，不依赖组件的「未装配格式」提示。

**后端零改动的依据（对本期的完整链路）：**

| 潜在问题 | 为何本期不触发 |
| --- | --- |
| 鉴权：`<iframe src>` 无法带 Bearer，跨站 `embed_session`（`SameSite=lax`）不下发 → 401 | 改用 axios 取 Blob，凭据自动携带，跨站嵌入同样可用 |
| `GZipMiddleware` 压缩 206 响应、违反 RFC 9110 | 走 Blob 全量获取，不使用 Range |
| Office 响应带 `Content-Disposition: attachment` | `attachment` 只影响浏览器直接导航，不影响 axios/fetch |
| `TEXT_PREVIEW_EXTENSIONS` 被 `/write`、`/create-entry` 复用为可写白名单，扩预览会误开写入 | Office 扩展名本就在**独立的** `OFFICE_PREVIEW_EXTENSIONS` 中，本期无需扩展白名单 |

## 6. 错误处理与降级

三层降级，统一兜底到画布已有的「下载文件」动作条（`ChatCanvas.vue:1609-1615`）：

| 层 | 场景 | 处理 |
| --- | --- | --- |
| 1 | 动态 import 失败（资产未正确部署） | 显示降级提示 + 「下载原文件」按钮 |
| 2 | 格式未装配（`.ppt`） | **不依赖组件提示**，在分派层排除，直接走下载 |
| 3 | Blob 获取失败（网络 / 鉴权 / axios 全局 60s 超时） | `catch` → 降级提示 + 下载按钮 |

**组件不提供错误事件**（已核实 `FileViewerComponentEmits` 只含 `load-start` / `load-complete` / `unload-*` / `operation-*` / `search-change` / `location-change` / `zoom-change` / `view-state-change` / `fit-change` / `theme-change`，**无 `error`**；`FileViewerLifecycleHooks` 也只有 `onLoad*` / `onUnload*`）。因此渲染阶段的失败**由组件自带错误 UI 承担**（其消息键含 `state.error.*` / `error.load` 等），`DocumentViewer` 只负责「Blob 获取」这一层，状态收敛为 `loading | ready | error` 三态——`ready` 表示已把 Blob 交给组件，之后由组件自理。不做渲染超时兜底（YAGNI）。

`operation-before`（打印/下载前权限预检）本期不接。

**下载路径的连带调整（必须实现）：** PDF 的 `content` 由 Blob URL 变为鉴权 URL 后，`ChatCanvas.downloadFile` 现有的 `a.href = content`（`a.download` + `target="_blank"`）对 PDF 会退化为「新窗口打开」而非下载（PDF 响应为 `inline`，且跨源时 `download` 属性被忽略）。因此对 `document` / `pdf` 类型改为：有 `downloadPath` 时调用既有 `downloadWorkspaceFile(path, title)`（自行取 Blob 后触发下载），否则回落到现有 `a.href` 逻辑。

## 7. 实现点

**新增：**

| 文件 | 内容 |
| --- | --- |
| `frontend/src/components/embed/DocumentViewer.vue` | 懒加载 + Blob 获取 + 主题桥接 + 错误降级 |
| `frontend/src/types/canvas.ts` | `WorkspaceCanvasType` / `CanvasPanelData` / `CanvasDocumentMeta` |
| `tests/frontend/test_document_viewer_contract.py` | 新增契约测试，见第 8 节 |

**修改：**

| 文件 | 改动 |
| --- | --- |
| `frontend/package.json` | 新增 `@file-viewer/vue3`、`@file-viewer/preset-standard`；devDep 新增 `@file-viewer/vite-plugin` |
| `frontend/vite.config.ts` | 注册 `fileViewerRenderers({ copyAssets: true })` |
| `frontend/src/utils/workspaceFilePreview.ts` | 改为从 `types/canvas` import；新增 `DOCUMENT_VIEWER_EXTENSIONS`（6 种）；Office 分支中该 6 种改走 `type: 'document'`（不取 Blob），`.ppt` 仍走下载；PDF 分支改为只传 URL 不预取 Blob；`.doc`/`.xls`/`.xlsm` 补 MIME 映射 |
| `frontend/src/composables/chat/useWorkspaceCanvas.ts` | 类型改为 import；移除 `canvas://file` 分支中重复的 Office 下载逻辑（`:136-150`），与 util 行为对齐 |
| `frontend/src/components/embed/ChatCanvas.vue` | 类型改为 import；新增 `document` 与 `pdf` 的渲染分支（插在 `:1160` 之前）；`downloadFile` 按第 6 节调整 |
| `frontend/src/components/MessageRenderer.vue` | 类型改为 import（去重） |
| `tests/CHECKLIST.md` | 按项目惯例登记本次验证项 |

## 8. 测试与验证

**新增契约测试** `tests/frontend/test_document_viewer_contract.py`（沿用项目纯源码断言惯例）：

- `DocumentViewer.vue` 存在，且使用 `defineAsyncComponent`；
- 以 `file`（Blob）作为组件入参，而非仅 `url`；
- 文件中**不含** `URL.createObjectURL`；
- 存在 `onUnmounted` 且包含 `abort`；
- `ChatCanvas.vue` 含 `document` 渲染分支；
- `types/canvas.ts` 定义了 `WorkspaceCanvasType` 与 `documentMeta`。

**现有测试影响（已核实断言形式）：**

| 测试 | 影响 |
| --- | --- |
| `test_code_execution_canvas_contract.py:56-64` | 断言 `props.data.runnable === true`、`sourcePath?: string`、`resolveWorkspaceScriptLanguage`、`runnable: !!scriptLanguage`——均为**存在性**断言，不删除这些标识符即不破 |
| `test_attachment_image_preview.py:64` | 断言 `/api/v1/chat/fs/preview?path=` 前缀——本设计不变更该 URL 形态 |
| `test_canvas_content_save_contract.py:37`、`test_chat_canvas_resizer_contract.py`、`test_workspace_browser_view_contract.py` | 需回归确认 |

**手动验证清单**（按项目约定，由用户在控制台执行 `./dev.sh` 后验证）：

| 用例 | 期望 |
| --- | --- |
| `.docx` / `.xlsx` / `.pptx` | 画布内渲染，含工具栏；xlsx 可切换 sheet |
| `.doc` / `.xls` / `.xlsm` | 旧版与宏格式可渲染 |
| `.ppt` | 走下载，不报错、不白屏 |
| `.pdf` | 画布内渲染，**重点验证不劣于浏览器原生** |
| `.csv` / `.png` / `.py` / `.md` / mermaid / compare | 与改造前完全一致（回归） |
| `.py` 可执行、`.html` 可编辑保存 | 与改造前一致（确认未误开编辑态） |
| 暗色主题 | 组件跟随主题切换 |
| 断网 / 资产缺失 | 出现降级提示 + 下载按钮可用 |
| 跨站嵌入 iframe 场景 | 能正常渲染（证明 axios 鉴权链路生效） |
| 画布下载按钮（Office / PDF） | 真正下载文件，而非仅打开新窗口 |

**验证命令：** `pytest --confcutdir=tests/frontend`、`vue-tsc --noEmit`。

## 9. 边界与风险

- **PDF 走 Blob 因此拿不到 Range 分片**（用户已确认接受）：组件 PDF 分片加载需直接喂 URL，与本期鉴权方案冲突，故 PDF 大文件仍为全量内存加载，相比现状无改善。组件提供的 `ViewerFetchFile` 自定义加载接口保留为后续优化入口。
- **`dist` 体积增加约 7–9MB**（`assets-standard` 7.37MB + `renderer-pdf` 2.37MB 量级），资产位于 `public/file-viewer/`，不进入 JS 主包。若后续关注体积，可改用精确 renderer 装配（`renderer-word` + `renderer-spreadsheet` + `renderer-pptx` + `renderer-pdf` + `rendererMode: 'replace'`）进一步裁剪。
- **axios 全局 60s 超时**：超大文档可能中断，降级为提示 + 下载。
- **Shadow DOM 隔离是双刃剑**：样式不会污染画布，但也无法用 Tailwind 深度定制组件内部观感；如需定制须显式设 `styleIsolation: 'none'` 或走组件开放的 `messages` / 主题变量。
- **`.ppt` 的用户体验**：与现状一致（下载），但用户可能期待与其他 Office 格式同样可预览，需在提示语中说明原因。
- **首次加载延迟**：组件与资产按需加载，首次打开文档会有可见等待，需有加载态。
- **`resolveUrlPath` 一致性**：PDF `content` 改传鉴权 URL 后仍走 `ChatCanvas.resolvedContent` 的 `resolveUrlPath`（`:659-661`），跨站嵌入时能正确补全 host，此路径无需改动但要回归确认。

## 10. 后续（本期不做，已记录）

1. **知识库 / RAG 原档预览**复用同一 `DocumentViewer`：改造 `RagPreviewDrawer.vue` 可同时覆盖 `EmbedChat` 与 `AgentDebug` 两条链路；`KnowledgeBaseManagement.vue` 的 Modal + iframe 亦可替换。
2. **PDF Range 优化**：门户同源场景喂 URL 取 Range、跨站嵌入回退 Blob 的自适应策略。**前置条件**：先给 `GZipMiddleware` 加路径跳过，否则 206 响应会被压缩污染。
3. **后端止血项**（与本期解耦但值得独立推进）：拆分 `TEXT_PREVIEW_EXTENSIONS` 的「可预览 / 可写」双语义；Office 响应改为 `content_disposition_type="inline"`；路由级 `FileResponse` 补条件请求（304）；`RagPreviewDrawer` / `CitationPopover` 的 `v-html` 清洗统一。
4. **扩展格式**：`preset-standard` 已含 OFD、压缩包、邮件、图片、音视频渲染器，后续开放对应扩展名即可，无需再动依赖。
