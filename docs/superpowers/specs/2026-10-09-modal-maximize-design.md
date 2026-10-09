# 弹框最大化（Modal Maximize）设计

- 日期：2026-10-09
- 状态：已评审（用户确认「应用内最大化」+「加到通用 Modal，opt-in」后进入实施）
- 范围：通用 `frontend/src/components/Modal.vue` 新增 opt-in 的最大化能力；知识库管理「预览原文件」弹框接入
- 不在范围内：浏览器 Fullscreen API、Esc 关闭弹框（Modal 目前就没有，属既有缺口，另议）、最大化状态持久化、其余 31 处弹框的改动

## 1. 问题陈述

知识库管理里选中文档后点「📄 预览原文件」，当前呈现是一个居中弹框：

- 容器是通用 `Modal` 组件，`size="max-w-5xl"`，即最大宽度 1024px（`frontend/src/views/KnowledgeBaseManagement.vue` 的预览块）
- 内容区固定 `h-[70vh]`，内部按格式分两个分支：`DocumentViewer`（file-viewer，覆盖 Office 6 种 + 文本 56 种 + PDF）或 `<iframe>` 兜底（`.rst` `.env` `.ppt` 等）

看宽表格或大页 PDF 时，1024px 宽 × 70vh 高明显偏窄，且高度写死为视口比例，大屏上浪费空间。用户诉求：**加一个把窗口放大的功能。**

## 2. 目标与非目标

**目标**

1. 弹框可一键切换「最大化」：铺满整个视口（保留半透明遮罩与标题栏），再点还原
2. 能力放在通用 `Modal` 组件里，但**默认关闭**，不改变现有 32 处弹框的任何行为
3. 关闭弹框后重新打开回到普通尺寸，不记忆最大化状态

**非目标**

- 不接浏览器 Fullscreen API（用户明确选择「应用内最大化」）
- 不加 Esc 关闭（既有缺口，本次不扩大范围）
- 不做状态持久化（`localStorage`）
- 不改动知识库以外的任何 `Modal` 使用点

## 3. 现状事实（动手前核对过）

| 事实 | 位置 |
| --- | --- |
| 容器 class 含 `max-h-[calc(100dvh-1.5rem)] sm:max-h-[calc(100vh-2rem)]`，宽度由 `:class="size \|\| 'max-w-md'"` 决定 | `Modal.vue:33-34` |
| header 右侧结构为 `<slot name="header-extra">` + 关闭按钮 | `Modal.vue:39-51` |
| 内容区为 `custom-scrollbar min-h-0 flex-1 overflow-y-auto p-4 sm:p-6`（**flex-1 + min-h-0**，可用高度由 flex 分配） | `Modal.vue:55` |
| `Modal` 被 8 个文件共 **32 处**使用；`header-extra` 插槽已有先例（`MetadataCronInspectionModal.vue:348`） | 全仓 grep |
| 应用内最大化的两个既有先例 | `WebPreviewPanel.vue` 的 `isMaximized`（「最大化查看 / 退出最大化」）、`ChatCanvas.vue` 的 `isFullscreen` + `toggleFullscreen()`（「全屏展示 / 半屏展示」，关闭时复位） |

两个先例都**不是** Fullscreen API，都是应用内布局切换，本次沿用同一模式。`ChatCanvas` 的展开/收缩箭头 SVG 直接复用，保持图标语言一致。

## 4. 方案与被否方案

差异在于**最大化状态归谁持有**：

| 方案 | 做法 | 结论 |
| --- | --- | --- |
| **A（采用）** | `Modal` 内部持有 `isMaximized`，通过 **默认插槽的 slot prop** 把 `maximized` 暴露给使用方 | 按钮、尺寸、关闭复位本就是一个整体；使用方只写一行三元 class；不写 `<template #default>` 的现有用法向后兼容 |
| B | `Modal` 提供 `v-model:maximized`，使用方自己声明 ref | 使用方更灵活，但每个使用点都要多一个 ref，当前需求用不上 |
| C | Modal 约定内容用 `h-full`，最大化时自动撑满，使用方零参与 | 会改变 Modal 的默认高度语义并波及现有弹框，不可取 |

## 5. 接口设计

```ts
// Modal.vue
const props = withDefaults(defineProps<{
  title: string
  show?: boolean
  size?: string
  zIndex?: number | string
  maximizable?: boolean   // 新增，默认 false
}>(), {
  show: true,
  zIndex: 60,
  maximizable: false,
})
```

默认插槽新增一个 prop：

```html
<div class="custom-scrollbar min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
  <slot :maximized="isMaximized"></slot>
</div>
```

**向后兼容论证**：Vue 的插槽 props 是附加信息，现有 31 处直接写内容（不声明 `<template #default>`）的用法会照常渲染并忽略该 prop，无需任何改动。

## 6. 行为细节

### 6.1 尺寸切换

- 普通态：宽度 `size || 'max-w-md'`（知识库传 `max-w-5xl`），高度上限 `max-h-[calc(100dvh-1.5rem)] sm:max-h-[calc(100vh-2rem)]`，由内容撑开
- 最大化态：宽度改为 `max-w-none`（外层容器本身是 `fixed inset-0 ... p-3 sm:p-4`，所以铺满的是「视口减去这圈留白」，仍能看见遮罩边缘，点遮罩即可关闭），**同时**补一个与 max-h 同值的确定高度 `h-[calc(100dvh-1.5rem)] sm:h-[calc(100vh-2rem)]`

**为什么必须补确定高度**：内容区是 `flex-1 min-h-0`，其高度在「父容器高度由内容决定」时不是确定值，子元素的 `height: 100%` 会解析失败。把容器高度固定下来后，使用方的 `h-full` 才有可靠参照 —— 这样就不必去硬编码「header 约 57px + 内容区 padding 48px + 留白 32px」这类脆弱估算值（估错会导致溢出或出现多余滚动条）。

### 6.2 使用方（知识库）

```html
<Modal :show="showDocPreview" ... size="max-w-5xl" maximizable>
  <template #default="{ maximized }">
    <div :class="maximized ? 'h-full w-full' : 'h-[70vh] w-full'">
```

普通态保持 `70vh`（现状不变），最大化态用 `h-full` 吃掉内容区的全部可用高度。`DocumentViewer` 与 `iframe` 两个分支共用这个容器，因此都受益，无需分别处理。

### 6.3 按钮

- 位置：header 右侧、`header-extra` 插槽之后、关闭按钮之前
- 图标：复用 `ChatCanvas` 的展开/收缩箭头（`M4 8V4h4m12 4V4h-4M4 16v4h4m12-4v4h-4` / 反向），`aria-hidden`
- `title` / `aria-label`：「最大化预览」/「还原窗口」
- 仅在 `maximizable` 为真时渲染
- **小屏隐藏**（`hidden sm:inline-flex`）：Modal 在小屏本来就是 `p-3` + `w-full`，可用区已接近满屏，最大化几乎无视觉差异；用纯 CSS 隐藏而不是引入 `matchMedia` 监听，避免给一个 32 处复用的组件增加每个实例一个媒体查询监听的固定开销

### 6.4 状态复位

`watch(() => props.show)`：`show` 变为 `false` 时把 `isMaximized` 复位为 `false`。每次打开都是普通尺寸，不记忆。

### 6.5 尺寸变化后的重排（风险与保险）

切换最大化的瞬间，容器尺寸变了但**浏览器不会派发 `window.resize`**。用户手动拖动窗口时文件预览能重排，靠的正是那个事件；若 file-viewer 的渲染器（pdf.js / docx 等）只监听 `window.resize`，最大化后画面可能停在旧尺寸（画布不重排、虚拟滚动位置错位）。

保险措施：切换后在 `nextTick` + `requestAnimationFrame` 中补派发一次 `window.dispatchEvent(new Event('resize'))`，效果与用户拖窗口一致。**这是保险而非既定结论**——若实测确认渲染器使用 `ResizeObserver`（无需该事件即可重排），实现时应移除该 hack，避免无谓地触发页面上其他 resize 监听者重算。

## 7. 测试策略

| 层次 | 内容 |
| --- | --- |
| 源码契约（新增 `tests/frontend/test_modal_maximize_contract.py`） | `maximizable` 默认 `false`、`isMaximized` 状态与切换存在、`title`/`aria-label` 存在、小屏隐藏按钮、`show` 关闭时复位、默认插槽暴露 `maximized`、知识库已开启该属性并在最大化时切到 `h-full` |
| SFC 编译 | 对 `Modal.vue` 与 `KnowledgeBaseManagement.vue` 跑 `compileTemplate` / `compileScript`，要求 0 错误 |
| 类型门禁 | `NODE_OPTIONS=--max-old-space-size=6144 npx vue-tsc -b --force`，维持基线 62 条且本次改动文件零命中（注意：本仓库 `vue-tsc --noEmit` 因根 tsconfig 为 `files: []` 而恒 exit 0，是空操作） |
| 回归 | 前端契约全量（`pytest tests/frontend -q --confcutdir=tests/frontend`），确保 32 处现有弹框的既有契约不受影响 |
| 人工验收（需用户执行 `./dev.sh`） | 知识库预览 PDF 与 DOCX：点最大化铺满、再点还原、关闭重开后回到普通尺寸；最大化状态下确认 file-viewer 画面确实重排（这是 6.5 的验证点） |

## 8. 待验证项

1. file-viewer 在容器尺寸变化后是否自行重排 —— 决定 6.5 的 `resize` 派发是保留还是移除
2. 最大化态在超宽屏（如 2560px）下的观感：`max-w-none` 是否过宽（若过宽，可考虑加上限如 `max-w-[1600px]`，但当前不预设）
