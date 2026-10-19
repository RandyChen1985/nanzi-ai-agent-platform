# @ 浮层的「文件」tab：从工作区选文件挂载到输入框

- 日期：2026-10-19
- 涉及：`frontend/src/components/agent/MentionList.vue`、`frontend/src/components/embed/WorkspaceFilePicker.vue`（新增）、`frontend/src/components/embed/ChatInput.vue`、`frontend/src/views/EmbedChat.vue`、`tests/frontend/test_at_file_picker_contract.py`（新增）、`tests/CHECKLIST.md`

## 背景

输入框里敲 `@` 目前只有一个作用：弹出「选择专家」浮层，选中后把当前会话切到该专家（`ChatInput.vue` 的 `handleInput` 里用 `/[@＠]([^\s@＠]*)$/` 检测，`MentionList.vue` 渲染列表，`EmbedChat.vue` 的 `switchToExpert` 落地）。

而「把工作区里的文件挂到这条消息上」的能力**已经存在**，只是入口在别处：

| 已有能力 | 位置 |
| --- | --- |
| 完整文件管理器抽屉（目录、搜索、最近、上传、重命名、删除、回收站、预览） | `frontend/src/components/embed/WorkspaceBrowserDrawer.vue` |
| 抽屉选中 → 挂到输入框 | `EmbedChat.vue` 的 `handleSelectLocalFs`，往 `chatInputRef.uploadedFiles` push `{type:'local_file'\|'local_dir', url:path, filename:name, size, ext}`，带去重 |
| 发送时变成模型可见上下文 | `frontend/src/composables/chat/useChatAttachments.ts`，生成「用户本轮已从服务器挂载文件：xxx（路径 yyy）」 |
| 后端文件接口 | `app/api/v1/endpoints/fs.py`：`/chat/fs/list`、`/search`、`/recent-files`、`/preview`；普通用户只能浏览公共目录与本人 `agent_workspaces` |

所以缺的只有一环：**让 `@` 也能直接选文件**，而不是必须先打开一个占满半屏的抽屉。

## 目标

1. `@` 浮层增加 tab：`专家`（现状）/ `文件`（新增），默认仍停在 `专家`。
2. 文件 tab 内可以浏览工作区：搜索、最近文件、逐级进入目录、面包屑回退。
3. 选中文件后挂到输入框附件区，浮层关闭，焦点回到输入框，正文不插入 `@文件名` 文本。
4. 已挂载的文件在列表里可见「已挂载」并置灰，避免重复添加。
5. 锁定专家的链接（`?agent=` / 集成嵌页）里 `@` 仍能选文件，只是专家 tab 不可切换。

## 非目标

- 不在浮层里做文件预览、上传、重命名、删除、回收站——这些继续走抽屉，文件 tab 底部给一个「打开文件面板 →」的逃生口。
- 不把知识库文档、数据集、数据表纳入文件 tab（范围已确认：仅工作区文件）。
- 不做「@ 路径直接命中」（例如输入 `@reports/2026Q3.md` 直接挂载），搜索是唯一命中方式。
- 不改后端接口、不改发送时的上下文提示文案。

## 交互设计

### 浮层结构

```
┌──────────────────────────────────────────┐
│  ● 选择专家  [专家] [文件]     Esc 关闭    │  tab 头
├──────────────────────────────────────────┤
│  专家 tab（原样）：智能委派 + N 匹配       │
│  文件 tab：                                │
│    🔍 [搜索文件名…]                        │
│    公共目录 / 我的工作区 / reports        │  面包屑（仅在目录视图）
│    📁 docs             📄 2026Q3报告.md    │
│    📄 客户清单.xlsx     已挂载 ✓           │
├──────────────────────────────────────────┤
│  共 N 个专家           打开文件面板 →      │  底部
└──────────────────────────────────────────┘
```

- tab 头右侧在文件 tab 下显示「Esc 关闭」，专家 tab 保持现有的「Enter 选择 · Esc 关闭」。
- 底部左侧：专家 tab 显示「共 N 个专家」（现状），文件 tab 显示当前视图的条目数（如「最近 6 个文件」「搜索到 12 项」）。
- 底部右侧：文件 tab 才有「打开文件面板 →」，点击后关闭 `@` 浮层并打开 `WorkspaceBrowserDrawer`。

### 文件 tab 的三种视图

| 视图 | 触发 | 内容 |
| --- | --- | --- |
| 目录视图 | 默认 | `GET /chat/fs/recent-files` 的最近文件（最多 5 条，带「最近」小节标题）+ `GET /chat/fs/list`（不传 `path`，拿虚拟根：公共目录 + 本人 `agent_workspaces`） |
| 下钻视图 | 点某个文件夹 | `GET /chat/fs/list?path=…`；面包屑由 `path` 分段生成（首段展示为「工作区」，不暴露服务器绝对路径） |
| 搜索结果 | 搜索框输入 ≥1 字符（防抖 250ms） | `GET /chat/fs/search?q=…&max_results=30`；每行显示「文件名 + 所在目录（相对工作区根的路径）」，以便区分同名文件 |

- 清空搜索词 → 回到上一个目录视图，用已缓存的结果，不重新请求。
- 目录视图里文件夹在前、文件在后，各自按名称排序（后端返回顺序不保证时前端再排）。
- 搜索结果显示 `truncated=true` 时，底部提示「结果过多，仅显示前 30 项」。

### 选中行为

- 点文件（或 `Enter` 选中高亮行）→ 从输入框正文中抹掉 `@关键词` 片段（复用现有 `clearMentionTrigger`）→ 附件 chip 入列 → 关闭浮层 → 焦点回 textarea。
- 点文件夹 → 进入该目录，浮层不关。
- 已挂载的文件：行尾显示「已挂载 ✓」，整行置灰且不可点击（点击无反应，不弹 toast）。
- 目录也能挂载（`local_dir`），与抽屉行为保持一致；目录行在鼠标悬停时显示「挂载整个目录」的次级按钮？——**不做**，避免与「点击进入目录」冲突。目录挂载只在抽屉里可用。

### 焦点与键盘

| 场景 | 行为 |
| --- | --- |
| 切到文件 tab | 桌面端 `nextTick` 后聚焦搜索框；移动端**不自动聚焦**（浮层是 `bottom: 80px` + 40vh，键盘弹起会压掉列表），用户点搜索框才聚焦 |
| 焦点在搜索框 | `↑↓` 移动高亮行、`Enter` 选中、`Esc` 关闭浮层并把焦点还给 textarea |
| 焦点在 textarea（浮层开着） | 继续走现有转发：`↑↓/Enter/Esc` 交给 `MentionList.handleKeydown`，文件 tab 下由它转给 picker |
| 选中/关闭 | 关闭浮层后 `inputRef.focus()`，光标停在抹掉 `@关键词` 之后的位置（现有 `clearMentionTrigger` 已做） |

### 移动端

文件 tab 与桌面同构，列表高度随浮层（`maxHeight: 40vh`）滚动；搜索框不自动聚焦。不额外做全屏文件选择页。

## 组件与数据流

### 组件树

```
ChatInput.vue
└── MentionList.vue                 # 加 tab 头；专家列表逻辑不动
    ├── （专家 tab 内容，保持现状）
    └── WorkspaceFilePicker.vue     # 新增：仅 tab === 'file' 时渲染
```

### Props / Emits

`MentionList.vue` 新增：

| 方向 | 名称 | 说明 |
| --- | --- | --- |
| prop | `initialTab?: 'expert' \| 'file'` | 默认 `'expert'`；不改则每次 `@` 都停在专家 tab |
| prop | `mountedFilePaths?: string[]` | 已挂载的 `local_file` / `local_dir` 路径，用于置灰 |
| prop | `expertLocked?: boolean` | 链接锁定了指定智能体时为 true：专家 tab 内容替换为锁定提示，文件 tab 照常 |
| emit | `select-file(payload)` | `{ type: 'local_file', path, name, size, ext }`（picker 只选文件，不提供目录挂载） |
| emit | `open-workspace()` | 点「打开文件面板 →」 |

`MentionList` 内部 `activeTab` 是本地 `ref`，`visible` 由 `false → true` 时重置为 `initialTab`；`keyword` 变化只影响专家 tab 的过滤（文件 tab 的搜索框是独立状态，只在切到文件 tab 的瞬间用当前 `keyword` 初始化一次）。

`WorkspaceFilePicker.vue`：

| 方向 | 名称 | 说明 |
| --- | --- | --- |
| prop | `initialKeyword?: string` | 切到文件 tab 时 `@` 后面的词，作为搜索框初值与首搜关键词 |
| prop | `mountedPaths?: string[]` | 同上，用于置灰 |
| emit | `select(payload)` | 同 `select-file` |
| emit | `open-workspace()` | 透传 |
| expose | `handleKeydown(e): boolean` | 供 `MentionList` 转发键盘事件 |

### `@` 检测条件的变化

现状（`ChatInput.vue` 的 `handleInput`）是两层门：外层 `!props.lockExpertAgent`，内层 `props.allowedAgents.length > 0`，任一不满足就完全不响应 `@`。加了文件 tab 之后，这两层都要放开——锁定的只是「切专家」，不是「选文件」：

```js
const atMatch = val.slice(0, cursor).match(/[@＠]([^\s@＠]*)$/);
if (atMatch && !isComposing.value) {
  const atIndex = val.slice(0, cursor).lastIndexOf(atMatch[0][0] || '@');
  const isStartOfWord = atIndex === 0 || val[atIndex - 1] === ' ' || val[atIndex - 1] === '\n';
  if (isStartOfWord) {
    mentionKeyword.value = atMatch[1] || '';
    // 没有可切换的专家时直接落在文件 tab，避免弹出一个空壳
    mentionInitialTab.value = props.allowedAgents.length > 0 ? 'expert' : 'file';
    showMentionList.value = true;
    // …定位
    return;
  }
}
showMentionList.value = false;
```

同时补 `!isComposing.value`：现状 `/` 命令菜单有这个守卫，`@` 没有，中文输入法下打全角 `＠` 会在组合态就弹层。

### 请求时机与缓存

- picker 首次渲染（`onMounted` 或 `watch(visible)`）→ 并行请求最近文件与根目录列表。
- 目录结果按 `path` 缓存在 `Map<string, FileItem[]>`；再次进入同一目录直接命中，切目录不发请求（除非手动「刷新」——本次不做刷新按钮）。
- 搜索请求带自增序号，丢弃过期响应（避免快速输入时旧结果覆盖新结果）。

### 挂载链路统一

现在 `EmbedChat.handleSelectLocalFs` 直接操作 `chatInputRef.uploadedFiles.push()`，去重逻辑也在 EmbedChat。本次把「push 一个本地文件附件」收进 `ChatInput.pushLocalFile(payload)` 并 `defineExpose`：

- 去重键：`type + path`
- push 的字段与抽屉路径完全一致：`{ type, url: path, filename: name, size, ext }`
- 抽屉入口（`EmbedChat.handleSelectLocalFs`）改为调用 `chatInputRef.value.pushLocalFile(payload)`，两条入口共用一份逻辑。

## 边界与异常

| 情况 | 处理 |
| --- | --- |
| 根目录与最近文件都为空 | 空态：「工作区还没有文件」，下方给「打开文件面板 →」引导上传 |
| 搜索无结果 | 「没有匹配「关键词」的文件」，保留搜索词便于修改 |
| 请求失败（403/500/网络） | 列表区显示「加载失败，点击重试」，点击重新发当前视图的请求；不弹全局 toast |
| 无权限目录 | 后端已返回 403，前端按「请求失败」处理，不展示具体路径 |
| 路径过长 | 面包屑只显示最后两级，前缀折成「…」；行内文件名 `truncate`，`title` 给完整相对路径 |
| 锁定专家的链接 | 专家 tab 内容替换为一行提示「当前链接已锁定指定智能体」，文件 tab 正常可用；`@` 检测不再因 `lockExpertAgent` 而拒绝弹层 |
| 没有可切换专家（`allowedAgents` 为空） | 不再阻止 `@` 弹层——`initialTab` 直接给 `'file'`（ChatInput 按 `allowedAgents.length === 0` 判定） |
| IME 组合态 | `@` 检测补 `!isComposing.value` 守卫（现状缺失，中文输入法打全角 `＠` 会提前弹层） |

## 测试计划

### 契约测试（`tests/frontend/test_at_file_picker_contract.py`，读源码断言）

1. `MentionList.vue` 含 tab 头与 `activeTab` 状态，`visible` 变 true 时重置为 `initialTab`。
2. `MentionList.vue` 在 `tab === 'file'` 时渲染 `WorkspaceFilePicker`，专家列表用 `v-show`/`v-if` 分离，二者不同时渲染。
3. `WorkspaceFilePicker.vue` 请求三个接口：`/api/v1/chat/fs/list`、`/search`、`/recent-files`，且 `search` 只带 `q` 与 `max_results`。
4. picker 的搜索防抖存在（`setTimeout` + `clearTimeout`），且丢弃过期响应用了自增序号。
5. `handleKeydown` 暴露且覆盖 `ArrowUp`/`ArrowDown`/`Enter`/`Escape`。
6. 已挂载路径在行上渲染「已挂载」并阻止点击（`mountedPaths.includes` 判据 + `pointer-events-none` 或早退）。
7. `ChatInput.vue` 的 `@` 检测带 `!isComposing.value`，且弹层条件不再要求 `allowedAgents.length > 0`。
8. `ChatInput.vue` 暴露 `pushLocalFile`，`EmbedChat.vue` 的 `handleSelectLocalFs` 调用它（不再直接 `uploadedFiles.push`）。
9. 移动端不自动聚焦：聚焦逻辑里有 `windowWidth >= 640` 之类判据。

### 手工验收

1. 桌面：输入 `@` → 浮层出现在输入框上方 → 点「文件」→ 看到最近文件与工作区根目录 → 点文件夹进入 → 面包屑回退。
2. 输入 `@report` → 切「文件」→ 搜索框已带 `report` 并显示结果 → Enter 挂载 → chip 出现、浮层关闭、可继续打字。
3. 再输入 `@` → 切「文件」→ 刚才的文件显示「已挂载」且点击无反应。
4. 移动端（窄于 640px）：`@` → 浮层贴底 → 切文件 tab 不自动弹键盘 → 搜索与挂载可用。
5. 锁定专家的链接：`@` 仍可弹层，专家 tab 显示锁定提示，文件 tab 可正常挂载。
6. 发送后确认后端收到的上下文里带「已从服务器挂载文件：xxx（路径 yyy）」。

## 落地文件清单

| 文件 | 动作 |
| --- | --- |
| `frontend/src/components/embed/WorkspaceFilePicker.vue` | 新增 |
| `frontend/src/components/agent/MentionList.vue` | 加 tab 头、`initialTab` / `mountedFilePaths` props、`select-file` / `open-workspace` emits、键盘转发 |
| `frontend/src/components/embed/ChatInput.vue` | `@` 检测加 IME 守卫与「无专家也可弹层」、`pushLocalFile` + `defineExpose`、`mountedFilePaths` 计算、关浮层后归还焦点 |
| `frontend/src/views/EmbedChat.vue` | `handleSelectLocalFs` 改为调用 `pushLocalFile`；`@open-workspace` 打开抽屉 |
| `tests/frontend/test_at_file_picker_contract.py` | 新增契约测试 |
| `tests/frontend/test_chat_shared_helpers_behavior.py` | 若既有 `@` 相关断言受影响则同步调整 |
| `tests/CHECKLIST.md` | 追加交付记录 |

## 风险与权衡

1. **`MentionList` 的键盘归属变复杂**：两个 tab 的键盘语义不同（专家 tab 是「前端过滤」，文件 tab 是「搜索框 + 网络结果」）。缓解：键盘转发按 `activeTab` 分派，picker 自己暴露 `handleKeydown`，`MentionList` 不碰文件逻辑。
2. **浮层空间小**：280×288px 放不下完整文件管理器。已用「最近 + 目录 + 搜索」三件套替代树，代价是没有「一眼看到整棵树」。抽屉仍是完整能力的出口。
3. **`@` 语义变宽**：从「切专家」变成「插入专家或文件」。需要保证默认仍是专家 tab，否则老用户手感被破坏。
4. **搜索走全量 `os.walk`**：后端 `/fs/search` 在授权目录内递归遍历，文件多时可能慢。前端已防抖 + 只取 30 条；若实测慢，后续可加目录内搜索（`path` 参数）而非全局搜索。
