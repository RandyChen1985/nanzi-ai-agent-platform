<template>
  <span
    class="relative inline-flex items-center"
    @mouseenter="hovered = true"
    @mouseleave="hovered = false"
    @focusin="hovered = true"
    @focusout="onBlur"
  >
    <button
      type="button"
      class="inline-flex items-center justify-center rounded-full text-current opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
      :aria-label="label"
      :aria-expanded="visible"
      @click.stop="pinned = !pinned"
    >
      <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="2"
          d="M12 9h.01M11 12.5h1V17h1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
    </button>

    <span
      v-if="visible"
      role="tooltip"
      class="absolute bottom-full z-[60] mb-2 w-64 rounded-lg px-3 py-2 text-left text-[11px] font-normal leading-relaxed shadow-lg"
      :class="align === 'left' ? 'left-0' : 'right-0'"
    >
      <span class="absolute inset-0 rounded-lg bg-gray-900 opacity-95 dark:bg-gray-700"></span>
      <span class="relative block text-white">{{ text }}</span>
      <span
        class="absolute -bottom-1 h-2 w-2 rotate-45 bg-gray-900 dark:bg-gray-700"
        :class="align === 'left' ? 'left-3' : 'right-3'"
      ></span>
    </span>
  </span>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

/**
 * 自定义说明气泡，替代原生 `title`。
 *
 * 原生 tooltip 的问题：1 秒左右才出现、无法控制样式与换行、移动端（触摸）根本不显示、
 * 长解释会被压成一行。像"人数为什么和各部门相加对不上"这种需要解释清楚的文案，
 * 用原生 title 等于没解释。
 *
 * 交互：悬停即显（桌面）、点击可固定（触摸）、按钮可聚焦（键盘 Tab + Enter）。
 */
const props = withDefaults(
  defineProps<{
    /** 说明文字，可较长，气泡内会自动换行 */
    text: string
    /** 无障碍标签，默认用 text 的开头部分 */
    label?: string
    /** 气泡对齐：靠近右边界时用 right，避免横向溢出 */
    align?: 'left' | 'right'
  }>(),
  { label: '', align: 'right' }
)

const hovered = ref(false)
const pinned = ref(false)

const visible = computed(() => hovered.value || pinned.value)
const label = computed(() => props.label || props.text)

const onBlur = () => {
  hovered.value = false
  pinned.value = false
}
</script>
