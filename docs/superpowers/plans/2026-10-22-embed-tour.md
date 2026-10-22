# EmbedChat 功能引导实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `/embed/chat` 加一套 10 步聚光灯引导，首次自动播、断点续播、Redis 跨设备记忆、设置内可重播。

**Architecture:** 前端自研 `ChatTour` 展示组件 + `useEmbedTour` 状态机 + `tourSteps` 静态定义；目标元素用 `data-tour` 锚点；状态存后端 Redis 的 portal_prefs（3 个新字段 + 专用 PUT 端点）。

**Tech Stack:** Vue 3 `<script setup>` + TypeScript + Tailwind 3；FastAPI + Redis（`app/core/redis.py`）；pytest 纯前端契约测试。

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `app/api/portal/endpoints/portal_prefs.py` | 3 个字段 + `PUT /embed-tour` |
| `frontend/src/components/embed/tourSteps.ts` | 10 步定义与类型 |
| `frontend/src/composables/embed/useEmbedTour.ts` | 状态机、断点、完成、版本比对、localStorage 兜底 |
| `frontend/src/components/embed/ChatTour.vue` | 遮罩、高亮框、气泡、键盘、定位 |
| `frontend/src/views/EmbedChat.vue` | 3 个锚点 + 挂载 ChatTour + 自动播条件 |
| `frontend/src/components/embed/ChatInput.vue` | 5 个锚点（输入框、+、浮标、模型、沙箱区块）+ 弹层开关暴露 |
| `frontend/src/components/embed/ChatSettings.vue` | 「功能引导」分组与重播按钮 |
| `tests/frontend/test_embed_tour_contract.py` | 契约测试 |
| `tests/CHECKLIST.md` | 登记 |

## Task 1: 后端偏好字段与专用端点

**Files:** Modify `app/api/portal/endpoints/portal_prefs.py`；Test `tests/api/portal/test_embed_tour_prefs.py`

- [ ] 在 `PortalPrefs` 与 `PortalPrefsUpdate` 增加 `embed_tour_version: int`、`embed_tour_step: int`、`embed_tour_completed_at: str`（Update 用 `Optional`）。
- [ ] 增加 `EmbedTourUpdate(BaseModel)`：`version: int`、`step: int`、`completed_at: str = ""`。
- [ ] 增加 `PUT /embed-tour`：读-改-写，`step` 夹在 `0..8`，返回三字段。
- [ ] 测试：字段默认值；端点写读一致；`step` 越界被夹紧；未带字段时其他偏好不被清空。
- [ ] 验证：`pytest tests/api/portal/test_embed_tour_prefs.py -q`。

## Task 2: 步骤定义

**Files:** Create `frontend/src/components/embed/tourSteps.ts`

- [ ] 定义 `TourStep`：`id`、`selector`、`title`、`body`、`beforeEnter?`、`afterLeave?`、`placement?: 'auto' | 'top' | 'bottom'`。
- [ ] 导出 `TOUR_VERSION = 1`、`TOUR_STEPS: TourStep[]`（10 步，文案见 spec 表格）。
- [ ] 第 7 步 `beforeEnter` 由调用方注入（打开上下文弹层），步骤定义里只声明 `opensContextPanel: true`。

## Task 3: 状态机

**Files:** Create `frontend/src/composables/embed/useEmbedTour.ts`

- [ ] `useEmbedTour(options)` 返回 `{ active, stepIndex, step, start, next, prev, finish, skip, resumeOrStart, loading }`。
- [ ] `loadState()` 读 `GET /api/portal/portal-prefs`，失败读 `localStorage['embed_tour_state']`。
- [ ] `shouldAutoPlay()`：版本一致、`completed_at` 空、`step < 8`、非移动端、无 `?tour=0|off`。
- [ ] 离开一步时 500ms 防抖 `PUT /api/portal/portal-prefs/embed-tour`；失败写 localStorage。
- [ ] `finish()` 上报 `step=8` + `completed_at=now`；`skip()` 只上报当前 step。

## Task 4: 展示组件

**Files:** Create `frontend/src/components/embed/ChatTour.vue`

- [ ] Props：`steps`、`modelValue`（是否显示）、`initialIndex`；Emits：`update:modelValue`、`leave(index)`、`finish`。
- [ ] 四块遮罩（上下左右）围出镂空；高亮框 `pointer-events: none`；遮罩拦截点击但不关闭。
- [ ] 气泡：优先下方、空间不足翻上；箭头指向目标水平中心；`scrollIntoView` 后测量；`scroll`/`resize` 重新定位。
- [ ] 键盘：`Esc` 退出、`←/→`、`Enter` 下一步；按钮：`跳过引导`、`上一步`、`下一步/完成`、进度 `n / 10`。
- [ ] 目标不存在时 emit `leave(index)` 并自动前进。

## Task 5: EmbedChat 接入

**Files:** Modify `frontend/src/views/EmbedChat.vue`

- [ ] 给顶部状态、右上按钮组、欢迎卡片加 `data-tour="header-status" | "header-actions" | "welcome-cards"`。
- [ ] 挂载 `<ChatTour v-if="tourActive" :steps="TOUR_STEPS" ... />`。
- [ ] `onMounted` 等 `hasPermission` 后延迟 600ms `resumeOrStart()`；`?tour=0|off` 短路。
- [ ] 传 `isMobile` 给状态机（移动端不自动播）。

## Task 6: ChatInput 锚点

**Files:** Modify `frontend/src/components/embed/ChatInput.vue`

- [ ] `data-tour="composer"`（输入框外壳）、`composer-plus`、`composer-pills`、`composer-model`。
- [ ] 沙箱区块 `data-tour="sandbox-block"`。
- [ ] `defineExpose({ openContextUsage })`：供引导打开上下文弹层；返回「是否由引导打开」。
- [ ] `defineExpose({ closeContextUsage })`：只关引导打开的那次。

## Task 7: 设置入口

**Files:** Modify `frontend/src/components/embed/ChatSettings.vue`

- [ ] 新增分组「功能引导 / Feature Tour」：说明文案 + `重新播放功能引导` 按钮 + `上次完成：…`。
- [ ] Props 增加 `tourCompletedAt?: string`；Emits 增加 `replay-tour`。
- [ ] EmbedChat 监听 `@replay-tour`：关闭设置、清零 step、立即开播。

## Task 8: 契约测试

**Files:** Create `tests/frontend/test_embed_tour_contract.py`

- [ ] 10 个 `data-tour` 锚点均存在且与 `TOUR_STEPS` 的选择器一致。
- [ ] `ChatTour` 挂载、`ChatInput` 暴露 `openContextUsage`/`closeContextUsage`。
- [ ] `TOUR_VERSION` 与后端默认版本一致；`?tour=0` 短路；移动端不自动播。
- [ ] 设置入口文案存在。

## Task 9: 验证与登记

- [ ] `pytest --confcutdir=tests/frontend tests/frontend/test_embed_tour_contract.py tests/api/portal/test_embed_tour_prefs.py -q`
- [ ] `cd frontend && NODE_OPTIONS=--max-old-space-size=4096 npx vue-tsc -b --force`（本文件零命中）
- [ ] `tests/CHECKLIST.md` 登记。
