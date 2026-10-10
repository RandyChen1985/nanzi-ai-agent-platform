# AI 产物「预览 / 下载」双入口（画布 file-viewer 预览）

**日期：** 2026-10-11
**需求（用户原话）：** 「这个目前点击就会下载吗」→「是不是可以加两个按钮，下载或预览，预览的时候画布打开，画布不是有 fileviewer 功能吗」

---

## 1. 现状与问题

「我的产出」抽屉与消息正文里的产物链接目前**只有下载一个动作**，而且 Office 文档连"点开看看"的机会都没有。

**证据链（改动前的真实行为）：**

| 环节 | 代码 | 行为 |
| --- | --- | --- |
| 抽屉列表项 | `MyArtifactsDrawer.vue:402-407` → `openArtifact`（`:175-182`） | `window.open(download_url, '_blank')`，URL 不带任何 disposition 参数 |
| 后端分派 | `chat.py:228-250` | 无 `disposition`、无 `download=true` 时：html/htm、text/plain、svg、png/jpeg/webp/gif、pdf → `inline`；**其余一律 `attachment`** |
| 结论 | — | `.docx` 的 MIME 不在 inline 白名单 → 点击即下载；html/pdf/图片反而是新标签页预览，"下载"语义不成立 |
| 消息正文 | `MessageRenderer.vue:334-343` | `<a>` 点击只在 `.pdf/.csv/.html/.htm/图片`（含链接文字后缀）时 `emit('open-canvas')`；**`.docx/.xlsx/.pptx` 不匹配任何分支**，落回浏览器默认 → 直接下载 |

画布侧其实早已具备完整预览能力：`ChatCanvas.vue` 的 `document` / `pdf` 分支（`:1246-1255`）由 `DocumentViewer.vue` 用 `@file-viewer/vue3` 渲染，且 `DocumentViewer` 走 `axios.get(url, { responseType: 'blob' })`（`:69-83`）——**`attachment` 响应头不影响 axios**，这正是本设计可行的前提（同 `2026-10-08-canvas-document-preview-design.md` 第 5 节的判断）。

## 2. 目标

1. 抽屉的每个产物提供**预览**与**下载**两个明确入口，预览走画布 file-viewer；
2. 消息正文里的产物链接语义与抽屉一致：**链接本体 = 预览，链接后另有「下载」按钮**；
3. 下载对**所有**产物类型都真的是下载（含 html/pdf/图片）；
4. 后端零改动、`.ppt` 维持下载、不引入新依赖。

## 3. 范围

**做：**

- 抽屉列表项：主体点击 = 预览，hover 显现「预览 / 下载」两个图标按钮；
- 新增生成产物 → 画布数据的分派器（判定与取内容分离，判定可被行为测试覆盖）；
- `useWorkspaceCanvas` 新增 `handleGeneratedFilePreview`，接进 `EmbedChat`；
- `MessageRenderer` 补 Office 扩展名的 `open-canvas` 分派 + 生成文件链接后的「下载」按钮；
- 修复两处下载语义缺陷（见第 7 节）；
- 契约测试 + 行为测试。

**明确不做：**

- 后端任何改动。`download=1` 参数已存在（`chat.py:215`），不需要新增开关；不把 Office 改成 `inline`。
- **旧版二进制 `.ppt`**：仍需 `@file-viewer/renderer-ppt` 那条带水印/`SEE LICENSE IN LICENSE` 的依赖链，维持「点击即下载」。
- 「可复用结果」tab、工作区抽屉、工作区文件预览链路：语义不同，不改。
- 产物预览**不支持保存回工作区**：生成产物不是工作区文件，没有 `sourcePath`，画布保持只读（这是有意的，避免把产物写进会话工作区）。
- 产物重命名 / 删除 / 分享等未提出的能力。

## 4. 交互设计

### 4.1 抽屉列表项

| 动作 | 触发方式 | 行为 |
| --- | --- | --- |
| 预览 | 点击列表项主体（图标 / 文件名 / 元信息行），或右侧「预览」图标 | 画布打开该产物 |
| 下载 | 右侧「下载」图标（`@click.stop`，避免冒泡成预览） | 立即下载原文件 |

- 两个图标默认 `opacity-0`，`group-hover` / `focus-visible` 时显现；移动端（`isMobile`）常显——触屏没有 hover。
- 图标按钮带 `aria-label` 与 `title`（与抽屉现有按钮风格一致）。
- **不可预览类型**（`.ppt`、无扩展名、未知后缀）：不渲染「预览」图标；主体点击改为「toast 说明不支持预览 + 触发下载」，不留"点了没反应"的死区。这个判定**由抽屉自己完成**（`resolveGeneratedPreviewPlan(name).kind === 'download-only'`），此时**不 emit `preview-file`**，父组件因此不需要"回落下载"的兜底分支。
- 下载中的图标不置 loading 态（下载由浏览器接管，无可靠完成信号，加个假的转圈反而误导）。

### 4.2 预览打开方式

`canvasFromWorkspace = false` + `showCanvas(true)`：**右侧钉住**，与消息正文点产物链接的行为一致（一边聊一边看产出物）。

因此调用方 `EmbedChat` 必须配合：**预览成功后收起「我的产出」抽屉**（`if (opened) showMyArtifactsDrawer.value = false`）。否则右侧抽屉（`z-125`）与右侧钉住画布（`ChatCanvas.vue:921-941` 的 `panelFrameClass`，`z-145`）会挤在同一侧。两个细节：

- **失败时不收抽屉**：取内容 404 / 网络错误时保住抽屉，用户还能改点「下载」（`openGeneratedFileInCanvas` 返回 `false` 正是为此）；
- 移动端由 `showCanvas` 内部自动降级（`canvasPinned = pinByDefault && !mobile`），画布全屏 `z-[260]` 压过抽屉。

`canvasFromWorkspace.value = false` 必须**显式**赋值：上一次可能是工作区预览，那次会把它置为 `true`（决定 `dock-side` 与是否左侧让位）。

## 5. 架构：判定与取内容分离

沿用 `documentPreviewFormats.ts:1-7` 已确立的约定——**判定模块保持零运行时依赖，才能被 `node --experimental-strip-types` 直接加载做行为断言**（字符串子串断言抓不到 `.pptx` 被 `.ppt` 规则误伤这类缺陷）。取内容必须依赖 axios，因此拆成两个文件：

### 5.1 新增 `frontend/src/utils/generatedFilePreviewPlan.ts`（零依赖）

```ts
export type GeneratedPreviewKind = 'url-document' | 'blob' | 'text' | 'download-only'

export interface GeneratedPreviewPlan {
  kind: GeneratedPreviewKind
  canvasType: 'document' | 'pdf' | 'image' | 'csv' | 'html' | 'code' | null
}

export function resolveGeneratedPreviewPlan(name: string): GeneratedPreviewPlan
/** 产物下载地址：追加 download=1，强制后端 attachment（见第 7 节） */
export function buildArtifactDownloadUrl(url: string): string
```

分派表（**复用** `documentPreviewFormats` 既有集合，不新建第二份判定；按表中自上而下**短路**匹配，第一行命中即返回）：

| 扩展名 | `kind` | `canvasType` | 取内容方式 |
| --- | --- | --- | --- |
| `.docx .doc .xlsx .xls .xlsm .pptx`（`OFFICE_PREVIEW_EXTENSIONS`） | `url-document` | `document` | 只传鉴权 URL |
| `.pdf`（`PDF_EXTENSIONS`） | `url-document` | `pdf` | 只传鉴权 URL |
| `.png .jpg .jpeg .webp .gif`（`IMAGE_EXTENSIONS`） | `blob` | `image` | axios → Blob → 对象 URL |
| `.csv` | `blob` | `csv` | 同上 |
| `.html .htm`（`CANVAS_NATIVE_TEXT_EXTENSIONS` 里的 HTML） | `text` | `html` | 取文本，画布 sandbox iframe 渲染 |
| 其余 `DOCUMENT_VIEWER_EXTENSIONS` / `TEXT_EXTENSIONS` 文本与代码（含 `.md/.markdown`） | `text` | `code` | 取文本 |
| `.ppt`（`shouldDownloadInsteadOfPreview`）与未知扩展名 | `download-only` | `null` | 不取内容 |

**为什么 `.md` 归 `code` 而不是 `document`**：`ChatCanvas.vue:400-407` 的 `isMarkdownFile` 只认 `type === 'code' | 'html'` + `.md` 后缀，归 `code` 才能命中画布的 Markdown 渲染器（带 mermaid / ECharts），而不是 file-viewer 那套只有 marked + dompurify 的降级渲染。

**为什么 `.csv` 不归 `document`**：`.csv` 不在 `DOCUMENT_VIEWER_EXTENSIONS` 内，画布的 PivotTable 分析能力也只挂在 `type === 'csv'` 上。

### 5.2 新增 `frontend/src/utils/generatedFilePreview.ts`（依赖 axios）

```ts
export async function openGeneratedFileInCanvas(options: {
  url: string
  name: string
  showToast: (message: string, type?: 'success' | 'error' | 'warning' | 'info') => void
  onOpen: (data: CanvasPanelData) => void
  activeBlobUrlRef?: { value: string }
}): Promise<boolean>   // true = 已打开预览；false = 该类型不支持预览，调用方决定是否改走下载
```

- 先按 `resolveGeneratedPreviewPlan(name)` 分派；`download-only` → toast 说明 + 返回 `false`（不构造任何画布数据）。抽屉已在 emit 前拦掉这一类（见 4.1），因此这是**防御性兜底**，服务于消息正文等未来调用方，不是主判断点。
- `url-document`：`onOpen({ type, title: name, content: url, documentMeta: { filename: name, mime } })`，**不传 `downloadPath`**（它不是工作区路径；`ChatCanvas.downloadFile` 的 `downloadPath` 分支走的是工作区下载 API）。
- `blob`：`axios.get(url, { responseType: 'blob' })` → `URL.createObjectURL` → 写回 `activeBlobUrlRef.value`（由 `useWorkspaceCanvas` 既有的 `revokeActiveBlobUrl` 与 `watch(canvasVisible)` 回收，与 `canvas://file` 分支同机制）。
- `text`：`axios.get(url)` 取文本 → `type: 'html' | 'code'`。
- 错误统一 `showToast`：`error.response?.data?.detail` 优先（沿用 `useWorkspaceCanvas.ts:189-197` 的措辞风格），404 提示"文件不存在或已过期"（对应后端 `chat.py:226`），其余给 `error.message`。

## 6. 接线

| 文件 | 改动 |
| --- | --- |
| `frontend/src/composables/chat/useWorkspaceCanvas.ts` | 新增 `handleGeneratedFilePreview({ url, name })`：`canvasFromWorkspace = false` + `showCanvas(true)`（右侧钉住），内部调 `openGeneratedFileInCanvas`，把该函数的 `Promise<boolean>` 直接返回给调用方 |
| `frontend/src/components/embed/MyArtifactsDrawer.vue` | 新增 `emit('preview-file', item)`（payload 为完整 `ArtifactListItem`，Host 绑定交给父组件）；`openArtifact` 拆为 `previewArtifact`（先判 `download-only`，命中则只 toast + 下载）/ `downloadArtifact`（用 `buildArtifactDownloadUrl`）；列表项加两个图标按钮 |
| `frontend/src/views/EmbedChat.vue` | `MyArtifactsDrawer` 上接 `@preview-file`：`handleGeneratedFilePreview({ url: resolveGeneratedFileHref(it.download_url), name: it.filename })`；**预览成功后收起抽屉**（右侧钉住与抽屉互挤），失败则保留 |
| `frontend/src/components/MessageRenderer.vue` | `handleContentClick` 的 `<a>` 分支补 Office 扩展名 → `emit('open-canvas', { type: 'document', title, content: href, documentMeta: { filename, mime } })` |
| `frontend/src/components/embed/ChatCanvas.vue` | `downloadFile`（`:117-127`）补直链分支，`import { buildArtifactDownloadUrl } from '@/utils/generatedFilePreviewPlan'` —— 产物下载地址的构造保持**单一来源** |

**为什么 `EmbedChat` 要参与**：画布状态归 `useWorkspaceCanvas`（`EmbedChat.vue:6124-6138`），抽屉只该 emit 意图，不该自己构造画布数据（用户已确认方案 A；方案 B「抽屉自己取内容」会让抽屉承担 blob 生命周期，与画布单槽 `activeBlobUrl` 冲突）。

**`resolveGeneratedFileHref` 必须保留**（`generatedFileUrl.ts:34-47`）：跨站嵌入时 `download_url` 里是配置的 `APP_PUBLIC_URL`，绑到当前页面 Host 才能通；`MessageRenderer.postProcessHtml:182-186` 已在 `<a href>` 上做过这一步。

**AgentDebug 顺带受益**：它也传 `@open-canvas="handleOpenCanvas"`、也用 `MessageRenderer` 与 `useWorkspaceCanvas`，`type: 'document'` 的 payload 走 `handleOpenCanvas` 默认分支直接赋值即可，无需为其单独接线。

## 7. 必须一起修的两个下载语义缺陷

### 7.1 下载按钮对 html / pdf / 图片产物无效

后端默认对这三类给 `inline`（`chat.py:232-248`），现行的「下载」动作会退化成新标签页预览。

**修法**：下载动作统一用 `buildArtifactDownloadUrl(url)` 追加 `download=1`（后端 `download: bool = False` 参数已存在，`chat.py:215`、`:245-246` 强制 `attachment`）。抽屉与消息正文的「下载」都走它。

### 7.2 `ChatCanvas.downloadFile` 对无 `downloadPath` 的 document/pdf 会下载出一段 URL 文本

`ChatCanvas.vue:111-150`：`type === 'document' | 'pdf'` 时若没有 `downloadPath`（产物预览正是这种情况），会穿透到 `:140-149` 的「把 `content` 当文本做 Blob」兜底——而 `content` 是鉴权 URL，用户会拿到一个内容为 URL 字符串的 `.docx`。触发点是 `DocumentViewer` 的 `@fallback-download`（Blob 获取失败时的降级按钮）。

**修法**：在 `:117-127` 分支内补一条——`content` 是 `/api/` 或 `http(s)://` 直链时，用 `buildArtifactDownloadUrl` 追加 `download=1` 后以隐藏 `<a>` 触发下载；`downloadPath` 分支与文本兜底均保持原样。注意产物 URL **不带扩展名**（`/api/v1/chat/generated-files/{32hex}?token=...`），因此该分支判据只能是 URL 形态，不能是扩展名。

## 8. 测试与验证

**新增行为测试** `tests/frontend/generated_file_preview.mjs`（`node --experimental-strip-types`，与 `document_preview_formats.mjs` 同风格）——对 `resolveGeneratedPreviewPlan` 断言真实返回值：

- `.docx/.xlsx/.pptx/.doc/.xls/.xlsm` → `url-document` + `document`；
- `.pdf` → `url-document` + `pdf`；
- `.png/.csv/.html/.md/.txt` → 各自 `blob/text` 与 `canvasType`；
- **`.pptx` 不被 `.ppt` 规则误伤**（`shouldDownloadInsteadOfPreview` 的边界）；
- `.ppt` / `README` / `.zip` → `download-only`；
- `buildArtifactDownloadUrl` 对已带 `?token=` 的地址补 `&download=1`、对已带 `download=` 的不重复追加。

**新增契约测试** `tests/frontend/test_generated_file_preview_contract.py`（源码形状，沿用项目惯例）：

- 两个 util 文件存在，且 `generatedFilePreviewPlan.ts` **不 import axios**（守住"判定零依赖"这条约定）；
- `MyArtifactsDrawer.vue` 含 `preview-file` emit 与预览/下载两个动作，且下载动作含 `buildArtifactDownloadUrl`；
- `MessageRenderer.vue` 含 Office 扩展名判定与 `data-generated-download`；
- `ChatCanvas.vue` 的 document/pdf 下载分支含 `download=1`；
- `EmbedChat.vue` 含 `@preview-file` 与 `handleGeneratedFilePreview`。

**回归命令**：`pytest --confcutdir=tests/frontend`、`vue-tsc --noEmit`。

**手动验证清单**（按项目约定由用户在控制台执行 `./dev.sh` 后验证）：

| 用例 | 期望 |
| --- | --- |
| 抽屉里点 `.docx` 主体 | 画布内渲染，带 file-viewer 工具栏（搜索/缩放/打印/下载） |
| 抽屉里点「下载」图标 | 直接下载，不新开标签页 |
| 抽屉里点 `.pdf` / `.png` 主体 | 画布内渲染并可翻页/缩放 |
| 抽屉里点 `.md` 产物主体 | 画布 Markdown 渲染（含 mermaid/ECharts，如有） |
| 抽屉里点 `.ppt` / `.zip` 主体 | toast 说明不支持预览并开始下载，不白屏 |
| 消息正文点 `北京到上海高铁车次参考清单.docx` | 画布内渲染 |
| 消息正文点文件名后的「下载」 | 直接下载 |
| html / pdf / 图片产物的「下载」 | 真的下载文件，不是新标签页预览 |
| 点抽屉里任一产物主体 | 抽屉自动收起，画布在右侧钉住并渲染该文件；对话区被画布挤窄但不遮挡 |
| 预览失败（token 过期 / 断网） | 画布给出错误提示，**抽屉保持打开**（可立即改点「下载」）；画布上的「下载原文件」下载的是真文件 |
| 连续预览两个不同产物 | 重新打开抽屉再点即可；画布内容正确切换，上一份的对象 URL 被回收（无内存堆积） |
| 断网 / token 过期 | 画布显示错误提示 + 「下载原文件」可用（且下载的是真文件） |
| 跨站嵌入 iframe 场景 | 预览能加载（证明 axios 鉴权 + Host 绑定链路生效） |
| 移动端 | 两个图标常显，画布全屏压过抽屉 |

## 9. 边界与风险

- **PDF 仍为全量 Blob**：拿不到 Range 分片（与 `2026-10-08` 设计第 9 节同一取舍），产物是大 PDF 时首次加载偏慢。
- **首次预览有可见延迟**：`DocumentViewer` 懒加载 file-viewer 与 `public/file-viewer/` 资产（约 7-9MB 量级，已在既有设计登记）。
- **对象 URL 只有单槽**：`activeBlobUrl` 是单值，快速连续预览两个图片/CSV 时前一个会被 revoke——这与工作区预览现状一致，不劣化。
- **产物预览只读**：无 `sourcePath` 时画布不显示「保存」；若后续想支持"另存到工作区"，需另设计（不在本期）。
- **下载按钮无完成反馈**：浏览器接管下载，不做假 loading。
- **产物 URL 的有效期**：token 已改为由 `(artifact_id, expires_at)` **确定性派生**（见 `2026-10-11-artifact-token-deterministic-signature.md`），TTL 30 天（`generated_file_service.py:26`），过期后预览与下载都会 404，提示语需说清"文件不存在或已过期"；列表接口已不再续期，因此不再是"每次列出就重置 30 天"。

## 10. 后续（本期不做，已记录）

1. **产物页签预览续看**：抽屉内嵌缩略图/首屏预览，需要后端出缩略图能力。
2. **`RagPreviewDrawer` / 知识库弹窗复用**同一条产物分派链路（`2026-10-08` 设计第 10 节遗留项）。
3. **后端止血项**（与本期解耦）：Office 响应改 `inline` 后可省掉 `download=1`；路由级 `FileResponse` 补条件请求（304）。


## 11. 修订补记（2026-10-11）：正文侧漏了文本类

**问题（用户实测）**：消息正文里点《北京到上海高铁车次参考清单.md》→ 直接下载、没有预览；同一个文件在「我的产出」抽屉里能预览。用户问「是不是刚刚只搞了 word 格式」。

**真实原因**：不是格式覆盖范围问题，而是**两侧判定没有共用同一份逻辑**。

- 抽屉侧走 `resolveGeneratedPreviewPlan`（Office / PDF / 图片 / CSV / HTML + 56 种文本扩展名）→ `.md` 命中 `DOCUMENT_VIEWER_EXTENSIONS` → `text/code`，可预览；
- 正文侧上一期只在原有分支（`isPdf` / `isCsv` / `isHtml` / `isImage`）之外**硬编码补了 Office**，文本类没有任何分支 → 点击落到浏览器默认行为（下载）；
- 后端 `.md` 的 mime 推断是 `None`（Python `mimetypes` 不认 `.md`）→ 退到 `application/octet-stream` → `Content-Disposition: attachment`，会让"未被前端拦截"的链接直接下载（走 axios blob/text 的画布预览不受此影响）。

**修法（消除第二份清单）**：正文侧复用抽屉同一套分派器 ——

1. `MessageRenderer` 对命中 `GENERATED_FILE_HREF_PATTERN` 的链接，用**链接文字（文件名）**调 `resolveGeneratedPreviewPlan`（产物 URL 本身不带扩展名）；
2. 只在其"原本扩展名分支没覆盖"时才走这条新路径，`pdf/csv/html/图片/Office` 的既有行为一律不动，避免回归；
3. 可预览则 `emit('preview-generated-file', { url, name })`，由 `EmbedChat` 交给 `handleGeneratedFilePreview`（与抽屉同一条链路：绑当前 Host → 取内容/URL → 右侧钉住画布）；
4. **兜底**：宿主未接线（如只用于展示回答摘要的 `MessageRenderer`）时不 `preventDefault`，退回浏览器默认下载 —— 否则链接会点了没反应，比下载更糟。

**验证**：契约测试 +3（复用分派器、兜底前提、EmbedChat 接线含 handler 体内 Host 绑定），前端全量 **1695 passed / 1 skipped / 0 failed**，`vue-tsc` 仍为既有 63 项且新增标识符 0 命中；正文侧变异 **4/4**（不再复用分派器 / 去掉兜底 / 不再接线 / 不再绑 Host）。

**遗留**：`AgentDebug.vue` 的 4 处 `MessageRenderer` 未接 `preview-generated-file`（内部调试页，靠兜底维持"点击下载"，不劣化）；需要时一行即可接上。

## 12. 修订补记（2026-10-11）：外部 review 三条建议的核实与处理

外部 AI review 提了 3 条。逐条核实（读代码 + 跑真实用例验证），**2 条采纳、1 条采纳意图但换写法**，另顺带补掉 §11 的遗留。

### 12.1 【采纳】`normalizeDirectPayloadTitle` 会吃掉 `documentMeta`

`useWorkspaceCanvas.ts` 里该分支是**逐字段重建**对象（`type/title/content/langName/runnable`），而 `AgentDebug.vue` 配了 `normalizeDirectPayloadTitle: true` → `MessageRenderer` 通过 `open-canvas` 传出的 `documentMeta`（Office 预览靠它拿 filename/mime）在调试页被丢弃。

补充两点事实修正：① `EmbedChat` **没有**开这个开关，所以线上聊天页不受影响，只有调试页；② 上一期改动后，**产物**链接在调试页走的是 `preview-generated-file`（当时无人接 → 兜底下载），真正踩到这条的是**非产物**的 Office 链接（如工作区 `.docx` 直链）。

修法上**没有照搬 review 的建议**。review 建议改成 `{ ...payload, title: ... }`（全量展开），但这会破坏该分支的既有语义：它是**白名单式**重建，只把画布认识的字段传下去，不夹带 `sourcePath` 之类的上下文 —— 既有测试 `test_workspace_canvas_keeps_workspace_toggle_and_debug_title_normalization`（真跑 TS，断言 `direct == {type,title,content}`）与 `test_workspace_canvas_preserves_script_metadata_when_normalizing_payload`（断言白名单字段存在）已经把它固化为规范。变异 M17b（就是换成 `...payload`）被这两个测试抓住即为证。

所以采用的是**往白名单里补字段**，并且只在 payload 确实带了 `documentMeta` 时才加（保持对象形状不变，不引入 `documentMeta: undefined` 这种多余键）：

```ts
?(payload.documentMeta ? { documentMeta: payload.documentMeta } : {})
```

### 12.2 【采纳意图、换写法】产物链接正则的 query 假设

review 说的风险（token 不是第一个参数时认不出来）方向对，但**给的正则 `(?:\?|\?.*&)token=` 反而更差**，实测（变异 M19）：

- `\?.*&token=` 里的 `.*` 是贪婪的，在单行 HTML 里会跨过 `</a>` 去匹配**别的**链接上的 token（抓它的用例必须让第二个链接写成 `?d=1&token=`，否则贪婪分支根本跨不过来 —— 第一版用例就因此漏抓）；
- 单独使用 `\?.*&token=` 时连最普通的 `?token=` 都匹配不了（它强制要求 `&`）。

最终写法显式允许 token 前面有若干 `参数&`，并把中间字符类收窄：

```js
/\/api\/v1\/chat\/generated-files\/[0-9a-f]{32}\?(?:[^"'\s<>&]+&)*token=[A-Za-z0-9_-]+/i
```

字符类排除 `"'\s<>&`：既不会跨标签，也不会误吃别的 token。用例覆盖 `?token=`、`?token=…&download=1`、`?download=1&token=…`、无 token、空 token、非 32 位 hex，以及一条**防贪婪**断言（匹配结果不得含 `</a>`）。

> 需要说明的是：当前后端 `build_download_url` 恒定把 token 放在第一个参数，所以这条**不是线上 bug**，属于防御性放宽。

### 12.3 【采纳】`list_artifacts` 兜底用了本地 naive 时间

```python
expires_at = ((row.created_at or datetime.now()) + DEFAULT_TTL).replace(microsecond=0)
```

`expires_at` 列的语义是 UTC（登记时用 `datetime.now(timezone.utc)` 写入），而 `datetime.now()` 是**本地** naive 时间 → 在 TZ 不是 UTC 的部署上，兜底补出来的过期时间会整体偏移，由它派生的 token 也跟着错。

修法：`datetime.now(timezone.utc)`。补充事实：当前容器 TZ=UTC，所以实际没有暴露（这也解释了此前取证里 `expires_at - created_at` 只多 21.7 分钟、而不是 8 小时）；另外 `ai_artifacts.created_at` 用的是 `default=datetime.now`，那是**全项目惯例**（`user.py`/`task.py`/`metadata.py` 等都如此），本次不动它，只在兜底处显式用 UTC 并在注释里写明原因。

### 12.4 【顺带补齐 §11 遗留】调试页接上正文产物预览

`AgentDebug.vue` 解构出 `handleGeneratedFilePreview`、加 `previewGeneratedFileInCanvas`、并在**全部 4 处** `MessageRenderer` 上接 `@preview-generated-file`（用 `count` 断言与 `@open-canvas` 数量相等，避免"有的消息能预览、有的只能下载"）。

### 12.5 验证

- 新增/调整测试 5 条：`test_workspace_canvas_normalizing_title_keeps_document_meta`（契约）、`test_workspace_canvas_direct_payload_keeps_document_meta_for_office_preview`（**真跑 TS** 的行为测试，同时断言白名单语义不变）、`test_generated_file_href_pattern_accepts_token_after_other_query_params`（**真跑正则字面量** + 防贪婪断言）、`test_agent_debug_wires_message_body_artifact_preview_to_its_canvas`、`test_list_artifacts_backfills_missing_expiry_with_utc`；
- 变异 **6/6** 命中且字节级还原：M17 白名单漏 documentMeta、**M17b 换成 review 的全量展开写法（被既有白名单语义测试抓住）**、M18 退回严格正则、**M19 换成 review 原样正则（被防贪婪断言抓住）**、M20 兜底退回 naive 本地时间、M21 调试页不接线；
- 回归：前端契约全量 **1702 passed / 1 skipped / 0 failed**、`tests/api/v1/test_chat_artifacts.py` **10 passed**、`tests/ai/tools` **203 passed / 4 xfailed**、`vue-tsc -b --force` **63 项**（与改动前相同；3 处命中均为既有错误：2 处逐字一致、1 处行号随新增代码平移 14 行）。
