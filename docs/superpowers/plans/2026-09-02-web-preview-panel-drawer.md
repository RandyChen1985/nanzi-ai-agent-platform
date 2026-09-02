# 网页预览面板抽屉交互 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让网页预览面板具备工作区抽屉同款的桌面钉住、宽度拖拽和移动端自适应体验。

**Architecture:** `WebPreviewPanel.vue` 独立管理面板展示、钉住状态、移动端切换、宽度拖拽和本地宽度偏好；`EmbedChat.vue` 只维护面板显隐、URL 与布局占位宽度，并在打开网页预览时隐藏自动化浏览器面板。网页内容仍使用受限 iframe，不引入后端浏览器会话。

**Tech Stack:** Vue 3、TypeScript、Tailwind CSS、pytest 前端契约测试。

---

### Task 1: 工作区抽屉交互契约

**Files:**
- Modify: `tests/frontend/test_web_preview_panel_contract.py`
- Modify: `tests/frontend/test_embed_browser_open_wiring_contract.py`

- [ ] **Step 1: 写失败契约**

断言网页预览面板提供 `pinned`、`panelWidth`、桌面拖拽、双击重置、移动端切换、宽度本地存储和父页面布局占位。

- [ ] **Step 2: 运行失败测试**

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_web_preview_panel_contract.py tests/frontend/test_embed_browser_open_wiring_contract.py -q
```

预期新增交互断言失败，因为当前面板还是固定宽度遮罩实现。

### Task 2: 实现自适应抽屉

**Files:**
- Modify: `frontend/src/components/embed/WebPreviewPanel.vue`
- Modify: `frontend/src/views/EmbedChat.vue`

- [ ] **Step 1: 实现面板状态与拖拽**

复用 `WorkspaceBrowserDrawer.vue` 的交互边界：桌面最小宽度 320px、最大宽度不超过视口减去 300px，双击拖拽条恢复默认值；移动端自动取消钉住并切换为全宽底部抽屉。

- [ ] **Step 2: 接入父页面布局**

新增网页预览的 `v-model:pinned` 与 `v-model:panel-width`，父页面在钉住时为主聊天区域让出宽度，并避免网页预览与自动化浏览器面板同时显示。

### Task 3: 回归验证

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 运行定向测试、类型检查和差异检查**

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_message_browser_open_contract.py tests/frontend/test_embed_browser_open_wiring_contract.py tests/frontend/test_web_preview_panel_contract.py tests/frontend/test_message_renderer_contract.py tests/frontend/test_browser_panel_contract.py tests/frontend/test_embed_instance_session_contract.py -q
(cd frontend && ./node_modules/.bin/vue-tsc --noEmit)
git diff --check
```

不启动服务、不执行真实浏览器会话；浏览器视觉验收由用户在控制台启动服务后完成。
