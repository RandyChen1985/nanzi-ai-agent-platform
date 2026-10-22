<script setup lang="ts">
import { formatTokenCompact } from "@/utils/tokenFormat"
import type { WorkbenchPersonalResource } from "@/types/workbench"

withDefaults(
  defineProps<{
    items: WorkbenchPersonalResource[]
    /** Embed 欢迎页：强制一行 5 卡 + 更紧凑字号 */
    compact?: boolean
  }>(),
  { compact: false },
)

const emit = defineEmits<{
  (e: "select", item: WorkbenchPersonalResource): void
}>()

const displayValue = (item: WorkbenchPersonalResource) => {
  if (item.status === "error") return "--"
  if (item.key === "tokens") return formatTokenCompact(item.value)
  return String(item.value ?? 0)
}

const getResourceStyle = (key: string) => {
  switch (key) {
    case "memory":
      return {
        iconBg: "bg-purple-50 text-purple-600 dark:bg-purple-950/40 dark:text-purple-400",
        hoverBorder: "hover:border-purple-300 dark:hover:border-purple-600/50",
      }
    case "tokens":
      return {
        iconBg: "bg-emerald-50 text-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-400",
        hoverBorder: "hover:border-emerald-300 dark:hover:border-emerald-600/50",
      }
    case "data":
      return {
        iconBg: "bg-blue-50 text-blue-600 dark:bg-blue-950/40 dark:text-blue-400",
        hoverBorder: "hover:border-blue-300 dark:hover:border-blue-600/50",
      }
    case "skills":
      return {
        iconBg: "bg-amber-50 text-amber-600 dark:bg-amber-950/40 dark:text-amber-400",
        hoverBorder: "hover:border-amber-300 dark:hover:border-amber-600/50",
      }
    case "mcp":
      return {
        iconBg: "bg-indigo-50 text-indigo-600 dark:bg-indigo-950/40 dark:text-indigo-400",
        hoverBorder: "hover:border-indigo-300 dark:hover:border-indigo-600/50",
      }
    case "tasks":
      return {
        iconBg: "bg-rose-50 text-rose-600 dark:bg-rose-950/40 dark:text-rose-400",
        hoverBorder: "hover:border-rose-300 dark:hover:border-rose-600/50",
      }
    case "inbox":
      return {
        iconBg: "bg-sky-50 text-sky-600 dark:bg-sky-950/40 dark:text-sky-400",
        hoverBorder: "hover:border-sky-300 dark:hover:border-sky-600/50",
      }
    default:
      return {
        iconBg: "bg-gray-50 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
        hoverBorder: "hover:border-blue-200 dark:hover:border-blue-600/50",
      }
  }
}
</script>

<template>
  <section
    v-if="items.length"
    class="grid"
    :class="compact
      ? 'grid-cols-2 gap-2 sm:grid-cols-5'
      : 'grid-cols-2 gap-2.5 sm:grid-cols-3 xl:grid-cols-6'"
  >
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="group relative overflow-hidden border bg-white text-left shadow-xs transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md dark:bg-gray-800/80 dark:border-gray-700/80"
      :class="[
        compact
          ? 'rounded-xl p-2.5 sm:rounded-xl sm:p-2'
          : 'rounded-2xl p-3.5',
        item.status === 'error'
          ? 'border-amber-200 dark:border-amber-700/60 hover:border-amber-300'
          : [getResourceStyle(item.key).hoverBorder, 'border-gray-100/90 dark:border-gray-700/70'],
      ]"
      @click="emit('select', item)"
    >
      <div class="flex items-center gap-2.5">
        <span
          class="flex shrink-0 items-center justify-center rounded-lg transition-transform group-hover:scale-110"
          :class="compact ? 'h-9 w-9 rounded-[10px]' : 'h-10 w-10 rounded-xl', getResourceStyle(item.key).iconBg"
        >
          <!-- memory -->
          <svg v-if="item.key === 'memory'" :class="compact ? 'h-5 w-5' : 'h-[22px] w-[22px]'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" />
            <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" />
            <path d="M12 5v13" />
          </svg>
          <!-- tokens -->
          <svg v-else-if="item.key === 'tokens'" :class="compact ? 'h-5 w-5' : 'h-[22px] w-[22px]'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
          </svg>
          <!-- data -->
          <svg v-else-if="item.key === 'data'" :class="compact ? 'h-5 w-5' : 'h-[22px] w-[22px]'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <ellipse cx="12" cy="5" rx="9" ry="3" />
            <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
            <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
          </svg>
          <!-- skills -->
          <svg v-else-if="item.key === 'skills'" :class="compact ? 'h-5 w-5' : 'h-[22px] w-[22px]'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="m12 3 1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3Z" />
            <path d="M18 15.5 18.9 18l2.5.9-2.5.9-.9 2.5-.9-2.5-2.5-.9 2.5-.9.9-2.5Z" />
          </svg>
          <!-- mcp -->
          <svg v-else-if="item.key === 'mcp'" :class="compact ? 'h-5 w-5' : 'h-[22px] w-[22px]'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 22v-5" />
            <path d="M9 8V2" /><path d="M15 8V2" />
            <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />
          </svg>
          <!-- tasks -->
          <svg v-else-if="item.key === 'tasks'" :class="compact ? 'h-5 w-5' : 'h-[22px] w-[22px]'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="8" y="2" width="8" height="4" rx="1" />
            <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
            <path d="m9 14 2 2 4-4" />
          </svg>
          <!-- inbox -->
          <svg v-else-if="item.key === 'inbox'" :class="compact ? 'h-5 w-5' : 'h-[22px] w-[22px]'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
            <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
          </svg>
          <!-- default -->
          <svg v-else :class="compact ? 'h-5 w-5' : 'h-[22px] w-[22px]'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 8v4" /><path d="M12 16h.01" />
          </svg>
        </span>
        <div class="min-w-0 flex-1">
          <p
            class="truncate font-medium text-gray-500 dark:text-gray-400"
            :class="compact ? 'text-[11px] leading-tight' : 'text-[11px]'"
          >
            {{ item.label }}
          </p>
          <p
            class="truncate font-bold tracking-tight text-gray-900 tabular-nums dark:text-gray-100"
            :class="compact ? 'mt-0.5 text-lg sm:text-base' : 'mt-0.5 text-xl'"
          >
            {{ displayValue(item) }}
          </p>
          <div
            class="flex items-center justify-between text-gray-400 dark:text-gray-500"
            :class="compact ? 'mt-0.5 text-[10px] sm:text-[9px] sm:leading-tight' : 'mt-0.5 text-[11px]'"
          >
        <span v-if="item.status === 'error'" class="text-amber-500 dark:text-amber-400 font-medium">暂时无法获取</span>
        <span v-else class="truncate">{{ item.unit }}</span>
        <svg class="h-3 w-3 opacity-0 -translate-x-1 transition-all group-hover:opacity-100 group-hover:translate-x-0 text-gray-400 dark:text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="9 18 15 12 9 6" />
          </svg>
          </div>
        </div>
      </div>
    </button>
  </section>
</template>

