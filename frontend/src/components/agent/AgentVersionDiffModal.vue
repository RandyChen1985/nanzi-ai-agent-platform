<script setup lang="ts">
import { computed, ref, watch } from "vue";
import Modal from "../Modal.vue";
import type { AIAgentVersion } from "../../api/agent";
import {
  buildAgentVersionDiff,
  filterAgentVersionDiffGroups,
  type AgentVersionDiff,
  type VersionDiffChange,
} from "../../utils/agentVersionDiff";

const props = defineProps<{
  show: boolean;
  sourceVersion: AIAgentVersion | null;
  publishedVersion: AIAgentVersion | null;
}>();

const emit = defineEmits<{
  close: [];
}>();

const showOnlyChanges = ref(false);

const diff = computed<AgentVersionDiff | null>(() => {
  if (!props.sourceVersion || !props.publishedVersion) return null;
  return buildAgentVersionDiff(props.sourceVersion, props.publishedVersion);
});

const visibleGroups = computed(() => {
  if (!diff.value) return [];
  return filterAgentVersionDiffGroups(diff.value, showOnlyChanges.value);
});

const toggleOnlyChanges = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  showOnlyChanges.value = target?.checked ?? false;
};

watch(() => props.show, (show) => {
  if (show) showOnlyChanges.value = false;
});

const statusLabel = (status: AIAgentVersion["status"]) => {
  if (status === "PUBLISHED") return "当前线上";
  if (status === "ARCHIVED") return "归档";
  return "草稿";
};

const changeLabel = (change: VersionDiffChange) => {
  if (change === "added") return "线上新增";
  if (change === "removed") return "线上移除";
  if (change === "modified") return "已修改";
  return "无变化";
};

const changeClass = (change: VersionDiffChange) => {
  if (change === "added") return "bg-emerald-50 text-emerald-700";
  if (change === "removed") return "bg-red-50 text-red-700";
  if (change === "modified") return "bg-amber-50 text-amber-700";
  return "bg-gray-100 text-gray-500";
};

const formatCreatedAt = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString();
};
</script>

<template>
  <Modal
    v-if="show && sourceVersion && publishedVersion && diff"
    title="版本 Diff"
    size="max-w-5xl"
    :show="show"
    :z-index="70"
    @close="emit('close')"
  >
    <div class="space-y-5">
      <div class="rounded-xl border border-indigo-100 bg-indigo-50/70 px-4 py-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="font-semibold text-gray-900">
            V{{ sourceVersion.version_number }} → 当前线上 V{{ publishedVersion.version_number }}
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <span class="rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-indigo-700">
              {{ diff.identical ? '配置一致' : `${diff.changedCount} 项变化` }}
            </span>
            <label class="inline-flex cursor-pointer items-center gap-2 rounded-full border border-indigo-100 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm">
              <span>仅显示变化</span>
              <span class="relative inline-flex h-5 w-9 shrink-0">
                <input
                  type="checkbox"
                  class="peer sr-only"
                  :checked="showOnlyChanges"
                  aria-label="仅显示变化"
                  @change="toggleOnlyChanges"
                />
                <span class="absolute inset-0 rounded-full bg-gray-200 transition-colors peer-checked:bg-indigo-600 peer-focus-visible:ring-2 peer-focus-visible:ring-indigo-500 peer-focus-visible:ring-offset-2"></span>
                <span class="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform peer-checked:translate-x-4"></span>
              </span>
            </label>
          </div>
        </div>
        <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
          <span>源版本状态：{{ statusLabel(sourceVersion.status) }}（{{ formatCreatedAt(sourceVersion.created_at) }}）</span>
          <span>当前线上发布：{{ formatCreatedAt(publishedVersion.created_at) }}</span>
        </div>
        <div class="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          <div class="rounded-lg bg-white/80 px-2.5 py-1.5 text-gray-600 border border-indigo-100/60">
            <span class="font-medium text-gray-700">V{{ sourceVersion.version_number }} 备注：</span>{{ sourceVersion.comment || '无备注' }}
          </div>
          <div class="rounded-lg bg-white/80 px-2.5 py-1.5 text-gray-600 border border-indigo-100/60">
            <span class="font-medium text-gray-700">线上 V{{ publishedVersion.version_number }} 备注：</span>{{ publishedVersion.comment || '无备注' }}
          </div>
        </div>
        <p class="mt-1 text-[11px] text-gray-400">只读对比 · 历史/草稿版本 → 当前线上版本</p>
      </div>

      <div
        v-if="showOnlyChanges && visibleGroups.length === 0"
        class="flex items-center gap-3 rounded-xl border border-emerald-100 bg-emerald-50/70 px-4 py-5 text-emerald-800"
      >
        <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-lg">✓</span>
        <div>
          <div class="text-sm font-semibold">当前版本与线上版本一致</div>
          <p class="mt-0.5 text-xs text-emerald-700">没有需要重点关注的配置项</p>
        </div>
      </div>

      <div
        v-for="group in visibleGroups"
        :key="group.id"
        class="overflow-hidden rounded-xl border border-gray-200"
      >
        <div class="flex items-center justify-between border-b border-gray-100 bg-gray-50 px-4 py-3">
          <h3 class="text-sm font-semibold text-gray-800">{{ group.label }}</h3>
          <span class="text-xs text-gray-500">
            {{ group.changedCount ? `${group.changedCount} 项变化` : '无变化' }}
          </span>
        </div>
        <div class="divide-y divide-gray-100">
          <div
            v-for="entry in group.items"
            :key="entry.key"
            class="p-4"
            :class="entry.changed ? 'bg-white' : 'bg-gray-50/50'"
          >
            <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
              <span class="text-xs font-semibold text-gray-700">{{ entry.label }}</span>
              <span
                class="rounded-full px-2 py-0.5 text-[11px] font-medium"
                :class="changeClass(entry.change)"
              >
                {{ changeLabel(entry.change) }}
              </span>
            </div>
            <div class="grid gap-3 md:grid-cols-2">
              <div>
                <div class="mb-1 text-[11px] font-medium text-gray-400">历史/草稿版本</div>
                <pre class="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-red-50/60 p-3 text-xs leading-5 text-gray-700">{{ entry.sourceText }}</pre>
              </div>
              <div>
                <div class="mb-1 text-[11px] font-medium text-gray-400">当前线上版本</div>
                <pre class="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-emerald-50/60 p-3 text-xs leading-5 text-gray-700">{{ entry.publishedText }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Modal>
</template>
