<script setup lang="ts">
import { computed } from 'vue';
import {
  BookOpenIcon,
  ChartBarIcon,
  ChatBubbleLeftRightIcon,
  ComputerDesktopIcon,
  DocumentTextIcon,
  ExclamationTriangleIcon,
} from '@heroicons/vue/24/outline';
import { useBranding } from '@/composables/useBranding';
import WorkbenchPersonalResources from '@/components/workbench/WorkbenchPersonalResources.vue';
import type { WorkbenchPersonalResource } from '@/types/workbench';

const { branding } = useBranding();

const props = defineProps<{
  welcomeMessage: string;
  slashCommands: any[];
  welcomeCards?: Array<{ icon: string; title: string; subtitle: string; prompt: string }>;
  personalResources?: WorkbenchPersonalResource[];
  personalResourcesRefreshing?: boolean;
}>();

const emit = defineEmits<{
  (e: 'quick-question', command: string): void;
  (e: 'open-data-portal'): void;
  (e: 'select-knowledge-base'): void;
  (e: 'open-workspace'): void;
  (e: 'open-personal-resources', tab: string): void;
  (e: 'refresh-personal-resources'): void;
}>();

type CapabilityAction = 'data-portal' | 'knowledge-base' | 'workspace';

// 2. Capabilities (Static highlights for the platform)
const capabilities: Array<{
  icon: string;
  title: string;
  desc: string;
  action: CapabilityAction;
}> = [
  {
    icon: 'chart',
    title: '自然语言查数',
    desc: '用中文询问机房 PUE、负载等业务指标。',
    action: 'data-portal',
  },
  {
    icon: 'knowledge',
    title: '智能知识检索',
    desc: '查询企业 SOP、运维手册和内部规范文档。',
    action: 'knowledge-base',
  },
  {
    icon: 'workspace',
    title: '管理工作空间',
    desc: '浏览、上传和整理您的 AI 工作目录文件。',
    action: 'workspace',
  },
];

const handleCapabilityClick = (action: CapabilityAction) => {
  if (action === 'data-portal') {
    emit('open-data-portal');
    return;
  }
  if (action === 'knowledge-base') {
    emit('select-knowledge-base');
    return;
  }
  emit('open-workspace');
};

const welcomeCardIcon = (icon: string) => ({
  chart: ChartBarIcon,
  knowledge: BookOpenIcon,
  workspace: ComputerDesktopIcon,
  report: DocumentTextIcon,
  alert: ExclamationTriangleIcon,
  chat: ChatBubbleLeftRightIcon,
}[icon] || ChatBubbleLeftRightIcon);

const welcomeCardSetKey = computed(() => {
  if (props.welcomeCards?.length === 3) {
    return `configured:${props.welcomeCards.map(card => [card.icon, card.title, card.subtitle, card.prompt].join(':')).join('|')}`;
  }
  return `fallback:${capabilities.map(capability => capability.title).join('|')}`;
});

// 1. Dynamic Greeting
const greeting = computed(() => {
  const hour = new Date().getHours();
  if (hour < 6) return '凌晨好';
  if (hour < 9) return '早上好';
  if (hour < 12) return '上午好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
});

// 3. Recommended Prompts (Pick first 4 from user commands)
const recommendedPrompts = computed(() => {
  // Filter out system commands and take top 4
  return props.slashCommands
    .filter(c => !String(c.id).startsWith('sys_'))
    .slice(0, 4);
});
</script>

<template>
  <div class="w-full mx-auto px-4 sm:px-6 py-8 sm:py-12 flex flex-col items-center">
    <!-- Header Section -->
    <div class="text-center mb-8 sm:mb-12 animate-fade-in-up max-w-3xl w-full">
      <h1 class="text-3xl font-black text-gray-900 dark:text-gray-100 mb-3 tracking-tight">
        {{ greeting }}！
      </h1>
      <p class="text-gray-500 dark:text-gray-400 text-sm max-w-md mx-auto leading-relaxed">
        {{ welcomeMessage || ('我是您的' + (branding.default_agent_name || 'NanZi · AI') + '，准备好帮您处理任何任务了。') }}
      </p>
    </div>

    <!-- 资源条单独放宽，避免 max-w-3xl 下 5 列被挤扁 -->
    <div v-if="personalResources?.length" class="w-full max-w-3xl mb-8 sm:mb-10">
      <div class="mb-3 flex items-center gap-1.5 px-1">
        <p class="text-[10px] font-black text-gray-300 uppercase tracking-widest">我的资源</p>
        <button
          type="button"
          class="inline-flex h-5 w-5 items-center justify-center rounded-full text-gray-300 transition-colors hover:bg-gray-100 hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-gray-800"
          :disabled="personalResourcesRefreshing"
          :aria-busy="personalResourcesRefreshing"
          :aria-label="personalResourcesRefreshing ? '刷新中' : '刷新我的资源'"
          title="刷新我的资源"
          @click="emit('refresh-personal-resources')"
        >
          <svg
            class="h-3 w-3"
            :class="{ 'animate-spin': personalResourcesRefreshing }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
        </button>
      </div>
      <WorkbenchPersonalResources
        compact
        :items="personalResources"
        @select="(item) => emit('open-personal-resources', item.tab)"
      />
    </div>

    <!-- Version-configured cards replace fixed capabilities only when enabled. -->
    <Transition name="welcome-card-set" mode="out-in">
      <div :key="welcomeCardSetKey" class="w-full max-w-3xl mb-12">
        <div class="mb-3 px-1">
          <p class="text-[10px] font-black text-gray-300 uppercase tracking-widest">快捷入口</p>
        </div>
        <div data-tour="welcome-cards" class="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <template v-if="welcomeCards?.length === 3">
          <button
            v-for="(card, index) in welcomeCards"
            :key="card.prompt"
            type="button"
            :aria-label="card.title"
            :style="{ '--welcome-card-index': index }"
            class="welcome-card-item p-4 rounded-2xl bg-white dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700 shadow-sm hover:shadow-md hover:border-primary/40 hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-all group text-left cursor-pointer"
            @click="emit('quick-question', card.prompt)"
          >
            <div class="mb-2 text-primary group-hover:scale-110 transition-transform"><component :is="welcomeCardIcon(card.icon)" class="h-7 w-7" aria-hidden="true" /></div>
            <h3 class="text-xs font-bold text-gray-800 dark:text-gray-200 mb-1 group-hover:text-primary transition-colors">{{ card.title }}</h3>
            <p class="text-[10px] text-gray-400 leading-normal">{{ card.subtitle }}</p>
          </button>
        </template>
        <template v-else>
          <button
            v-for="(cap, index) in capabilities"
            :key="cap.title"
            type="button"
            :style="{ '--welcome-card-index': index }"
            class="welcome-card-item p-4 rounded-2xl bg-white dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700 shadow-sm hover:shadow-md hover:border-primary/40 hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-all group text-left cursor-pointer"
            :aria-label="cap.title"
            @click="handleCapabilityClick(cap.action)"
          >
            <div class="mb-2 text-primary group-hover:scale-110 transition-transform"><component :is="welcomeCardIcon(cap.icon)" class="h-7 w-7" aria-hidden="true" /></div>
            <h3 class="text-xs font-bold text-gray-800 dark:text-gray-200 mb-1 group-hover:text-primary transition-colors">{{ cap.title }}</h3>
            <p class="text-[10px] text-gray-400 leading-normal">{{ cap.desc }}</p>
          </button>
        </template>
        </div>
      </div>
    </Transition>

    <!-- Recommended Prompts (Actionable Grid) -->
    <div class="w-full max-w-3xl animate-fade-in-up delay-200" v-if="recommendedPrompts.length > 0">
      <div class="flex items-center space-x-2 mb-4 px-1">
        <span class="h-px flex-1 bg-gray-100 dark:bg-gray-800"></span>
        <span class="text-[10px] font-black text-gray-300 uppercase tracking-widest">您可以试着问我</span>
        <span class="h-px flex-1 bg-gray-100 dark:bg-gray-800"></span>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <button 
          v-for="cmd in recommendedPrompts" 
          :key="cmd.id"
          @click="emit('quick-question', cmd.command)"
          class="flex items-center space-x-3 p-4 bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-2xl text-left hover:border-primary/50 hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-all group"
        >
          <div class="w-8 h-8 rounded-xl bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center text-primary group-hover:bg-primary group-hover:text-white transition-colors">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
          </div>
          <div class="flex-1 min-w-0">
            <div class="text-xs font-bold text-gray-800 dark:text-gray-200 truncate">{{ cmd.label }}</div>
            <div class="text-[10px] text-gray-400 truncate">{{ cmd.command }}</div>
          </div>
          <svg class="w-3.5 h-3.5 text-gray-300 opacity-0 group-hover:opacity-100 group-hover:translate-x-1 transition-all" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3" /></svg>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
@keyframes fade-in-up {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
.animate-fade-in-up {
  animation: fade-in-up 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
}
.delay-100 { animation-delay: 0.1s; }
.delay-200 { animation-delay: 0.2s; }

.welcome-card-set-enter-active {
  transition: opacity 260ms cubic-bezier(0.16, 1, 0.3, 1), transform 260ms cubic-bezier(0.16, 1, 0.3, 1);
}
.welcome-card-set-leave-active {
  transition: opacity 140ms ease-out, transform 140ms ease-out;
}
.welcome-card-set-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.welcome-card-set-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
@keyframes welcome-card-reveal {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
.welcome-card-item {
  animation: welcome-card-reveal 260ms cubic-bezier(0.16, 1, 0.3, 1) both;
  animation-delay: calc(35ms + var(--welcome-card-index) * 45ms);
}
@media (prefers-reduced-motion: reduce) {
  .welcome-card-set-enter-active,
  .welcome-card-set-leave-active {
    transition-duration: 1ms;
  }
  .welcome-card-item {
    animation: none;
  }
}
</style>
