<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue';

interface AgentOption {
  id: string;
  name: string;
  display_name: string;
  avatar_url?: string;
  description?: string;
  is_system?: boolean;
}

type MentionRow =
  | { kind: 'auto' }
  | { kind: 'agent'; agent: AgentOption };

const props = withDefaults(
  defineProps<{
    visible: boolean;
    keyword: string;
    agents: AgentOption[];
    position: { top: number; left: number };
    /** 当前委派模式，用于高亮「智能委派 / 当前专家」 */
    routingMode?: string;
    expertAgentId?: string;
  }>(),
  {
    routingMode: 'auto',
    expertAgentId: '',
  },
);

const emit = defineEmits<{
  (e: 'select', agent: AgentOption): void;
  (e: 'select-auto'): void;
  (e: 'close'): void;
}>();

const selectedIndex = ref(0);
const listContainer = ref<HTMLElement | null>(null);
const windowWidth = ref(window.innerWidth);

const updateWidth = () => { windowWidth.value = window.innerWidth; };
onMounted(() => window.addEventListener('resize', updateWidth));
onUnmounted(() => window.removeEventListener('resize', updateWidth));

const isExpertMode = computed(
  () => props.routingMode === 'expert' && !!props.expertAgentId,
);

const filteredAgents = computed(() => {
  const k = (props.keyword || '').toLowerCase();
  return props.agents.filter(
    (a) =>
      (a.display_name || '').toLowerCase().includes(k) ||
      (a.name || '').toLowerCase().includes(k) ||
      (a.description || '').toLowerCase().includes(k),
  );
});

const showAutoOption = computed(() => {
  const k = (props.keyword || '').trim().toLowerCase();
  if (!k) return true;
  return (
    '智能委派'.includes(k) ||
    '委派'.includes(k) ||
    '全能助手'.includes(k) ||
    '自动'.includes(k) ||
    'auto'.includes(k)
  );
});

const rows = computed<MentionRow[]>(() => {
  const list: MentionRow[] = [];
  if (showAutoOption.value) list.push({ kind: 'auto' });
  for (const agent of filteredAgents.value) {
    list.push({ kind: 'agent', agent });
  }
  return list;
});

watch(
  () => [props.visible, props.keyword, rows.value.length] as const,
  () => {
    selectedIndex.value = 0;
    nextTick(() => {
      if (listContainer.value) {
        listContainer.value.scrollTop = 0;
      }
    });
  },
);

watch(selectedIndex, () => {
  nextTick(() => {
    if (!listContainer.value) return;
    const activeItem = listContainer.value.querySelector(
      `[data-mention-index="${selectedIndex.value}"]`,
    ) as HTMLElement | null;
    if (activeItem && typeof activeItem.scrollIntoView === 'function') {
      activeItem.scrollIntoView({ block: 'nearest' });
    }
  });
});

const handleSelectRow = (row: MentionRow) => {
  if (row.kind === 'auto') {
    emit('select-auto');
    return;
  }
  emit('select', row.agent);
};

const handleKeydown = (e: KeyboardEvent) => {
  if (!props.visible || rows.value.length === 0) return false;

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    selectedIndex.value = (selectedIndex.value + 1) % rows.value.length;
    return true;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    selectedIndex.value = (selectedIndex.value - 1 + rows.value.length) % rows.value.length;
    return true;
  }
  if (e.key === 'Enter' || e.key === 'Tab') {
    e.preventDefault();
    const selected = rows.value[selectedIndex.value];
    if (selected) handleSelectRow(selected);
    return true;
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    emit('close');
    return true;
  }
  return false;
};

defineExpose({ handleKeydown });
</script>

<template>
  <div
    v-if="visible && rows.length > 0"
    class="fixed z-[100] bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden flex flex-col animate-fade-in-up"
    :class="['w-[calc(100vw-2rem)] sm:w-[17.5rem]']"
    :style="{
      top: windowWidth < 640 ? 'auto' : `${position.top}px`,
      bottom: windowWidth < 640 ? '80px' : 'auto',
      left: windowWidth < 640 ? '1rem' : `${position.left}px`,
      maxHeight: windowWidth < 640 ? '40vh' : '18rem',
    }"
    role="listbox"
    aria-label="选择专家"
  >
    <div class="px-2.5 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between shrink-0 bg-white dark:bg-gray-800">
      <div class="flex items-center gap-1.5 min-w-0">
        <span class="w-1 h-3.5 bg-primary rounded-full shrink-0" />
        <span class="text-xs font-semibold text-gray-900 dark:text-gray-100 truncate">选择专家</span>
        <span class="px-1.5 py-0.5 bg-primary/10 text-primary text-[9px] font-bold rounded-md shrink-0">
          {{ filteredAgents.length }} 匹配
        </span>
      </div>
      <span class="text-[9px] text-gray-400 hidden sm:inline shrink-0">Enter 选择 · Esc 关闭</span>
    </div>

    <div ref="listContainer" class="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-1.5 space-y-0.5 bg-white dark:bg-gray-800">
      <template v-for="(row, index) in rows" :key="row.kind === 'auto' ? 'auto' : `${row.agent.id}_${index}`">
        <!-- 智能委派 -->
        <button
          v-if="row.kind === 'auto'"
          type="button"
          :data-mention-index="index"
          class="w-full flex items-start gap-2.5 px-2.5 py-2 rounded-lg cursor-pointer transition-all border text-left"
          :class="index === selectedIndex
            ? 'bg-primary/10 border-primary/30 ring-1 ring-primary/20 shadow-sm dark:bg-primary/20 dark:border-primary/40 dark:ring-primary/30'
            : 'border-transparent hover:bg-gray-50 dark:hover:bg-gray-700/60'"
          @click="handleSelectRow(row)"
        >
          <div
            class="w-8 h-8 mt-0.5 rounded-full flex items-center justify-center border shrink-0 transition-colors"
            :class="index === selectedIndex
              ? 'bg-primary/15 border-primary/40 text-primary dark:bg-primary/25 dark:border-primary/50'
              : 'bg-primary/10 border-primary/15 text-primary'"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between gap-2">
              <span
                class="text-[13px] font-semibold truncate"
                :class="index === selectedIndex || !isExpertMode ? 'text-primary' : 'text-gray-900 dark:text-gray-100'"
              >智能委派</span>
              <div class="flex items-center gap-1.5 shrink-0">
                <span
                  v-if="index === selectedIndex"
                  class="rounded bg-primary/20 px-1 py-0.5 text-[9px] font-bold text-primary dark:bg-primary/30 leading-none"
                >
                  ↵
                </span>
                <svg
                  v-if="!isExpertMode"
                  class="w-3.5 h-3.5 text-primary shrink-0"
                  fill="currentColor"
                  viewBox="0 0 20 20"
                >
                  <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
                </svg>
              </div>
            </div>
            <p class="text-[10px] text-gray-500 dark:text-gray-400 mt-0.5 leading-snug line-clamp-2">由主助手直接处理，或按任务需要自动委派其他专家</p>
          </div>
        </button>

        <div
          v-if="row.kind === 'auto' && filteredAgents.length > 0"
          class="h-px bg-gray-200 dark:bg-gray-700 my-1 mx-1.5"
        />

        <!-- 专家 -->
        <button
          v-else-if="row.kind === 'agent'"
          type="button"
          :data-mention-index="index"
          class="w-full flex items-start gap-2.5 px-2.5 py-2 rounded-lg cursor-pointer transition-all border text-left"
          :class="index === selectedIndex
            ? 'bg-primary/10 border-primary/30 ring-1 ring-primary/20 shadow-sm dark:bg-primary/20 dark:border-primary/40 dark:ring-primary/30'
            : 'border-transparent hover:bg-gray-50 dark:hover:bg-gray-700/60'"
          :title="row.agent.description || row.agent.display_name"
          @click="handleSelectRow(row)"
        >
          <div
            class="w-8 h-8 mt-0.5 rounded-full flex items-center justify-center overflow-hidden border shrink-0 transition-colors"
            :class="index === selectedIndex
              ? 'bg-primary/15 border-primary/40 text-primary dark:bg-primary/25 dark:border-primary/50'
              : 'bg-gray-100 dark:bg-gray-700 border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-300'"
          >
            <img v-if="row.agent.avatar_url" :src="row.agent.avatar_url" class="w-full h-full object-cover" />
            <span v-else class="text-[11px] font-bold">{{ Array.from(row.agent.display_name || 'E')[0] }}</span>
          </div>
          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-1 min-w-0">
                <span
                  class="text-[13px] font-semibold truncate"
                  :class="index === selectedIndex || (isExpertMode && expertAgentId === row.agent.id)
                    ? 'text-primary'
                    : 'text-gray-900 dark:text-gray-100'"
                >{{ row.agent.display_name }}</span>
                <span
                  v-if="row.agent.is_system"
                  class="shrink-0 px-1 py-px text-[8px] font-semibold rounded bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300 uppercase"
                >SYS</span>
              </div>
              <div class="flex items-center gap-1.5 shrink-0">
                <span
                  v-if="index === selectedIndex"
                  class="rounded bg-primary/20 px-1 py-0.5 text-[9px] font-bold text-primary dark:bg-primary/30 leading-none"
                >
                  ↵
                </span>
                <svg
                  v-if="isExpertMode && expertAgentId === row.agent.id"
                  class="w-3.5 h-3.5 text-primary shrink-0"
                  fill="currentColor"
                  viewBox="0 0 20 20"
                >
                  <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
                </svg>
              </div>
            </div>
            <p
              class="text-[10px] text-gray-500 dark:text-gray-400 mt-0.5 leading-snug line-clamp-2"
              :title="row.agent.description || '专属能力专家'"
            >{{ row.agent.description || '专属能力专家' }}</p>
          </div>
        </button>
      </template>
    </div>

    <div class="px-2.5 py-1.5 border-t border-gray-200 dark:border-gray-700 text-center shrink-0 bg-gray-50 dark:bg-gray-900/80">
      <span class="text-[10px] text-gray-500 dark:text-gray-400">共 {{ filteredAgents.length }} 个专家</span>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 4px;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 2px;
}
.animate-fade-in-up {
  animation: fadeInUp 0.2s ease-out;
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
