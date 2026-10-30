<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue';
import { agentOwnAvatarUrl } from '@/utils/agentAvatar';
import WorkspaceFilePicker from '@/components/embed/WorkspaceFilePicker.vue';

interface AgentOption {
  id: string;
  name: string;
  display_name: string;
  avatar_url?: string;
  description?: string;
  is_system?: boolean;
  /** 已发布版本里 get_dataset_schema 绑定的数据集数量，0/空表示不限定 */
  metadata_dataset_count?: number | null;
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
    /** 每次打开时停留的 tab；没有可切换专家时由父组件传 "file" */
    initialTab?: "expert" | "file";
    /** 已挂到输入框的文件路径，用于置灰 */
    mountedFilePaths?: string[];
    /** 链接锁定了指定智能体时，专家 tab 只显示提示 */
    expertLocked?: boolean;
  }>(),
  {
    routingMode: 'auto',
    expertAgentId: '',
    initialTab: "expert",
    mountedFilePaths: () => [],
    expertLocked: false,
  },
);

const emit = defineEmits<{
  (e: 'select', agent: AgentOption): void;
  (e: 'select-auto'): void;
  (e: 'close'): void;
  (e: 'select-file', payload: {
    type: 'local_file';
    path: string;
    name: string;
    size: number;
    ext: string;
  }): void;
  (e: 'open-workspace'): void;
  (e: 'tab-change', tab: "expert" | "file"): void;
}>();

const activeTab = ref<"expert" | "file">(props.initialTab);
const pickerRef = ref<any>(null);

/** 浮层标题与 aria-label 跟随当前 tab，文件 tab 下不能仍写「选择专家」 */
const panelTitle = computed(() => (activeTab.value === 'file' ? '选择文件' : '选择专家'));

const onPickerSwitchTab = (direction: "left" | "right") => {
  if (direction === "left") activeTab.value = "expert";
};

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      activeTab.value = props.initialTab;
    }
  },
);

watch(activeTab, (tab) => {
  // 切到文件 tab 把焦点交给搜索框；切回专家 tab 由父组件把焦点还给输入框
  if (tab === "file") nextTick(() => pickerRef.value?.focusSearch?.());
  emit("tab-change", tab);
});

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
  if (!props.visible) return false;
  // 左右切 tab 优先于列表导航：专家 → 文件，文件 ← 专家（文件已是最右，→ 不处理）
  if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    const force = e.metaKey || e.ctrlKey || e.altKey;
    if (activeTab.value === "file") {
      if (e.key === "ArrowRight") return false;
      // 搜索框有字时左右键留给光标，⌘/Ctrl/Alt + ← 可强制切走
      if (!force && pickerRef.value?.canLeaveByArrow === false) return false;
      activeTab.value = "expert";
      e.preventDefault();
      return true;
    }
    if (e.key === "ArrowLeft") return false;
    activeTab.value = "file";
    e.preventDefault();
    return true;
  }
  // 文件 tab 的键盘语义归 picker（搜索框 + 网络结果），这里只做转发
  if (activeTab.value === "file") {
    return pickerRef.value?.handleKeydown?.(e) ?? false;
  }
  if (rows.value.length === 0) return false;

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
    v-if="visible && (activeTab === 'file' || rows.length > 0)"
    class="fixed z-[100] bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden flex flex-col animate-fade-in-up"
    :class="['w-[calc(100vw-2rem)] sm:w-[17.5rem]']"
    :style="{
      top: windowWidth < 640 ? 'auto' : `${position.top}px`,
      bottom: windowWidth < 640 ? '80px' : 'auto',
      left: windowWidth < 640 ? '1rem' : `${position.left}px`,
      maxHeight: windowWidth < 640 ? '40vh' : '18rem',
    }"
    role="listbox"
    :aria-label="panelTitle"
  >
    <div class="px-2.5 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between shrink-0 bg-white dark:bg-gray-800">
      <div class="flex items-center gap-1.5 min-w-0">
        <span class="w-1 h-3.5 bg-primary rounded-full shrink-0" />
        <span class="text-xs font-semibold text-gray-900 dark:text-gray-100 truncate">{{ panelTitle }}</span>
        <div class="flex items-center gap-0.5 p-0.5 rounded-lg bg-gray-100 dark:bg-gray-700/60 shrink-0">
          <button
            type="button"
            class="px-1.5 py-0.5 text-[10px] rounded-md transition-colors"
            :class="activeTab === 'expert'
              ? 'bg-white dark:bg-gray-800 text-primary shadow-sm'
              : 'text-gray-500 dark:text-gray-400'"
            @click="activeTab = 'expert'"
          >专家</button>
          <button
            type="button"
            class="px-1.5 py-0.5 text-[10px] rounded-md transition-colors"
            :class="activeTab === 'file'
              ? 'bg-white dark:bg-gray-800 text-primary shadow-sm'
              : 'text-gray-500 dark:text-gray-400'"
            @click="activeTab = 'file'"
          >文件</button>
        </div>
      </div>
      <span class="text-[9px] text-gray-400 hidden sm:inline shrink-0">
        {{ activeTab === 'expert' ? 'Enter 选择 · Esc 关闭' : 'Esc 关闭' }}
      </span>
    </div>

    <div
      v-show="activeTab === 'expert'"
      ref="listContainer"
      class="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-1.5 space-y-0.5 bg-white dark:bg-gray-800"
    >
      <div
        v-if="expertLocked"
        class="px-2.5 py-6 text-center text-[11px] text-gray-500 dark:text-gray-400 leading-relaxed"
      >当前链接已锁定指定智能体，无法切换其他专家</div>
      <template v-else>
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
            <img v-if="agentOwnAvatarUrl(row.agent)" :src="agentOwnAvatarUrl(row.agent)" class="w-full h-full object-cover" />
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
                <span
                  v-if="row.agent.metadata_dataset_count"
                  class="shrink-0 px-1 py-px text-[8px] font-semibold rounded bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
                  :title="`已绑定 ${row.agent.metadata_dataset_count} 个数据集，查询范围不会超出这些数据集`"
                >{{ row.agent.metadata_dataset_count }} 数据集</span>
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
      </template>
    </div>

    <WorkspaceFilePicker
      v-if="activeTab === 'file'"
      ref="pickerRef"
      :initial-keyword="keyword"
      :mounted-paths="mountedFilePaths"
      @select="(payload) => emit('select-file', payload)"
      @open-workspace="emit('open-workspace')"
      @switch-tab="onPickerSwitchTab"
      @close="emit('close')"
    />

    <div
      v-if="activeTab === 'expert'"
      class="px-2.5 py-1.5 border-t border-gray-200 dark:border-gray-700 text-center shrink-0 bg-gray-50 dark:bg-gray-900/80"
    >
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
