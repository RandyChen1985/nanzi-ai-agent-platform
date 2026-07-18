<template>
  <section
    v-if="items.length"
    class="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm sm:p-5"
  >
    <WorkbenchSectionHeader
      eyebrow="待处理"
      :title="`有 ${items.length} 件事情值得关注`"
      tone="amber"
      view-all-label="查看全部任务"
      @view-all="$emit('view-all')"
    />
    <div class="space-y-2">
      <button
        v-for="item in sortedItems"
        :key="item.id"
        type="button"
        class="flex w-full items-center justify-between rounded-xl border border-gray-100 bg-gray-50/50 px-3 py-3 text-left transition hover:border-amber-200 hover:bg-amber-50/40"
        :class="item.severity === 'critical' ? 'border-l-4 border-l-red-500' : ''"
        @click="$emit('open-item', item)"
      >
        <span class="min-w-0">
          <span class="block text-sm font-medium text-gray-900">{{ item.title }}</span>
          <span class="mt-0.5 block text-xs text-gray-500">{{ item.subtitle }}</span>
          <WorkbenchItemMeta
            :occurred-at="item.occurred_at"
            :severity="item.severity"
            :status="item.status"
            :type="item.type"
            :action="item.action"
          />
        </span>
        <span class="ml-3 shrink-0 text-xs font-medium text-amber-700">{{ actionLabel(item) }}</span>
      </button>
    </div>
    <WorkbenchMobileViewAll label="查看全部任务" @view-all="$emit('view-all')" />
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue"
import type { WorkbenchItem } from "@/types/workbench"
import { workbenchActionLabel } from "@/utils/workbenchDisplay"
import WorkbenchItemMeta from "./WorkbenchItemMeta.vue"
import WorkbenchMobileViewAll from "./WorkbenchMobileViewAll.vue"
import WorkbenchSectionHeader from "./WorkbenchSectionHeader.vue"

const props = defineProps<{ items: WorkbenchItem[] }>()
defineEmits<{
  (event: "open-item", item: WorkbenchItem): void
  (event: "view-all"): void
}>()

const severityRank: Record<string, number> = { critical: 3, warning: 2, info: 1 }

const sortedItems = computed(() =>
  [...props.items].sort((a, b) => {
    const sa = severityRank[String(a.severity || "info")] || 0
    const sb = severityRank[String(b.severity || "info")] || 0
    if (sa !== sb) return sb - sa
    return String(b.occurred_at || "").localeCompare(String(a.occurred_at || ""))
  })
)

const actionLabel = (item: WorkbenchItem) => workbenchActionLabel(item)
</script>
