# Chat Surface Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `EmbedChat.vue` 与 `AgentDebug.vue` 中重复的聊天业务能力下沉为共享 composable 和组件，同时保持正式聊天与调试页的所有现有行为不变。

**Architecture:** 两个页面继续作为独立外壳，正式聊天保留嵌入通信、欢迎页与移动端体验，调试页保留配置面板、原始提示词和链路诊断。共享的纯函数、黄金报表、工作区画布、附件、历史会话和流式事件按风险从低到高逐批下沉；每批先用双页面契约测试锁定行为，再替换调用点。

**Tech Stack:** Vue 3 `<script setup>`, TypeScript, Axios, pytest 前端契约测试, `@vue/compiler-sfc` 静态解析。

---

## 文件边界

- `frontend/src/composables/chat/useSavedReportWorkflow.ts`：报表参数识别、预览、执行错误归一化和结果 Markdown 格式化。
- `frontend/src/composables/chat/useWorkspaceCanvas.ts`：画布显隐、工作区文件预览、Blob URL 生命周期。
- `frontend/src/composables/chat/useChatAttachments.ts`：图片、技能、记忆、知识库附件上下文构建。
- `frontend/src/composables/chat/useChatHistoryGroups.ts`：历史日期分组；两页不同的加载、分页和脏数据策略保留在页面层。
- `frontend/src/composables/chat/useChatStream.ts`：SSE 生命周期与共享事件分发，不包含页面专属 UI。
- `frontend/src/components/chat/ChatMessageList.vue`：共享消息循环和用户/智能体消息分派。
- `frontend/src/components/chat/ChatThinkingProcess.vue`：思考链展示。
- `frontend/src/components/chat/SavedReportDialogs.vue`：保存与运行报表弹窗。
- `frontend/src/views/EmbedChat.vue`：正式聊天外壳及嵌入通信。
- `frontend/src/views/AgentDebug.vue`：调试外壳及调试专属面板。

### Task 1: 建立双页面重构保护线

**Files:**
- Create: `tests/frontend/test_chat_surface_refactor_contract.py`
- Modify: `tests/CHECKLIST.md`

- [x] **Step 1: 写失败的共享边界测试**

```python
def test_both_chat_surfaces_use_shared_saved_report_workflow():
    embed = read("frontend/src/views/EmbedChat.vue")
    debug = read("frontend/src/views/AgentDebug.vue")
    assert "useSavedReportWorkflow" in embed
    assert "useSavedReportWorkflow" in debug
```

- [x] **Step 2: 运行测试并确认因共享模块尚不存在而失败**

Run: `venv/bin/python -m pytest tests/frontend/test_chat_surface_refactor_contract.py -q`
Expected: FAIL，缺少 `useSavedReportWorkflow`。

- [x] **Step 3: 添加现状特征测试**

锁定两个页面共同依赖的数据门户、知识门户、工作区画布、停止生成、权限确认、黄金报表执行和附件处理入口，确保后续抽取不删除任何入口。

- [x] **Step 4: 运行全部现有前端契约测试**

Run: `venv/bin/python -m pytest tests/frontend -q`
Expected: 除新共享边界断言外，其余测试通过。

### Task 2: 抽取黄金报表纯函数与工作流

**Files:**
- Create: `frontend/src/composables/chat/useSavedReportWorkflow.ts`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Test: `tests/frontend/test_chat_surface_refactor_contract.py`

- [x] **Step 1: 在测试中约定共享 API**

```python
for name in (
    "detectSavedReportDateTemplate",
    "buildSavedReportRunParams",
    "renderSavedReportDataToMarkdown",
    "extractSavedReportExecuteErrorMessage",
):
    assert f"export const {name}" in shared
```

- [x] **Step 2: 确认测试失败**

Run: `venv/bin/python -m pytest tests/frontend/test_chat_surface_refactor_contract.py -q`
Expected: FAIL，共享文件不存在。

- [x] **Step 3: 原样迁移纯函数并显式注入网络与提示依赖**

共享模块不得直接读取页面局部 ref；需要页面状态的动作通过参数传入，确保两个页面仍保留自己的弹窗状态和提示文案。

- [x] **Step 4: 删除两页重复实现并改用共享导出**

保留原方法名称作为页面调用点，避免模板行为变化。

- [x] **Step 5: 运行契约和 SFC 解析**

Run: `venv/bin/python -m pytest tests/frontend/test_chat_surface_refactor_contract.py tests/frontend/test_data_portal_home_contract.py -q`
Expected: PASS。

### Task 3: 抽取工作区画布生命周期

**Files:**
- Create: `frontend/src/composables/chat/useWorkspaceCanvas.ts`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Test: `tests/frontend/test_chat_surface_refactor_contract.py`

- [x] **Step 1: 写画布共享契约测试**

断言两页都从 `useWorkspaceCanvas` 获取 `canvasVisible`、`canvasData`、`handleWorkspaceFilePreview`、`closeCanvas` 和 `revokeActiveBlobUrl`。

- [x] **Step 2: 确认测试失败**

Run: `venv/bin/python -m pytest tests/frontend/test_chat_surface_refactor_contract.py -q`
Expected: FAIL，尚未使用共享画布 composable。

- [x] **Step 3: 迁移状态与资源回收逻辑**

保留 `conversationId`、overlay、dockSide、授权预览 URL 和组件卸载时 Blob 回收语义；页面仅负责把 composable 返回值传给 `ChatCanvas`。

- [x] **Step 4: 运行画布相关契约**

Run: `venv/bin/python -m pytest tests/frontend -q -k "workspace or canvas or chat_surface"`
Expected: PASS。

### Task 4: 抽取附件与历史会话

**Files:**
- Create: `frontend/src/composables/chat/useChatAttachments.ts`
- Create: `frontend/src/composables/chat/useChatHistory.ts`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Test: `tests/frontend/test_chat_surface_refactor_contract.py`

- [x] **Step 1: 写附件和历史共享契约测试**

锁定图片提示、技能附件、知识库附件、记忆会话、历史搜索、历史分组及新会话行为。

- [x] **Step 2: 确认测试失败**

Run: `venv/bin/python -m pytest tests/frontend/test_chat_surface_refactor_contract.py -q`
Expected: FAIL，共享 composable 尚未接入。

- [x] **Step 3: 迁移无 UI 的状态和方法**

附件上下文与历史日期分组已迁移。历史网络端点、分页方式和脏数据策略在两页并不相同，因此继续保留在各页面；正式页与调试页的侧栏布局也继续由各自页面控制。

- [x] **Step 4: 运行历史与附件契约**

Run: `venv/bin/python -m pytest tests/frontend -q -k "attachment or history or chat_surface"`
Expected: PASS。

### Task 5: 拆分共享消息展示组件

**Files:**
- Create: `frontend/src/components/chat/ChatThinkingHeader.vue`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Test: `tests/frontend/test_chat_surface_refactor_contract.py`

- [x] **Step 1: 写组件状态契约测试**

测试必须覆盖消息操作、取证卡片、继续分析、权限确认、外部执行和附件预览事件，防止为了缩短模板而丢功能。

- [x] **Step 2: 确认测试失败**

Run: `venv/bin/python -m pytest tests/frontend/test_chat_surface_refactor_contract.py -q`
Expected: FAIL，共享消息组件尚不存在。

- [x] **Step 3: 抽取共享思考头部**

共享展开按钮、运行态图标、步骤数、折叠数、技能摘要和耗时。日志主体与消息操作在两页差异很大，继续保留在页面层，避免引入几十个条件 props 和事件透传。

- [x] **Step 4: 运行全部前端契约和 SFC 解析**

Run: `venv/bin/python -m pytest tests/frontend -q`
Expected: PASS。

### Task 6: 收敛共享流式事件归一化

**Files:**
- Modify: `frontend/src/utils/agentscopeSseHandlers.ts`
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`
- Test: `tests/frontend/test_chat_surface_refactor_contract.py`

- [x] **Step 1: 为共享事件归一化写失败测试**

覆盖文本增量、日志、引用、ChatBI insight、权限确认、外部执行、生成文件、错误、停止和完成事件。

- [x] **Step 2: 把共享 trace 与 citation 归一化为 typed handler**

页面专属 postMessage、调试日志和 UI 状态通过回调注入；不得改变 SSE 字段名、消息顺序和完成清理时机。

- [x] **Step 3: 两页主流和恢复流逐一切换到共享 handler**

先切 `AgentDebug.vue`，全绿后再切 `EmbedChat.vue`，每次只改变一个页面。

- [x] **Step 4: 全量回归**

Run: `venv/bin/python -m pytest tests/frontend -q`
Expected: PASS；所有相关 SFC 解析无错误，`git diff --check` 无输出。

### Task 7: 完成度审计

**Files:**
- Modify: `tests/CHECKLIST.md`
- Modify: `docs/superpowers/plans/2026-07-15-chat-surface-refactor.md`

- [x] **Step 1: 对照功能矩阵逐项检查两个页面**

核对会话、流式输出、停止、重试、编辑重发、附件、历史、数据门户、知识门户、黄金报表、画布、引用、反馈、权限确认、外部工具和调试专属功能。

- [x] **Step 2: 检查重复和文件规模**

Run: `wc -l frontend/src/views/EmbedChat.vue frontend/src/views/AgentDebug.vue`
Expected: 两个页面均显著下降，且共享功能只有一个实现来源。

- [x] **Step 3: 最终验证**

Run: `venv/bin/python -m pytest tests/frontend -q && git diff --check`
Expected: PASS 且无格式错误。根据项目约束，不运行 `./dev.sh`、前端构建或服务重启。

结果：68 项无基础设施前端回归通过、3 项基础设施用例按标记跳过；3 个相关 Vue SFC 编译解析通过，`git diff --check` 无输出。`EmbedChat.vue` 从 7108 行降至 6605 行，`AgentDebug.vue` 从 6480 行降至 5986 行。
