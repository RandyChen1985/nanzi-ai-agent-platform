<template>
  <section class="space-y-4">
    <!-- Header -->
    <div class="bg-gray-50/40 dark:bg-gray-800/10 backdrop-blur-sm border border-gray-150 dark:border-gray-800/80 rounded-xl p-3 flex flex-wrap justify-between items-center gap-3">
      <div class="flex items-center gap-2.5">
        <div class="flex items-center justify-center w-8 h-8 rounded-lg bg-blue-600 dark:bg-blue-500 text-white shadow-sm flex-shrink-0">
          <svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2v-4zM14 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2v-4z"/>
          </svg>
        </div>
        <div class="min-w-0">
          <h3 class="text-sm font-bold text-gray-900 dark:text-gray-100 tracking-wide">我的数据门户</h3>
          <div class="flex flex-wrap items-center gap-1.5 mt-0.5 text-[10px]">
            <span
              v-if="payload.dataset_count !== undefined"
              class="font-semibold text-blue-600 dark:text-blue-400"
            >
              {{ payload.dataset_count }} 个数据集
            </span>
            <span class="text-gray-300 dark:text-gray-700">|</span>
            <span v-if="payload.generated_at" class="text-gray-500 dark:text-gray-400">
              更新时间 {{ formattedGeneratedAt }}
            </span>
            <template v-if="payload.dataset_menu_hash">
              <span class="text-gray-300 dark:text-gray-700">|</span>
              <span class="text-gray-400 dark:text-gray-500 font-mono">
                {{ payload.dataset_menu_hash.slice(0, 8) }}
              </span>
            </template>
          </div>
        </div>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-xs font-semibold text-gray-600 dark:text-gray-300 shadow-sm transition-all hover:bg-gray-50 dark:hover:bg-gray-700/50 hover:border-gray-300 dark:hover:border-gray-600 active:scale-95"
        @click="handleRefreshClick"
      >
        <svg 
          class="w-3.5 h-3.5 transition-transform duration-700"
          :class="{ 'animate-spin': isRefreshing }"
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
        刷新
      </button>
    </div>

    <!-- Cards Grid -->
    <div class="grid gap-4">
      <article
        v-for="(group, idx) in props.payload.groups || []"
        :key="group.id || group.title"
        class="group/card rounded-xl border border-gray-150 dark:border-gray-800/80 bg-white dark:bg-gray-900/30 p-4 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all duration-300"
      >
        <!-- Card Title -->
        <div class="flex items-start justify-between gap-3">
          <div class="flex items-start gap-3 min-w-0">
            <!-- Icon -->
            <div 
              class="flex-shrink-0 flex items-center justify-center w-8 h-8 rounded-lg shadow-sm border"
              :class="[
                getGroupVisuals(idx, group.title).bg,
                getGroupVisuals(idx, group.title).text,
                getGroupVisuals(idx, group.title).border
              ]"
              v-html="getGroupVisuals(idx, group.title).icon"
            ></div>
            <div class="min-w-0">
              <h4 class="text-sm font-bold text-gray-900 dark:text-gray-100 leading-normal flex items-center gap-1.5">
                {{ group.title }}
              </h4>
              <p class="mt-1 text-xs text-gray-500 dark:text-gray-400 leading-normal">
                {{ group.summary }}
              </p>
            </div>
          </div>
          
          <!-- Tags -->
          <div v-if="group.tags?.length" class="flex flex-wrap justify-end gap-1 flex-shrink-0">
            <span
              v-for="tag in group.tags.slice(0, 3)"
              :key="tag"
              class="rounded-full bg-gray-50 dark:bg-gray-800/80 border border-gray-100 dark:border-gray-700/60 px-2 py-0.5 text-[9px] font-bold text-gray-500 dark:text-gray-400"
            >
              {{ tag }}
            </span>
          </div>
        </div>

        <!-- You Can Ask Section -->
        <div v-if="group.questions?.length" class="mt-4">
          <div class="mb-2 text-[10px] font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider flex items-center gap-1 select-none">
            <svg class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
            你可以这样问
          </div>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="question in group.questions"
              :key="question.query"
              type="button"
              class="group/btn relative inline-flex items-center gap-1.5 rounded-lg border border-blue-100 dark:border-blue-900/30 bg-blue-50/30 dark:bg-blue-950/20 px-3 py-2 text-left text-xs font-semibold text-blue-700 dark:text-blue-300 transition-all hover:bg-blue-50 hover:border-blue-300/60 dark:hover:bg-blue-900/40 hover:-translate-y-0.5 active:translate-y-0 shadow-sm"
              @click="handleQuestionClick(question, group)"
            >
              <svg class="w-3.5 h-3.5 text-blue-400/80 group-hover/btn:text-blue-500 dark:text-blue-400/60 dark:group-hover/btn:text-blue-300 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
              </svg>
              <span>{{ question.label }}</span>
              <span 
                v-if="question.click_count" 
                class="ml-1 inline-flex items-center px-1 py-0.5 rounded bg-amber-500/10 text-[9px] font-bold text-amber-600 border border-amber-500/20 dark:bg-amber-500/20 dark:text-amber-300 dark:border-amber-500/30 shadow-sm"
              >
                🔥 常用 {{ question.click_count }}
              </span>
            </button>
          </div>
        </div>

        <!-- Related Data Section -->
        <div v-if="group.related_data?.length" class="mt-4 border-t border-gray-100 dark:border-gray-800/80 pt-3">
          <button
            type="button"
            class="flex items-center justify-between w-full text-left text-[10px] font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider hover:text-gray-600 dark:hover:text-gray-300 transition-colors select-none"
            @click="toggleGroup(group.id || group.title)"
          >
            <span class="flex items-center gap-1.5">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/>
              </svg>
              相关数据
            </span>
            <svg
              class="w-3.5 h-3.5 transform transition-transform duration-300"
              :class="{ 'rotate-180': expandedGroups[group.id || group.title] }"
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          
          <div 
            class="grid transition-all duration-300 ease-in-out overflow-hidden"
            :class="expandedGroups[group.id || group.title] ? 'grid-rows-[1fr] opacity-100 mt-2.5' : 'grid-rows-[0fr] opacity-0'"
          >
            <div class="overflow-hidden">
              <div class="space-y-3 bg-gray-50/50 dark:bg-gray-950/20 rounded-xl p-3 border border-gray-100 dark:border-gray-800">
                <div v-for="related in group.related_data" :key="related.dataset || related.display_name" class="space-y-1.5">
                  <div class="text-[11px] font-bold text-gray-500 dark:text-gray-400 flex items-center gap-1 select-none">
                    <svg class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2z"/>
                    </svg>
                    {{ related.display_name || related.dataset }}
                  </div>
                  <div class="flex flex-wrap gap-1.5">
                    <span
                      v-for="table in related.tables || []"
                      :key="table"
                      class="inline-flex items-center gap-1 rounded bg-white dark:bg-gray-800 px-2 py-0.5 text-[10px] font-medium text-gray-600 dark:text-gray-300 ring-1 ring-gray-100 dark:ring-gray-700/60 shadow-sm hover:scale-102 hover:shadow-xs transition-all duration-200 cursor-default"
                    >
                      <svg class="w-2.5 h-2.5 text-gray-400 dark:text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/>
                      </svg>
                      {{ table }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Follow-ups Section -->
        <div v-if="group.followups?.length" class="mt-4 border-t border-gray-100 dark:border-gray-800/80 pt-3">
          <div class="mb-2 text-[10px] font-bold text-gray-400 dark:text-gray-500 uppercase tracking-wider flex items-center gap-1 select-none">
            <svg class="w-3.5 h-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 5l7 7-7 7M5 5l7 7-7 7"/>
            </svg>
            继续追问
          </div>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="followup in group.followups"
              :key="followup.query"
              type="button"
              class="inline-flex items-center gap-1 px-2.5 py-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition-colors bg-gray-50/50 dark:bg-gray-800/20 hover:bg-blue-50/30 dark:hover:bg-blue-900/20 border border-gray-200/50 dark:border-gray-700 hover:border-blue-100 dark:hover:border-blue-900/40 rounded-lg shadow-xs active:scale-95 font-medium"
              @click="emitQuickQuestion(followup.query)"
            >
              <span>{{ followup.label }}</span>
            </button>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, watch, computed } from "vue";

interface DatasetCapabilityQuestion {
  label: string;
  query: string;
  type?: string;
  click_count?: number;
  last_clicked_at?: string;
}

interface DatasetCapabilityRelatedData {
  dataset?: string;
  display_name?: string;
  tables?: string[];
  table_descriptions?: Array<{ name: string; description?: string }>;
}

interface DatasetCapabilityGroup {
  id?: string;
  title: string;
  summary: string;
  tags?: string[];
  questions?: DatasetCapabilityQuestion[];
  related_data?: DatasetCapabilityRelatedData[];
  followups?: DatasetCapabilityQuestion[];
}

interface DatasetNavigationPayload {
  dataset_count?: number;
  dataset_menu_hash?: string;
  generated_at?: string;
  groups?: DatasetCapabilityGroup[];
  markdown?: string;
}

const props = defineProps<{
  payload: DatasetNavigationPayload;
}>();

const emit = defineEmits<{
  (event: "quick-question", query: string): void;
  (event: "record-question-click", payload: { query: string; label?: string; group_id?: string }): void;
  (event: "refresh"): void;
}>();

const isRefreshing = ref(false);
const expandedGroups = ref<Record<string, boolean>>({});

const formattedGeneratedAt = computed(() => {
  if (!props.payload.generated_at) return "";
  const date = new Date(props.payload.generated_at);
  if (Number.isNaN(date.getTime())) return props.payload.generated_at;
  return date.toLocaleString();
});

const handleRefreshClick = () => {
  isRefreshing.value = true;
  emit("refresh");
  // 防御性安全重置
  setTimeout(() => {
    isRefreshing.value = false;
  }, 5000);
};

// 监听 payload 的变化重置刷新动画
watch(
  () => props.payload,
  () => {
    isRefreshing.value = false;
  },
  { deep: true }
);

const toggleGroup = (groupId: string) => {
  expandedGroups.value[groupId] = !expandedGroups.value[groupId];
};

const emitQuickQuestion = (query?: string) => {
  const text = String(query || "").trim();
  if (text) {
    emit("quick-question", text);
  }
};

const handleQuestionClick = (question: DatasetCapabilityQuestion, group: DatasetCapabilityGroup) => {
  const query = String(question.query || "").trim();
  if (!query) return;
  emit("record-question-click", {
    query,
    label: question.label,
    group_id: group.id,
  });
  emitQuickQuestion(query);
};

// 图标与视觉渐变定义辅助函数
const getGroupVisuals = (index: number, title: string) => {
  const titleStr = title || "";
  
  if (titleStr.includes("分析") || titleStr.includes("监控") || titleStr.includes("日志") || titleStr.includes("诊断")) {
    return {
      bg: "bg-blue-50 dark:bg-blue-950/20",
      border: "border-blue-100 dark:border-blue-900/30",
      text: "text-blue-500 dark:text-blue-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 002 2h2a2 2 0 002-2z"/>
      </svg>`
    };
  }
  
  if (titleStr.includes("智能体") || titleStr.includes("Agent") || titleStr.includes("大模型") || titleStr.includes("AI")) {
    return {
      bg: "bg-violet-50 dark:bg-violet-950/20",
      border: "border-violet-100 dark:border-violet-900/30",
      text: "text-violet-500 dark:text-violet-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/>
      </svg>`
    };
  }
  
  if (titleStr.includes("数据") || titleStr.includes("看板") || titleStr.includes("报表") || titleStr.includes("门户")) {
    return {
      bg: "bg-emerald-50 dark:bg-emerald-950/20",
      border: "border-emerald-100 dark:border-emerald-900/30",
      text: "text-emerald-500 dark:text-emerald-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 8v8m-4-5v5m-4-2v2m-2 4h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
      </svg>`
    };
  }

  // 默认根据 index 索引轮换
  const visuals = [
    {
      bg: "bg-blue-50 dark:bg-blue-950/20",
      border: "border-blue-100 dark:border-blue-900/30",
      text: "text-blue-500 dark:text-blue-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/>
      </svg>`
    },
    {
      bg: "bg-purple-50 dark:bg-purple-950/20",
      border: "border-purple-100 dark:border-purple-900/30",
      text: "text-purple-500 dark:text-purple-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/>
      </svg>`
    },
    {
      bg: "bg-teal-50 dark:bg-teal-950/20",
      border: "border-teal-100 dark:border-teal-900/30",
      text: "text-teal-500 dark:text-teal-400",
      icon: `<svg class="w-4.5 h-4.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>
      </svg>`
    }
  ];
  return visuals[index % visuals.length];
};
</script>

<style scoped>
.animate-pulse-slow {
  animation: pulse 2.5s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: .85;
    transform: scale(0.98);
  }
}
</style>
