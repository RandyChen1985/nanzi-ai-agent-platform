import type { Directive } from 'vue'

/**
 * 全局 `v-tooltip` 指令：用平台自己的深色气泡替代浏览器原生 `title`。
 *
 * 原生 title 的问题：悬停约 1 秒才出现、样式不可控、移动端触摸不显示、
 * 长文本（Client ID、Token、完整 Scope 列表）会被压成一行。
 *
 * 实现要点：
 * - 气泡固定挂在 body 上并 `position: fixed`，因此不会被表格的 `overflow` 容器裁切；
 * - 只在悬停 / 聚焦时创建，离开即销毁，不给列表里的每个单元格留常驻节点；
 * - 滚动或窗口尺寸变化时立即收起，避免气泡与触发器错位；
 * - 元素上的原生 `title` 会被主动移除，否则两套提示会叠加。
 */

type TooltipValue = string | number | null | undefined

const GAP = 8
const EDGE = 8
const MAX_WIDTH = 288
const BUBBLE_BG = 'rgba(17, 24, 39, 0.95)'

type Handlers = {
  enter: () => void
  leave: () => void
  focus: () => void
  blur: () => void
}

let activeBubble: HTMLDivElement | null = null
let activeArrow: HTMLSpanElement | null = null
let listening = false
const handlers = new WeakMap<HTMLElement, Handlers>()

const readText = (el: HTMLElement) => (el.dataset.tooltipText || '').trim()

const removeBubble = () => {
  activeBubble?.remove()
  activeBubble = null
  activeArrow = null
}

const hideTooltip = () => removeBubble()

const onViewportChange = () => hideTooltip()

const ensureGlobalListeners = () => {
  if (listening) return
  listening = true
  window.addEventListener('scroll', onViewportChange, true)
  window.addEventListener('resize', onViewportChange)
}

const positionBubble = (el: HTMLElement, bubble: HTMLDivElement, arrow: HTMLSpanElement) => {
  const rect = el.getBoundingClientRect()
  const { width, height } = bubble.getBoundingClientRect()
  const openUpwards = rect.top > height + GAP + EDGE
  const left = Math.max(EDGE, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - EDGE))
  bubble.style.left = `${left}px`
  bubble.style.top = `${openUpwards ? rect.top - height - GAP : rect.bottom + GAP}px`
  bubble.style.visibility = 'visible'

  // 箭头跟着触发器中线走，同时限制在气泡圆角内，避免贴到边缘被裁
  const arrowLeft = Math.max(10, Math.min(rect.left + rect.width / 2 - left - 4, width - 18))
  arrow.style.left = `${arrowLeft}px`
  arrow.style.top = openUpwards ? '' : '-4px'
  arrow.style.bottom = openUpwards ? '-4px' : ''
}

const createBubble = (text: string) => {
  const bubble = document.createElement('div')
  bubble.setAttribute('role', 'tooltip')
  Object.assign(bubble.style, {
    position: 'fixed',
    zIndex: '90',
    maxWidth: `${MAX_WIDTH}px`,
    padding: '6px 10px',
    borderRadius: '8px',
    background: BUBBLE_BG,
    color: '#fff',
    fontSize: '11px',
    lineHeight: '1.5',
    fontWeight: '400',
    textAlign: 'left',
    whiteSpace: 'normal',
    wordBreak: 'break-word',
    pointerEvents: 'none',
    boxShadow: '0 10px 24px rgba(15, 23, 42, 0.18)',
    visibility: 'hidden',
  })

  const arrow = document.createElement('span')
  Object.assign(arrow.style, {
    position: 'absolute',
    width: '8px',
    height: '8px',
    background: BUBBLE_BG,
    transform: 'rotate(45deg)',
    pointerEvents: 'none',
  })

  const content = document.createElement('span')
  content.style.position = 'relative'
  content.style.display = 'block'
  content.textContent = text

  bubble.append(arrow, content)
  document.body.appendChild(bubble)
  return { bubble, arrow }
}

const showTooltip = (el: HTMLElement) => {
  const text = readText(el)
  if (!text) return
  removeBubble()
  const { bubble, arrow } = createBubble(text)
  positionBubble(el, bubble, arrow)
  activeBubble = bubble
  activeArrow = arrow
}

const writeText = (el: HTMLElement, value: TooltipValue) => {
  const text = value === null || value === undefined ? '' : String(value)
  if (text) {
    el.dataset.tooltipText = text
  } else {
    delete el.dataset.tooltipText
  }
}

export const tooltip: Directive<HTMLElement, TooltipValue> = {
  mounted(el, binding) {
    writeText(el, binding.value)
    // 原生 title 会和自定义气泡同时弹出，必须清掉
    el.removeAttribute('title')

    const enter = () => showTooltip(el)
    const leave = () => hideTooltip()
    const focus = () => showTooltip(el)
    const blur = () => hideTooltip()

    el.addEventListener('mouseenter', enter)
    el.addEventListener('mouseleave', leave)
    el.addEventListener('focusin', focus)
    el.addEventListener('focusout', blur)
    handlers.set(el, { enter, leave, focus, blur })
    ensureGlobalListeners()
  },
  updated(el, binding) {
    writeText(el, binding.value)
    if (!readText(el)) {
      hideTooltip()
    }
  },
  unmounted(el) {
    const saved = handlers.get(el)
    if (saved) {
      el.removeEventListener('mouseenter', saved.enter)
      el.removeEventListener('mouseleave', saved.leave)
      el.removeEventListener('focusin', saved.focus)
      el.removeEventListener('focusout', saved.blur)
      handlers.delete(el)
    }
    hideTooltip()
  },
}
