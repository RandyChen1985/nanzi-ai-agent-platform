# AI 消息 URL 右侧打开 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 EmbedChat 的 AI 消息链接旁提供“打开”按钮，并在右侧服务端浏览器面板的新标签页中打开目标 HTTP(S) URL。

**Architecture:** `MessageRenderer` 负责在显式开启时为 HTTP(S) 链接追加同行操作并发出 `open-browser-url` 事件；`EmbedChat` 负责无会话时以目标 URL 创建会话，已有会话时把 URL 传给 `BrowserPanel`；`BrowserPanel` 在 WebSocket 就绪后发送一次 `new_tab`。其他 `MessageRenderer` 使用方不启用该能力。

**Tech Stack:** Vue 3、TypeScript、MarkdownIt、现有服务端浏览器 WebSocket、pytest 前端契约测试、`vue-tsc`。

---

## 文件结构

- Create: `frontend/src/utils/messageBrowserLinks.ts` — HTTP(S) 白名单和链接操作元素生成。
- Modify: `frontend/src/components/MessageRenderer.vue` — prop、事件、按钮渲染和点击代理。
- Modify: `frontend/src/views/EmbedChat.vue` — 消息事件、浏览器 URL 状态、会话初始化。
- Modify: `frontend/src/components/embed/BrowserPanel.vue` — `openUrl` prop 和 `new_tab` 消费。
- Create: `tests/frontend/test_message_browser_open_contract.py` — URL 工具和 MessageRenderer 契约测试。
- Create: `tests/frontend/test_embed_browser_open_wiring_contract.py` — EmbedChat/BrowserPanel 链路契约测试。
- Modify: `tests/CHECKLIST.md` — 增加本次功能验收记录，只更新本次对应行。

### Task 1: URL 白名单与 MessageRenderer 操作按钮

**Files:**
- Create: `frontend/src/utils/messageBrowserLinks.ts`
- Modify: `frontend/src/components/MessageRenderer.vue`
- Test: `tests/frontend/test_message_browser_open_contract.py`

- [ ] **Step 1: 写失败测试**

在测试文件中增加 `_run_typescript` 辅助函数，并验证：

```python
def test_browser_url_helper_accepts_only_http_and_https():
    result = _run_typescript(
        "frontend/src/utils/messageBrowserLinks.ts",
        """
return {
  http: api.isBrowserOpenableUrl('http://example.com/a'),
  https: api.isBrowserOpenableUrl('https://example.com/a?token=x#top'),
  upper: api.isBrowserOpenableUrl('HTTPS://example.com/a'),
  javascript: api.isBrowserOpenableUrl('javascript:alert(1)'),
  data: api.isBrowserOpenableUrl('data:text/html,<script>alert(1)</script>'),
  local: api.isBrowserOpenableUrl('/api/v1/chat/generated-files/a'),
  quick: api.isBrowserOpenableUrl('quick:查询订单')
};
""",
    )
    assert result == {
        "http": True, "https": True, "upper": True,
        "javascript": False, "data": False, "local": False, "quick": False,
    }


def test_browser_link_helper_adds_only_http_sibling_action():
    result = _run_typescript(
        "frontend/src/utils/messageBrowserLinks.ts",
        "return api.appendBrowserOpenActions('<a href=\"https://example.com/a?x=1\">外部</a><a href=\"quick:查询\">快捷</a>');",
    )
    assert result.count("data-open-browser-url=") == 1
    assert 'data-open-browser-url="https://example.com/a?x=1"' in result
    assert 'class="message-link-open"' in result
    assert "quick:查询" in result
```

- [ ] **Step 2: 运行失败测试**

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_message_browser_open_contract.py -q
```

预期：失败，因为 URL 工具文件尚未创建。

- [ ] **Step 3: 实现 URL 工具**

创建工具并导出以下两个函数；原始值必须先匹配 HTTP(S)，再用 `new URL` 校验，属性值必须 HTML 转义：

```ts
export const isBrowserOpenableUrl = (value: string | null | undefined) => {
  const raw = String(value || '').trim();
  if (!/^https?:\/\//i.test(raw)) return false;
  try {
    const protocol = new URL(raw).protocol;
    return protocol === 'http:' || protocol === 'https:';
  } catch { return false; }
};

export const appendBrowserOpenActions = (html: string) => html.replace(
  /<a\b[^>]*>[\s\S]*?<\/a>/gi,
  (anchor) => {
    const match = anchor.match(/\bhref=(['"])(.*?)\1/i);
    const href = match?.[2] || '';
    if (!isBrowserOpenableUrl(href) || anchor.includes('data-open-browser-url=')) return anchor;
    const escaped = href.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `${anchor}<button type="button" class="message-link-open" data-open-browser-url="${escaped}" title="在右侧浏览器打开">打开</button>`;
  },
);
```

- [ ] **Step 4: 接入 MessageRenderer**

新增 `enableBrowserOpen?: boolean` prop，默认 `false`；新增 `(e: 'open-browser-url', url: string): void` emit。`postProcessHtml` 在已有裸 URL/路径链接处理完成后，仅当 prop 开启时调用 `appendBrowserOpenActions(res)`。

点击代理在普通 `a` 分支之前处理：

```ts
const browserOpenButton = target.closest<HTMLButtonElement>('[data-open-browser-url]');
if (browserOpenButton && props.enableBrowserOpen) {
  const href = browserOpenButton.getAttribute('data-open-browser-url') || '';
  if (isBrowserOpenableUrl(href)) emit('open-browser-url', href);
  event.preventDefault();
  event.stopPropagation();
  return;
}
```

给 `.message-link-open` 增加 `inline-flex`、`flex: 0 0 auto`、`white-space: nowrap`、紧凑蓝色边框背景和 hover 样式，确保长 URL 换行而按钮不挤压。

- [ ] **Step 5: 运行 Task 1 测试**

再次运行 Step 2 命令，预期 URL 白名单和 MessageRenderer 契约全部通过。

### Task 2: EmbedChat 事件出口与目标 URL 初始化

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`
- Test: `tests/frontend/test_embed_browser_open_wiring_contract.py`

- [ ] **Step 1: 写失败契约**

```python
def test_embed_chat_wires_browser_open_url_for_current_and_history_messages():
    source = _source("frontend/src/views/EmbedChat.vue")
    assert source.count(':enable-browser-open="true"') >= 2
    assert source.count('@open-browser-url="handleOpenBrowserUrl"') >= 2
    assert "const browserOpenUrl = ref<string | null>(null);" in source
    assert "const handleOpenBrowserUrl = (url: string) =>" in source
    assert "void openBrowserPanel(url);" in source
    assert "browserOpenUrl.value = url;" in source
    assert "const openBrowserPanel = async (initialUrl = \"https://www.baidu.com/\") =>" in source
    assert "url: initialUrl" in source
    assert ':open-url="browserOpenUrl"' in source
```

- [ ] **Step 2: 运行失败契约**

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_embed_browser_open_wiring_contract.py::test_embed_chat_wires_browser_open_url_for_current_and_history_messages -q
```

预期：失败，因为 EmbedChat 尚未接收该事件。

- [ ] **Step 3: 实现事件处理和会话初始化**

导入 `isBrowserOpenableUrl`，加入状态和入口：

```ts
const browserOpenUrl = ref<string | null>(null);

const handleOpenBrowserUrl = (url: string) => {
  if (!isBrowserOpenableUrl(url)) return;
  if (browserSessionId.value && browserViewerToken.value) {
    browserOpenUrl.value = url;
    browserPanelVisible.value = true;
    return;
  }
  void openBrowserPanel(url);
};
```

把 `openBrowserPanel` 的签名改为 `async (initialUrl = "https://www.baidu.com/")`，仅替换创建请求的 URL 为 `initialUrl`，保留鉴权、代际保护、attach、loading 和错误处理。当前消息和历史回答两个 `MessageRenderer` 都增加：

```vue
:enable-browser-open="true"
@open-browser-url="handleOpenBrowserUrl"
```

`BrowserPanel` 增加 `:open-url="browserOpenUrl"`；`closeBrowserPanel` 和 `closeBrowserSession` 清空 `browserOpenUrl`。

- [ ] **Step 4: 运行 EmbedChat 测试**

运行 Task 2 Step 2 命令，并回归 `tests/frontend/test_embed_instance_session_contract.py`，预期全部通过。

### Task 3: BrowserPanel 通过新标签页消费 URL

**Files:**
- Modify: `frontend/src/components/embed/BrowserPanel.vue`
- Test: `tests/frontend/test_embed_browser_open_wiring_contract.py`

- [ ] **Step 1: 写失败契约**

```python
def test_browser_panel_consumes_open_url_once_after_socket_connects():
    source = _source("frontend/src/components/embed/BrowserPanel.vue")
    assert "openUrl?: string | null;" in source
    assert "const lastOpenedUrl = ref('');" in source
    assert "watch(() => props.openUrl" in source
    assert "type: 'new_tab'" in source or 'type: "new_tab"' in source
    assert "if (url === lastOpenedUrl.value) return;" in source
    assert "client.onopen" in source
```

- [ ] **Step 2: 运行失败契约**

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_embed_browser_open_wiring_contract.py::test_browser_panel_consumes_open_url_once_after_socket_connects -q
```

预期：失败，因为 BrowserPanel 尚无 `openUrl` prop。

- [ ] **Step 3: 实现一次性消费**

在 props 加 `openUrl?: string | null`，并在 WebSocket 连接后通过 `new_tab` 发送一次：

```ts
const lastOpenedUrl = ref('');

const consumeOpenUrl = () => {
  const url = props.openUrl?.trim() || '';
  if (!connected.value || !socket.value || !isBrowserOpenableUrl(url)) return;
  if (url === lastOpenedUrl.value) return;
  lastOpenedUrl.value = url;
  send({ type: 'new_tab', url });
};

watch(() => props.openUrl, () => {
  if (!props.openUrl) lastOpenedUrl.value = '';
  consumeOpenUrl();
});
```

在 `client.onopen` 设置 `connected.value = true` 后调用 `consumeOpenUrl()`；不改人工地址栏的 `normalizeNavigationUrl`，不发送非 HTTP(S) 地址。

- [ ] **Step 4: 运行 BrowserPanel 测试**

运行 Task 3 Step 2 命令，并回归 `tests/frontend/test_browser_panel_contract.py`，预期全部通过。

### Task 4: 测试清单、类型检查与验收

**Files:**
- Modify: `tests/CHECKLIST.md`
- Test: `tests/frontend/test_message_browser_open_contract.py`

- [ ] **Step 1: 更新清单**

新增一行，记录 HTTP(S) 按钮、无会话目标 URL 初始化、已有会话 `new_tab`、协议白名单和窄面板样式测试；记录契约测试、`vue-tsc --noEmit`、`git diff --check` 结果，并明确真实浏览器/服务端会话需手工验收。只暂存本次新增行。

- [ ] **Step 2: 运行聚焦前端测试**

```bash
pytest --confcutdir=tests/frontend \
  tests/frontend/test_message_browser_open_contract.py \
  tests/frontend/test_message_renderer_contract.py \
  tests/frontend/test_browser_panel_contract.py \
  tests/frontend/test_embed_instance_session_contract.py -q
```

预期：本次功能及相关回归测试通过。

- [ ] **Step 3: 运行类型检查**

工作目录：`frontend`。

```bash
./node_modules/.bin/vue-tsc --noEmit
```

预期：通过。

- [ ] **Step 4: 运行 diff 检查**

```bash
git diff --check -- frontend/src/utils/messageBrowserLinks.ts frontend/src/components/MessageRenderer.vue frontend/src/views/EmbedChat.vue frontend/src/components/embed/BrowserPanel.vue tests/frontend/test_message_browser_open_contract.py tests/CHECKLIST.md
```

预期：无空白错误。

- [ ] **Step 5: 记录手工验收边界**

用户启动服务后验证裸 URL、Markdown URL、带 token URL、无会话初始化、已有面板新标签页、长 URL 窄面板布局，以及原链接/`quick:`/`canvas:` 行为。Agent 不执行 `./dev.sh`，不把静态测试表述为真实服务验收。
