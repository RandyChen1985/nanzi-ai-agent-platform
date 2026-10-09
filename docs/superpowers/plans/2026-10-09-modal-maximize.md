# 弹框最大化（Modal Maximize）实施计划

- 日期：2026-10-09
- 依据：`docs/superpowers/specs/2026-10-09-modal-maximize-design.md`
- 规模：小（1 个通用组件加 opt-in 能力 + 1 处接入 + 1 份契约）

## 任务

### Task 1：`Modal.vue` 新增 opt-in 最大化能力

1. `maximizable?: boolean`，`withDefaults` 默认 `false`
2. `isMaximized` 状态与 `toggleMaximize()`；`watch(() => props.show)` 在关闭时复位
3. 容器 `:class` 在最大化时切到 `max-w-none` 并补同值确定高度（`h-[calc(100dvh-1.5rem)] sm:h-[calc(100vh-2rem)]`）
4. header 右侧、关闭按钮之前插入切换按钮：复用 `ChatCanvas` 的展开/收缩箭头、`title`/`aria-label`（「最大化预览」/「还原窗口」）、`hidden sm:inline-flex`
5. 默认插槽暴露 `:maximized="isMaximized"`
6. 切换后在 `nextTick` + `requestAnimationFrame` 派发一次 `window resize`（见 spec 6.5，属保险，实测后可移除）

**验证**：SFC `compileScript` / `compileTemplate` 0 错误。

### Task 2：知识库预览接入

- `<Modal ... maximizable>` + `<template #default="{ maximized }">`
- 预览容器 `:class="maximized ? 'h-full w-full' : 'h-[70vh] w-full'"`（普通态行为不变）

**验证**：SFC 编译 0 错误；`DocumentViewer` 与 `iframe` 两个分支共用该容器。

### Task 3：契约测试

新增 `tests/frontend/test_modal_maximize_contract.py`，覆盖 spec 第 7 节的源码契约条目；并断言知识库确实开启且切换了高度。

**验证**：先确认断言在实现前失败（RED），实现后转 GREEN；抽样做变异验证确认断言非假绿。

### Task 4：回归与登记

- `NODE_OPTIONS=--max-old-space-size=6144 npx vue-tsc -b --force`：维持基线 62 且本次文件零命中
- `pytest tests/frontend -q --confcutdir=tests/frontend`：全量回归
- `tests/CHECKLIST.md` 追加一行交付登记

## 交付边界

- 不执行 `git commit`（按仓库约定，等用户明确要求）
- 不运行 `./dev.sh`，浏览器端人工验收由用户执行
- 若实测发现 file-viewer 自带 `ResizeObserver` 重排，移除 Task 1 第 6 步的 resize 派发
