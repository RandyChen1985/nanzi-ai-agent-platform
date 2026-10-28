<template>
  <transition
    enter-active-class="transition-all duration-200 ease-out"
    enter-from-class="opacity-0 -translate-y-1 scale-98"
    enter-to-class="opacity-100 translate-y-0 scale-100"
    leave-active-class="transition-all duration-200 ease-in"
    leave-from-class="opacity-100 translate-y-0 scale-100"
    leave-to-class="opacity-0 -translate-y-1 scale-98"
  >
    <div
      v-if="todo && !isDismissed"
      class="w-full min-w-0 rounded-2xl border border-slate-200/80 bg-slate-50/90 px-3.5 py-2 text-[12px] leading-5 shadow-xs backdrop-blur-xs transition-all dark:border-slate-800/80 dark:bg-slate-900/90"
    >
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="group flex min-w-0 flex-1 items-center gap-2 text-left font-medium text-slate-800 hover:text-slate-950 dark:text-slate-200 dark:hover:text-white"
          :aria-expanded="expanded"
          :aria-label="expanded ? '折叠任务清单' : '展开任务清单'"
          @click="toggleExpanded"
        >
          <!-- 任务滑块/配置图标（对齐截图样式） -->
          <svg
            class="h-3.5 w-3.5 shrink-0 text-slate-500 dark:text-slate-400"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <line x1="4" y1="7" x2="20" y2="7" />
            <circle cx="8" cy="7" r="2.5" />
            <line x1="4" y1="17" x2="20" y2="17" />
            <circle cx="16" cy="17" r="2.5" />
          </svg>

          <!-- 任务标题 -->
          <span class="shrink-0 font-semibold text-slate-800 dark:text-slate-100 text-[13px]">
            {{ todo.title || '任务' }}
          </span>

          <!-- 状态摘要文本（对齐截图：如 1 进行中 · 6 待处理） -->
          <span class="min-w-0 flex-1 truncate text-[12px] font-normal text-slate-400 dark:text-slate-500">
            {{ statusSummary }}
          </span>

          <!-- 终态徽标：已结束的清单不再显示「进行中」；全部完成时摘要已表达，不重复 -->
          <span
            v-if="isSettled && !isAllCompleted"
            class="shrink-0 rounded bg-slate-200/70 px-1.5 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400"
          >
            {{ settledLabel }}
          </span>

          <!-- 折叠/展开箭头 -->
          <svg
            class="h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform duration-200 group-hover:text-slate-600 dark:group-hover:text-slate-300"
            :class="{ 'rotate-180': !expanded }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 15-7-7-7 7" />
          </svg>
        </button>

        <!-- 手动关闭按钮（归档到历史后不再提供，避免被误认为「删除历史」） -->
        <button
          v-if="!isArchived"
          type="button"
          class="flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-200/60 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          aria-label="关闭任务清单"
          title="关闭任务清单"
          @click="closeCard"
        >
          <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <!-- 展开的任务项列表 -->
      <div v-if="expanded" class="mt-2 space-y-1 border-t border-slate-200/60 pt-1.5 dark:border-slate-800/60">
        <div
          v-for="item in todo.todos"
          :key="item.content"
          class="flex items-start gap-2 text-[11px] leading-relaxed transition-colors"
          :class="{
            'text-slate-400 dark:text-slate-500': item.status === 'completed',
            'font-medium text-slate-800 dark:text-slate-100': item.status === 'in_progress',
            'text-slate-500 dark:text-slate-400': item.status === 'pending',
          }"
        >
          <span class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center text-center" aria-hidden="true">
            <!-- 已完成：绿色勾选 -->
            <svg v-if="item.status === 'completed'" class="h-3.5 w-3.5 text-emerald-500 dark:text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="m5 13 4 4L19 7" />
            </svg>
            <!-- 已终结或归档未完成：灰色暂停，不能看起来还在跑 -->
            <svg v-else-if="isSettled || isArchived" class="h-3.5 w-3.5 text-slate-300 dark:text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 6v12M15 6v12" />
            </svg>
            <!-- 进行中：蓝色旋转图标 -->
            <svg v-else-if="item.status === 'in_progress' && showLiveProgress" class="h-3.5 w-3.5 animate-spin text-blue-600 dark:text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            <!-- 待开始：柔和浅灰圆圈 -->
            <svg v-else class="h-3 w-3 text-slate-300 dark:text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="9" stroke-width="2" />
            </svg>
          </span>
          <span class="min-w-0 flex-1 break-words" :class="item.status === 'completed' ? 'line-through decoration-slate-300 dark:decoration-slate-600' : ''">
            {{ item.content }}
          </span>
          <span
            v-if="item.status === 'in_progress' && showLiveProgress"
            class="shrink-0 rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-600 dark:bg-blue-950/60 dark:text-blue-400"
          >
            进行中
          </span>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { ProcessTimelineItem, ProcessTimelineTodoItem } from "@/utils/processTimeline";

const props = defineProps<{
  timeline?: ProcessTimelineItem[];
  /**
   * 归档视图（历史消息的执行过程里）：不提供关闭按钮，也不沿用 banner 的
   * 「关掉后永久隐藏」记忆，否则关一次 banner 会连历史里一起消失。
   */
  archived?: boolean;
}>();

const DISMISSED_STORAGE_KEY = "nanzi_dismissed_todo_fingerprints";
const dismissedTrigger = ref(0);

const todo = computed<ProcessTimelineTodoItem | undefined>(() =>
  [...(props.timeline || [])].reverse().find((item): item is ProcessTimelineTodoItem => item.kind === "todo"),
);

const expanded = ref(true);

const isAllCompleted = computed(() => {
  if (!todo.value || !todo.value.todos.length) return false;
  return todo.value.counts?.completed === todo.value.todos.length;
});

/** 本轮已终结：卡片只做回顾，不再宣称「进行中」。 */
const isSettled = computed(() => todo.value?.settled === true);
const isArchived = computed(() => props.archived === true);
/** 只有「当前仍在跑」的清单才显示旋转图标与进行中徽标。 */
const showLiveProgress = computed(() => !isSettled.value && !isArchived.value);

const settledLabel = computed(() => {
  const status = String(todo.value?.settledStatus || "");
  if (status === "success") return "已完成";
  if (status === "cancelled" || status === "interrupted") return "已中断";
  if (status === "error" || status === "failed") return "已失败";
  if (status === "rejected") return "已拒绝";
  return "已结束";
});

// 全部完成、本轮已终结或归档视图一律折叠为单行
watch(
  [isAllCompleted, isSettled, isArchived],
  ([allDone, settled, archived]) => {
    if (allDone || settled || archived) {
      expanded.value = false;
    }
  },
  { immediate: true },
);

function computeTodoFingerprint(item?: ProcessTimelineTodoItem): string {
  if (!item) return "";
  return `${item.id || ""}:${item.title || ""}:${item.todos.map((t) => t.content).join("||")}`;
}

const currentFingerprint = computed(() => computeTodoFingerprint(todo.value));

function getDismissedFingerprints(): Set<string> {
  try {
    const raw = localStorage.getItem(DISMISSED_STORAGE_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
}

function saveDismissedFingerprint(fingerprint: string): void {
  try {
    const set = getDismissedFingerprints();
    set.add(fingerprint);
    const arr = Array.from(set).slice(-50);
    localStorage.setItem(DISMISSED_STORAGE_KEY, JSON.stringify(arr));
  } catch {}
}

const isDismissed = computed(() => {
  void dismissedTrigger.value;
  if (isArchived.value || !currentFingerprint.value) return false;
  const set = getDismissedFingerprints();
  return set.has(currentFingerprint.value);
});

const statusSummary = computed(() => {
  if (!todo.value || !todo.value.todos.length) return "";
  const { completed = 0, in_progress = 0, pending = 0 } = todo.value.counts || {};
  const total = todo.value.todos.length;

  if (completed === total) {
    return `${completed} 已完成`;
  }

  // 已终结但没有全部完成：说清「还剩几项没做」，别再谎称进行中。
  if (isSettled.value) {
    return `还有 ${total - completed} 项未完成`;
  }

  // 归档的旧快照没有终态标记：同样不能声称「进行中」。
  if (isArchived.value) {
    return `${total - completed} 项未完成`;
  }

  const parts: string[] = [];
  if (in_progress > 0) parts.push(`${in_progress} 进行中`);
  if (pending > 0) parts.push(`${pending} 待处理`);
  if (completed > 0) parts.push(`${completed} 已完成`);

  return parts.join(" · ") || `${completed}/${total} 已完成`;
});

function toggleExpanded(): void {
  expanded.value = !expanded.value;
}

function closeCard(): void {
  if (currentFingerprint.value) {
    saveDismissedFingerprint(currentFingerprint.value);
    dismissedTrigger.value += 1;
  }
}
</script>
