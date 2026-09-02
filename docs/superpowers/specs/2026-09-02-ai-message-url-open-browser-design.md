# AI 消息 URL 右侧浏览器打开设计

## 目标

在 EmbedChat 的 AI 消息中识别 `http://` 和 `https://` 链接，并在链接旁提供紧凑的“打开”按钮。点击后在右侧服务端浏览器面板中打开该地址：没有浏览器会话时用该地址初始化会话，已有会话时新建标签页打开，不覆盖用户当前页面。

## 范围与非目标

### 范围

- 覆盖 AI 消息中的 Markdown 链接和 MarkdownIt `linkify` 生成的裸 URL 链接。
- 覆盖当前流式消息和历史消息中由 EmbedChat 渲染的回答。
- “打开”按钮采用链接同行的紧凑样式，长 URL 允许换行，按钮保持不收缩。
- 原始链接继续保留原有访问行为；“打开”是独立入口。
- 只允许 `http:` / `https:` 协议进入右侧浏览器打开事件。

### 非目标

- 不改变 `quick:`、`canvas:`、本地文件预览、引用徽章和图片/CSV/PDF 画布打开逻辑。
- 不让 SessionTraceModal、技能编辑器等其他 `MessageRenderer` 使用方自动出现按钮。
- 不新增后端接口、数据库字段或浏览器权限模型。
- 不把 URL 交给 AI 重新分析，也不绕过现有服务端浏览器会话的鉴权、审计和安全策略。

## 现状与约束

- `frontend/src/components/MessageRenderer.vue` 通过 `renderMarkdown` 生成 HTML，再在根节点使用事件代理处理链接点击。
- `frontend/src/views/EmbedChat.vue` 已负责创建、绑定、关闭服务端浏览器会话，并挂载 `BrowserPanel`。
- `BrowserPanel` 已支持 `navigate` 和 `new_tab` 消息，已有人工地址栏和多标签页能力。
- `MessageRenderer` 被多个页面复用，因此必须使用显式 prop 控制按钮显示，避免其他页面出现无处理出口的按钮。

## 方案

### 组件边界

1. `MessageRenderer`
   - 新增可选 prop `enableBrowserOpen`，默认 `false`。
   - 在 Markdown 后处理阶段，仅对最终 `href` 为 `http:` / `https:` 的 `<a>` 追加同行的 `[打开]` 操作元素。
   - 为操作元素写入明确的 `data-open-browser-url`，使用 HTML 转义后的 URL，避免把 URL 拼成可执行属性。
   - 在现有根节点点击代理中优先处理该操作元素，校验协议后 emit `open-browser-url`；阻止该按钮触发原始链接，但不影响原始链接自身点击。
   - 已有 `quick:`、`canvas:` 和资源画布分支保持原顺序与语义；不为非 HTTP(S) 地址追加操作。

2. `EmbedChat`
   - 在当前 AI 消息和历史回答的 `MessageRenderer` 上启用 `enable-browser-open`。
   - 处理 `open-browser-url`，新增一个面向 URL 的浏览器打开入口：
     - 没有有效的 `browserSessionId` / `browserViewerToken` 时，调用现有会话创建接口，初始 URL 使用用户点击的 URL；
     - 已有有效会话时，将 URL 传给 `BrowserPanel`，由面板通过 `new_tab` 创建新标签页并导航；
     - 在打开期间保留现有 loading、环境错误、鉴权失败和代际竞态保护。
   - 打开失败时沿用现有 toast/环境错误提示，不让消息渲染失败。

3. `BrowserPanel`
   - 增加受控 URL prop `openUrl`，并 watch 变化。
   - WebSocket 已连接时发送 `{ type: 'new_tab', url }`，新标签页成为当前标签页；未连接时暂存一次请求，连接后只消费最新 URL，避免重复打开。
   - 复用现有 `normalizeNavigationUrl` 仅用于人工地址栏；来自消息的 URL 在 `MessageRenderer` 和 `EmbedChat` 两侧都做协议白名单校验，不接受协议相对地址、脚本协议或本地文件协议。
   - 现有人工导航、AI 导航、标签页切换和面板关闭行为保持不变。

## 数据流

```text
AI Markdown / 裸 URL
        ↓
MessageRenderer（enableBrowserOpen=true）
        ↓ 生成链接 + [打开]
用户点击 [打开]
        ↓ emit open-browser-url(url)
EmbedChat
   ├─ 无会话：sessions/open(url) → attachBrowserSession → BrowserPanel
   └─ 有会话：更新 openUrl → BrowserPanel new_tab(url)
```

## 安全与错误处理

- URL 白名单为精确的 `http:` 和 `https:`，大小写协议统一按不区分大小写校验后再传递。
- `javascript:`, `data:`, `file:`, `quick:`, `canvas:` 及无法解析的值不显示“打开”或不发出浏览器打开事件。
- URL 中的 token、query 和 hash 作为地址的一部分保留；不新增日志打印，不在 toast 中回显完整敏感 URL。
- 右侧浏览器仍使用现有登录凭证、会话权限、审批模式和服务端浏览器环境检查；此功能只是复用现有导航入口。
- 重复点击通过现有打开代际和面板同步状态抑制；过期的异步创建结果不能重新显示已关闭的面板。
- 浏览器环境不可用时显示现有环境错误与重试入口；网络/鉴权失败使用现有错误提示。

## 测试设计

### 前端契约测试

- `MessageRenderer`：默认不显示按钮；启用 prop 后对 Markdown 链接和裸 URL 生成 `[打开]`；非 HTTP(S) 协议不生成；操作元素使用独立 data 属性；点击事件 emit 且不会触发原链接。
- `EmbedChat`：两个 EmbedChat 消息渲染入口启用 prop；存在 URL 打开事件处理；无会话创建时传入点击 URL；已有会话走面板新标签页入口。
- `BrowserPanel`：监听 URL prop；WebSocket 就绪后发送一次 `new_tab`；重复相同值不重复消费；连接未就绪时保留最新请求；现有人工导航契约继续通过。

### 静态验证

- 运行聚焦前端契约测试。
- 从 `frontend` 运行 `vue-tsc --noEmit`。
- 对本次修改运行 `git diff --check`。

### 手工验证边界

服务端浏览器会话、真实外部 URL、带 token 的生成文件 URL、右侧面板新标签页切换和移动/窄面板布局需要用户启动服务后在浏览器中验证；静态测试不能替代这些验收。

## 验收标准

1. AI 消息中 Markdown 链接和裸 `http/https` URL 旁可见“打开”。
2. 点击“打开”后，右侧面板打开目标 URL；已有面板不会覆盖当前标签页。
3. 没有浏览器会话时，目标 URL 是首次打开地址，而不是默认百度页面。
4. 原始链接、文件画布、快捷动作和其他 `MessageRenderer` 使用页面行为不回归。
5. 脚本、本地文件和自定义协议不会触发右侧浏览器打开。
6. 代码级测试、类型检查和 diff 检查通过，并明确标注未做的真实服务/浏览器验收。
