<template>
  <span
    ref="triggerRef"
    class="inline-flex items-center"
    @mouseenter="show"
    @mouseleave="hide"
  >
    <slot />
    <Teleport to="body">
      <span
        v-if="visible"
        role="tooltip"
        class="pointer-events-none fixed z-[100] rounded-lg bg-gray-900 px-3 py-2 text-left text-[11px] font-normal leading-relaxed text-white shadow-xl"
        :style="panelStyle"
      >
        {{ text }}
      </span>
    </Teleport>
  </span>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

/**
 * 悬停说明气泡，替代原生 `title`。
 *
 * 为什么不用原生 title：一两秒才出现、样式不可控、换行被压平、触摸设备根本不显示；
 * 而「失联是什么意思」这类需要解释清楚的状态，靠原生 title 等于没解释。
 *
 * 为什么用 Teleport + position: fixed 而不是父元素里的 absolute：
 * 知识库树是 `overflow-y-auto` 的滚动容器，absolute 气泡会被直接裁掉 ——
 * 这正是"看着有 tooltip 但永远显示不出来"的典型原因。传送到 body 后不受任何
 * 祖先的 overflow / transform 影响。
 */
const props = withDefaults(
  defineProps<{
    /** 说明文字，可较长，气泡内自动换行 */
    text: string
    /** 气泡宽度（px），默认 280 */
    width?: number
  }>(),
  { width: 280 },
)

const triggerRef = ref<HTMLElement | null>(null)
const visible = ref(false)
const position = ref({ top: 0, left: 0, placement: 'bottom' as 'bottom' | 'top' })

const EDGE_GAP = 10
const OFFSET = 8
/** 上方至少留这么多空间才翻到上面，否则宁可压在下面 */
const MIN_TOP_SPACE = 110

const updatePosition = () => {
  const el = triggerRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const width = props.width

  // 水平：以触发元素为中心，碰到视口左右边缘时收回来
  let left = rect.left + rect.width / 2 - width / 2
  left = Math.max(EDGE_GAP, Math.min(left, window.innerWidth - width - EDGE_GAP))

  // 垂直：默认显示在下方；下方空间不够且上方够高时翻到上方
  const spaceBelow = window.innerHeight - rect.bottom
  const placement: 'bottom' | 'top' =
    spaceBelow < 120 && rect.top > MIN_TOP_SPACE ? 'top' : 'bottom'
  const top = placement === 'bottom' ? rect.bottom + OFFSET : rect.top - OFFSET

  position.value = { top, left, placement }
}

const panelStyle = computed(() => ({
  top: `${position.value.top}px`,
  left: `${position.value.left}px`,
  width: `${props.width}px`,
  // 翻到上方时用 translateY(-100%) 让气泡底边贴住触发元素
  transform: position.value.placement === 'top' ? 'translateY(-100%)' : 'none',
}))

const onScrollOrResize = () => {
  if (visible.value) updatePosition()
}

const show = () => {
  updatePosition()
  visible.value = true
  // 树在滚动时气泡要跟着走，否则会飘在原地
  window.addEventListener('scroll', onScrollOrResize, true)
  window.addEventListener('resize', onScrollOrResize)
}

const hide = () => {
  visible.value = false
  window.removeEventListener('scroll', onScrollOrResize, true)
  window.removeEventListener('resize', onScrollOrResize)
}

onBeforeUnmount(hide)
</script>
