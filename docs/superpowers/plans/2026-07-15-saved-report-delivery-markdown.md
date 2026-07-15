# 运行历史推送内容 Markdown 预览 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将黄金报表运行历史的推送内容安全地渲染为紧凑 Markdown 预览。

**Architecture:** 在 Markdown 工具层新增禁用原始 HTML 的预览渲染器；`DatasetCapabilityMenu.vue` 只负责调用和展示。通过行为测试锁定安全规则，通过组件契约锁定运行历史接入。

**Tech Stack:** Vue 3、TypeScript、markdown-it、pytest 源码契约、Node 行为测试

---

### Task 1: 安全 Markdown 渲染器

**Files:**
- Create: `frontend/src/utils/safeMarkdown.ts`
- Create: `tests/frontend/test_saved_report_delivery_markdown.py`

- [x] **Step 1: 写失败测试**

测试 `renderSafeMarkdownPreview` 能渲染标题和列表、转义 `<script>`，且不生成 `javascript:` 链接。

- [x] **Step 2: 运行测试确认失败**

Run: `venv/bin/python -m pytest tests/frontend/test_saved_report_delivery_markdown.py -q`
Expected: FAIL，安全渲染器尚不存在。

- [x] **Step 3: 实现最小安全渲染器**

新增独立 `MarkdownIt({ html: false, linkify: true, typographer: true, breaks: true })` 实例，复用表格规范化并为外部链接补充安全属性。

- [x] **Step 4: 运行行为测试**

Run: `venv/bin/python -m pytest tests/frontend/test_saved_report_delivery_markdown.py -q`
Expected: PASS。

### Task 2: 运行历史详情接入

**Files:**
- Modify: `frontend/src/components/chatbi/DatasetCapabilityMenu.vue`
- Test: `tests/frontend/test_saved_report_delivery_markdown.py`

- [x] **Step 1: 写失败契约**

断言组件导入并调用 `renderSafeMarkdownPreview`，使用 `v-html` 和 `markdown-body`，且旧的 delivery `<pre>` 不再存在。

- [x] **Step 2: 替换展示并增加紧凑样式**

使用带最大高度、滚动、折行和深色模式的 article 容器；补充标题、段落、列表、引用、代码、表格和链接样式。

- [x] **Step 3: 完整验证**

Run: `venv/bin/python -m pytest tests/frontend -m no_infrastructure -q`
Expected: PASS；相关 SFC 编译解析通过，`git diff --check` 无输出。不运行 `./dev.sh`。
