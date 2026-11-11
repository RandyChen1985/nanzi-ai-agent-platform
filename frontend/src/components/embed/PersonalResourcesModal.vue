<script setup lang="ts">
import { defineAsyncComponent, type AsyncComponentLoader } from 'vue'
import {
  PERSONAL_RESOURCE_MODAL_TABS,
  type PersonalResourceTab,
} from '@/constants/personalResources'
import PersonalResourcePanelLoader from '@/components/embed/PersonalResourcePanelLoader.vue'

const props = defineProps<{
  visible: boolean
  activeTab: PersonalResourceTab
}>()

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void
  (e: 'update:activeTab', val: PersonalResourceTab): void
  (e: 'open-report', payload: unknown): void
  (e: 'open-conversation', payload: unknown): void
  (e: 'open-question', payload: unknown): void
}>()

/**
 * 各 Tab 面板按需懒加载：delay 设为 0 让占位立刻出现（默认 200ms 会先白一下再跳），
 * 占位本身撑满内容区，所以加载期间弹窗尺寸不会变化。
 */
const lazyPanel = (loader: AsyncComponentLoader) =>
  defineAsyncComponent({
    loader,
    loadingComponent: PersonalResourcePanelLoader,
    delay: 0,
    suspensible: false,
  })

const PersonalMemoryPanel = lazyPanel(() => import('@/components/personal/PersonalMemoryPanel.vue'))
const PersonalTokenUsage = lazyPanel(() => import('@/components/personal/PersonalTokenUsage.vue'))
const DataPortalHome = lazyPanel(() => import('@/views/DataPortalHome.vue'))
const SkillsManagement = lazyPanel(() => import('@/views/SkillsManagement.vue'))
const McpManagement = lazyPanel(() => import('@/views/McpManagement.vue'))
const TaskCenter = lazyPanel(() => import('@/views/TaskCenter.vue'))

const close = () => emit('update:visible', false)

const setTab = (tab: PersonalResourceTab) => {
  if (props.activeTab === tab) return
  emit('update:activeTab', tab)
}
</script>

<template>
  <div
    v-if="visible"
    class="absolute inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
    @click.self="close"
  >
    <div
      class="bg-white/95 dark:bg-gray-800/95 backdrop-blur-md rounded-2xl shadow-2xl border border-gray-200/80 dark:border-gray-700/80 transform transition-all scale-100 animate-fade-in-up flex flex-col overflow-hidden w-[min(920px,96vw)] h-[min(820px,88vh)] max-w-full max-h-full"
    >
      <!-- Header -->
      <div class="flex justify-between items-center px-5 sm:px-6 pt-5 pb-3 flex-shrink-0">
        <h3 class="text-sm font-black text-gray-800 dark:text-gray-100 uppercase tracking-widest">
          我的资源
        </h3>
        <button
          type="button"
          class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
          aria-label="关闭"
          @click="close"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>

      <!-- Tabs -->
      <div
        class="flex flex-shrink-0 gap-1 overflow-x-auto px-5 sm:px-6 pb-3 border-b border-gray-100 dark:border-gray-700/60"
      >
        <button
          v-for="spec in PERSONAL_RESOURCE_MODAL_TABS"
          :key="spec.tab"
          type="button"
          class="whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-bold transition-colors"
          :class="
            activeTab === spec.tab
              ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/40 dark:text-blue-200'
              : 'text-gray-500 hover:bg-gray-50 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-gray-700/50 dark:hover:text-gray-200'
          "
          @click="setTab(spec.tab)"
        >
          {{ spec.label }}
        </button>
      </div>

      <!-- Content：高度由弹窗固定，切换 Tab 时旧内容淡出、新内容淡入，不会牵动弹窗尺寸 -->
      <div class="flex-1 min-h-0 overflow-hidden">
        <div
          :key="activeTab"
          class="panel-switch h-full overflow-y-auto px-4 sm:px-5 py-4 custom-scrollbar"
        >
          <PersonalMemoryPanel v-if="activeTab === 'memory'" />
          <PersonalTokenUsage v-else-if="activeTab === 'tokens'" />
          <DataPortalHome
            v-else-if="activeTab === 'data'"
            embedded
            delegate-navigation
            @open-report="emit('open-report', $event)"
            @open-conversation="emit('open-conversation', $event)"
            @open-question="emit('open-question', $event)"
          />
          <SkillsManagement v-else-if="activeTab === 'skills'" personal-only />
          <McpManagement v-else-if="activeTab === 'mcp'" personal-only />
          <TaskCenter
            v-else-if="activeTab === 'tasks'"
            personal-only
            embedded
            @open-report="emit('open-report', $event)"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 弹窗入场：原先模板里就写了 animate-fade-in-up，但全局没有这个 keyframes，实际不生效 */
.animate-fade-in-up {
  animation: personal-resource-fade-in-up 0.16s ease-out both;
}

@keyframes personal-resource-fade-in-up {
  from {
    opacity: 0;
    transform: translateY(6px) scale(0.99);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

/* Tab 内容切换：只做透明度/位移，不参与布局，固定高度下不会有尺寸抖动 */
.panel-switch {
  animation: personal-resource-panel-in 0.18s ease-out both;
}

@keyframes personal-resource-panel-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .animate-fade-in-up,
  .panel-switch {
    animation: none;
  }
}
</style>
