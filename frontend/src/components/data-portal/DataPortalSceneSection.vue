<template>
  <section>
    <div class="mb-3 flex items-center justify-between gap-3"><div><h2 class="text-base font-bold text-gray-900 dark:text-gray-100">推荐场景</h2><p v-if="!compact" class="mt-1 text-xs text-gray-400">基于当前有权限的数据生成，可直接分析或先填入问题调整</p></div><span v-if="compact" class="text-xs text-gray-400">根据当前可用数据生成</span></div>
    <div v-if="visibleGroups.length" class="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <article v-for="group in visibleGroups" :key="groupKey(group)" class="rounded-2xl border bg-white p-4 dark:bg-gray-900" :class="isDatasetDisabled(group) ? 'border-dashed border-amber-200 opacity-75 dark:border-amber-900/50' : 'border-gray-100 dark:border-gray-800'">
        <div class="flex items-start justify-between gap-3"><div><h3 class="text-sm font-bold text-gray-900 dark:text-gray-100 flex flex-wrap items-center gap-1.5">{{ group.title }}<span v-if="isDatasetDisabled(group)" class="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-bold text-amber-700 dark:border-amber-800/60 dark:bg-amber-950/40 dark:text-amber-300">未启用</span></h3><p class="mt-1 text-xs" :class="isDatasetDisabled(group) ? 'text-amber-600 dark:text-amber-400' : 'text-gray-500 dark:text-gray-400'">{{ isDatasetDisabled(group) ? '数据集未启用，暂不可查询' : group.summary }}</p></div><button v-if="!compact" type="button" class="shrink-0 rounded-lg px-2 py-1 text-xs text-primary transition hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-primary-dark/30" :disabled="isDatasetDisabled(group) || !!refreshing[groupKey(group)]" @click="refreshGroup(group)"><span :class="{ 'inline-block animate-spin': refreshing[groupKey(group)] }">↻</span> {{ refreshing[groupKey(group)] ? '生成中' : '换一批' }}</button></div>
        <div class="mt-3 space-y-2" :class="{ 'pointer-events-none select-none': isDatasetDisabled(group) }">
          <div v-for="question in groupQuestions(group)" :key="question.query" class="flex items-center gap-2 rounded-xl bg-primary/5 p-2 dark:bg-primary-dark/30">
            <button type="button" class="min-w-0 flex-1 text-left text-xs leading-5 text-primary-dark dark:text-primary-hover" :disabled="isDatasetDisabled(group)" @click="ask(group, question.query, 'send')">{{ question.label }}</button>
            <button v-if="!compact" type="button" class="shrink-0 rounded-md bg-white px-2 py-1 text-[11px] text-gray-500 shadow-sm hover:text-primary disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-900 dark:text-gray-300" :disabled="isDatasetDisabled(group)" @click="ask(group, question.query, 'fill')">填入</button>
            <button v-if="!compact" type="button" class="shrink-0 rounded-md bg-primary px-2 py-1 text-[11px] text-white hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-50" :disabled="isDatasetDisabled(group)" @click="ask(group, question.query, 'send')">分析</button>
          </div>
        </div>
        <p v-if="refreshErrors[groupKey(group)]" class="mt-2 text-xs text-amber-600">{{ refreshErrors[groupKey(group)] }}</p>
        <div v-if="!compact && group.followups?.length" class="mt-3 border-t border-gray-100 pt-3 dark:border-gray-800" :class="{ 'pointer-events-none select-none': isDatasetDisabled(group) }"><span class="text-[11px] text-gray-400">还可以继续探索</span><div class="mt-2 flex flex-wrap gap-2"><button v-for="question in group.followups" :key="question.query" type="button" class="rounded-lg border border-gray-200 px-2.5 py-1.5 text-left text-xs text-gray-600 hover:border-primary/20 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:text-gray-300" :disabled="isDatasetDisabled(group)" @click="ask(group, question.query, 'fill')">{{ question.label }}</button></div></div>
      </article>
    </div>
    <div v-else class="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-sm text-gray-400 dark:border-gray-800">暂无推荐场景</div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import axios from "@/utils/axios";
import type { DatasetPortalGroup, DatasetPortalPayload, DatasetPortalQuestion } from "@/composables/useDatasetPortal";

const props = withDefaults(defineProps<{ payload: DatasetPortalPayload; compact?: boolean }>(), { compact: false });
const emit = defineEmits<{ (event: "quick-question", query: string, action: "send" | "fill"): void }>();
const refreshedQuestions = ref<Record<string, DatasetPortalQuestion[]>>({});
const refreshing = ref<Record<string, boolean>>({});
const refreshErrors = ref<Record<string, string>>({});
const visibleGroups = computed(() => props.compact ? (props.payload.groups || []).slice(0, 4) : (props.payload.groups || []));
const groupKey = (group: DatasetPortalGroup) => String(group.id || group.title);
const isDatasetDisabled = (group: DatasetPortalGroup) => group.enabled === false;
const groupQuestions = (group: DatasetPortalGroup) => (refreshedQuestions.value[groupKey(group)] || group.questions || []).slice(0, 3);
const ask = (group: DatasetPortalGroup, query: string, action: "send" | "fill") => {
  if (isDatasetDisabled(group)) return;
  emit("quick-question", query, action);
};
const refreshGroup = async (group: DatasetPortalGroup) => {
  if (isDatasetDisabled(group)) return;
  const key = groupKey(group);
  const tables = Array.from(new Set((group.related_data || []).flatMap((item) => item.tables || [])));
  refreshing.value = { ...refreshing.value, [key]: true };
  refreshErrors.value = { ...refreshErrors.value, [key]: "" };
  try {
    const response = await axios.post("/api/v1/chat/dataset-menu/refresh-group-questions", { group_id: group.id || "", group_title: group.title, tables, exclude_questions: groupQuestions(group), purpose: "questions" });
    const questions = (response.data?.data?.questions || []) as DatasetPortalQuestion[];
    if (!questions.length) throw new Error(response.data?.data?.refresh_disabled_reason || "暂无更多问题");
    refreshedQuestions.value = { ...refreshedQuestions.value, [key]: questions };
  } catch (error: any) {
    refreshErrors.value = { ...refreshErrors.value, [key]: error?.response?.data?.detail || error?.message || "生成失败，请稍后重试" };
  } finally {
    refreshing.value = { ...refreshing.value, [key]: false };
  }
};
</script>
