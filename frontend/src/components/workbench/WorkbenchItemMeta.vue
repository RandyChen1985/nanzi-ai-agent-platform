<template>
  <div v-if="hasMeta" class="mt-2 flex flex-wrap items-center gap-1.5">
    <span
      v-if="kindLabel"
      class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium"
      :class="kindClass"
    >
      <span aria-hidden="true">{{ kindIcon }}</span>
      {{ kindLabel }}
    </span>
    <span
      v-if="severityLabel"
      class="rounded border px-1.5 py-0.5 text-[10px] font-medium"
      :class="severityClass"
    >{{ severityLabel }}</span>
    <span
      v-if="statusLabel"
      class="rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-gray-600"
    >{{ statusLabel }}</span>
    <span v-if="relativeTime" class="text-[11px] text-gray-400">{{ relativeTime }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"
import {
  formatWorkbenchRelativeTime,
  resolveWorkbenchKind,
  workbenchKindClass,
  workbenchKindIcon,
  workbenchKindLabel,
  workbenchSeverityClass,
  workbenchSeverityLabel,
  workbenchStatusLabel,
} from "@/utils/workbenchDisplay"

const props = defineProps<{
  occurredAt?: string | null
  nextRunAt?: string | null
  severity?: string | null
  status?: string | null
  type?: string | null
  action?: string | null
  showKind?: boolean
}>()

const relativeTime = computed(() =>
  formatWorkbenchRelativeTime(props.nextRunAt || props.occurredAt || "")
)
const severityLabel = computed(() => workbenchSeverityLabel(props.severity))
const severityClass = computed(() => workbenchSeverityClass(props.severity))
const statusLabel = computed(() => workbenchStatusLabel(props.status))
const kind = computed(() => resolveWorkbenchKind({ type: props.type, action: props.action }))
const kindLabel = computed(() => (props.showKind === false ? "" : workbenchKindLabel(kind.value)))
const kindClass = computed(() => workbenchKindClass(kind.value))
const kindIcon = computed(() => workbenchKindIcon(kind.value))
const hasMeta = computed(
  () => Boolean(relativeTime.value || severityLabel.value || statusLabel.value || kindLabel.value)
)
</script>
