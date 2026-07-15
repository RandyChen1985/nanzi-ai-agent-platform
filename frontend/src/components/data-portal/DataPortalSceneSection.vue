<template>
  <section>
    <div class="mb-3 flex items-center justify-between gap-3"><div><h2 class="text-base font-bold text-gray-900 dark:text-gray-100">推荐场景</h2><p v-if="!compact" class="mt-1 text-xs text-gray-400">基于当前有权限的数据生成，可直接分析或先填入问题调整</p></div><span v-if="compact" class="text-xs text-gray-400">根据当前可用数据生成</span></div>
    <div v-if="visibleGroups.length" class="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <article v-for="group in visibleGroups" :key="groupKey(group)" class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
        <div class="flex items-start justify-between gap-3"><div><h3 class="text-sm font-bold text-gray-900 dark:text-gray-100">{{ group.title }}</h3><p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ group.summary }}</p></div><button v-if="!compact" type="button" class="shrink-0 rounded-lg px-2 py-1 text-xs text-blue-600 transition hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-blue-950/30" :disabled="!!refreshing[groupKey(group)]" @click="refreshGroup(group)"><span :class="{ 'inline-block animate-spin': refreshing[groupKey(group)] }">↻</span> {{ refreshing[groupKey(group)] ? '生成中' : '换一批' }}</button></div>
        <div class="mt-3 space-y-2">
          <div v-for="question in groupQuestions(group)" :key="question.query" class="flex items-center gap-2 rounded-xl bg-blue-50/70 p-2 dark:bg-blue-950/30">
            <button type="button" class="min-w-0 flex-1 text-left text-xs leading-5 text-blue-700 dark:text-blue-300" @click="emit('quick-question', question.query, 'send')">{{ question.label }}</button>
            <button v-if="!compact" type="button" class="shrink-0 rounded-md bg-white px-2 py-1 text-[11px] text-gray-500 shadow-sm hover:text-blue-600 dark:bg-gray-900 dark:text-gray-300" @click="emit('quick-question', question.query, 'fill')">填入</button>
            <button v-if="!compact" type="button" class="shrink-0 rounded-md bg-blue-600 px-2 py-1 text-[11px] text-white hover:bg-blue-700" @click="emit('quick-question', question.query, 'send')">分析</button>
          </div>
        </div>
        <p v-if="refreshErrors[groupKey(group)]" class="mt-2 text-xs text-amber-600">{{ refreshErrors[groupKey(group)] }}</p>
        <div v-if="!compact && group.followups?.length" class="mt-3 border-t border-gray-100 pt-3 dark:border-gray-800"><span class="text-[11px] text-gray-400">还可以继续探索</span><div class="mt-2 flex flex-wrap gap-2"><button v-for="question in group.followups" :key="question.query" type="button" class="rounded-lg border border-gray-200 px-2.5 py-1.5 text-left text-xs text-gray-600 hover:border-blue-200 hover:text-blue-600 dark:border-gray-700 dark:text-gray-300" @click="emit('quick-question', question.query, 'fill')">{{ question.label }}</button></div></div>
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
const groupQuestions = (group: DatasetPortalGroup) => (refreshedQuestions.value[groupKey(group)] || group.questions || []).slice(0, 3);
const refreshGroup = async (group: DatasetPortalGroup) => {
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
