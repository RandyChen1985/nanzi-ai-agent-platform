<template>
  <section class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
    <button
      type="button"
      class="flex w-full items-center justify-between gap-3 bg-gray-50 px-4 py-2.5 text-left transition-colors hover:bg-gray-100/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400"
      :class="collapsed ? '' : 'border-b border-gray-200'"
      :aria-expanded="!collapsed"
      :aria-controls="bodyId"
      :title="collapsed ? `展开「${title}」` : `折叠「${title}」`"
      @click="emit('toggle')"
    >
      <span class="flex min-w-0 items-center gap-2">
        <slot name="icon" />
        <!-- 标题用 span 而不是 h3：button 的内容模型只允许 phrasing content -->
        <span class="truncate text-xs font-bold text-gray-900 sm:text-sm">{{ title }}</span>
        <slot name="meta" />
      </span>
      <span class="flex shrink-0 items-center gap-2 text-[10px] font-bold text-gray-400">
        <slot name="count" />
        <svg
          class="h-3.5 w-3.5 text-gray-400 transition-transform"
          :class="collapsed ? '' : 'rotate-90'"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
        </svg>
      </span>
    </button>

    <!-- v-show 而非 v-if：折叠只收起视觉，树内已展开的节点等状态不丢 -->
    <div v-show="!collapsed" :id="bodyId" class="p-3">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
import { useId } from 'vue'

withDefaults(
  defineProps<{
    title: string
    /** 折叠态由父层持有（刷新复位、本次会话内保留），组件本身不持久化 */
    collapsed?: boolean
  }>(),
  { collapsed: false },
)

const emit = defineEmits<{
  (e: 'toggle'): void
}>()

const bodyId = `permission-section-${useId()}`
</script>
