// 钉住面板的布局决策：总宽封顶、窄容器只保留一个、右侧偏移不重叠。
// 运行：node --experimental-strip-types --no-warnings tests/frontend/pinned_drawer_layout.mjs
import assert from 'node:assert/strict'

import {
  MIN_CHAT_WIDTH_PX,
  SINGLE_PINNED_MAX_WIDTH_PX,
  computePinnedDrawerLayout,
} from '../../frontend/src/utils/pinnedDrawerLayout.ts'

const panel = (key, widthPx) => ({ key, widthPx })

// 1) 没有钉住面板：不占位、没有偏移表
{
  const layout = computePinnedDrawerLayout({ availableWidthPx: 900, panels: [] })
  assert.equal(layout.marginRightPx, 0)
  assert.deepEqual(layout.keptKeys, [])
  assert.deepEqual(layout.droppedKeys, [])
  assert.deepEqual(layout.rightOffsetPx, {})
}

// 2) 单个钉住面板：对话区完整让位，面板贴右边缘
{
  const layout = computePinnedDrawerLayout({
    availableWidthPx: 900,
    panels: [panel('workspace', 448)],
  })
  assert.deepEqual(layout.keptKeys, ['workspace'])
  assert.equal(layout.marginRightPx, 448)
  assert.equal(layout.rightOffsetPx.workspace, 0)
}

// 3) 窄容器（< 1100）只保留一个右侧钉住面板：第二个降级，不能把对话区挤成 0
{
  const layout = computePinnedDrawerLayout({
    availableWidthPx: 900,
    panels: [panel('workspace', 448), panel('canvas', 520)],
  })
  assert.deepEqual(layout.keptKeys, ['workspace'], '窄容器只保留从右到左的第一个面板')
  assert.deepEqual(layout.droppedKeys, ['canvas'])
  assert.equal(layout.marginRightPx, 448, '对话区至少要留下可读宽度，不能被两个面板吃光')
}

// 4) 宽容器可以并排两个：偏移连续，不互相覆盖
{
  const layout = computePinnedDrawerLayout({
    availableWidthPx: 1400,
    panels: [panel('workspace', 448), panel('canvas', 520)],
  })
  assert.deepEqual(layout.keptKeys, ['workspace', 'canvas'])
  assert.equal(layout.marginRightPx, 968)
  assert.equal(layout.rightOffsetPx.workspace, 0, '最右侧面板贴住右边缘')
  assert.equal(layout.rightOffsetPx.canvas, 448, '第二个面板要从第一个面板左侧起步')
  const firstLeftEdge = 1400 - layout.rightOffsetPx.workspace - 448
  const secondRightEdge = 1400 - layout.rightOffsetPx.canvas
  assert.equal(firstLeftEdge, secondRightEdge, '两个面板必须首尾相接，不能重叠也不能留缝')
}

// 5) 宽度不够时按顺序丢弃，保证对话区至少 MIN_CHAT_WIDTH_PX
{
  const layout = computePinnedDrawerLayout({
    availableWidthPx: 1400,
    panels: [panel('workspace', 448), panel('canvas', 520), panel('memory', 448)],
  })
  assert.deepEqual(layout.keptKeys, ['workspace', 'canvas'])
  assert.deepEqual(layout.droppedKeys, ['memory'])
  assert.ok(
    1400 - layout.marginRightPx >= MIN_CHAT_WIDTH_PX,
    `对话区宽度 ${1400 - layout.marginRightPx} 不应小于 ${MIN_CHAT_WIDTH_PX}`,
  )
}

// 6) 单个面板本身就比可用宽度还宽：保留面板，但让位封顶，对话区保底可见
{
  const layout = computePinnedDrawerLayout({
    availableWidthPx: 700,
    panels: [panel('canvas', 520)],
  })
  assert.deepEqual(layout.keptKeys, ['canvas'])
  assert.equal(layout.marginRightPx, 700 - MIN_CHAT_WIDTH_PX)
  assert.ok(700 - layout.marginRightPx >= MIN_CHAT_WIDTH_PX)
}

// 7) 极小容器：让位不能为负
{
  const layout = computePinnedDrawerLayout({
    availableWidthPx: 300,
    panels: [panel('canvas', 520)],
  })
  assert.equal(layout.marginRightPx, 0)
}

// 8) 宽度为 0 / 负数的面板视为未钉住，不参与计算
{
  const layout = computePinnedDrawerLayout({
    availableWidthPx: 1400,
    panels: [panel('workspace', 0), panel('canvas', 520)],
  })
  assert.deepEqual(layout.keptKeys, ['canvas'])
  assert.equal(layout.marginRightPx, 520)
  assert.equal(layout.rightOffsetPx.canvas, 0)
}

// 9) 边界：恰好等于 SINGLE_PINNED_MAX_WIDTH_PX 时允许并排（总宽仍在预算内）
{
  const layout = computePinnedDrawerLayout({
    availableWidthPx: SINGLE_PINNED_MAX_WIDTH_PX,
    panels: [panel('workspace', 380), panel('workspace2', 380)],
  })
  assert.deepEqual(layout.keptKeys, ['workspace', 'workspace2'])
  assert.ok(
    SINGLE_PINNED_MAX_WIDTH_PX - layout.marginRightPx >= MIN_CHAT_WIDTH_PX,
    '并排后对话区仍要满足最小宽度',
  )
}

console.log('pinned_drawer_layout: ok')
