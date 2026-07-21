<script setup lang="ts">
import { computed } from 'vue';
import { useBranding } from '@/composables/useBranding';
import {
  ChartBarIcon,
  BookOpenIcon,
  CommandLineIcon,
} from '@heroicons/vue/24/outline';

const { branding } = useBranding();

const props = defineProps<{
  welcomeMessage: string;
  slashCommands: any[];
}>();

const emit = defineEmits<{
  (e: 'quick-question', command: string): void;
  (e: 'open-data-portal'): void;
  (e: 'select-knowledge-base'): void;
  (e: 'open-workspace'): void;
}>();

type CapabilityAction = 'data-portal' | 'knowledge-base' | 'workspace';

// 能力卡：Heroicons outline 线性图标，对齐 TDesign 视觉克制
const capabilities: Array<{
  icon: any;
  title: string;
  desc: string;
  action: CapabilityAction;
}> = [
  {
    icon: ChartBarIcon,
    title: '自然语言查数',
    desc: '用中文询问机房 PUE、负载等业务指标。',
    action: 'data-portal',
  },
  {
    icon: BookOpenIcon,
    title: '智能知识检索',
    desc: '查询企业 SOP、运维手册和内部规范文档。',
    action: 'knowledge-base',
  },
  {
    icon: CommandLineIcon,
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

// 动态问候
const greeting = computed(() => {
  const hour = new Date().getHours();
  if (hour < 6) return '凌晨好';
  if (hour < 9) return '早上好';
  if (hour < 12) return '上午好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
});

// 推荐提问：取前 4 条用户指令
const recommendedPrompts = computed(() => {
  return props.slashCommands
    .filter(c => !String(c.id).startsWith('sys_'))
    .slice(0, 4);
});
</script>

<template>
  <div class="max-w-3xl mx-auto px-4 sm:px-6 py-8 sm:py-12 flex flex-col items-center">
    <!-- Header：TDesign 中性无衬线，标题用 brand 色作为唯一锚点 -->
    <div class="text-center mb-10 sm:mb-12 animate-fade-in-up">
      <h1 class="text-2xl sm:text-3xl font-semibold text-gray-900 dark:text-gray-100 mb-3 tracking-tight">
        {{ greeting }}
      </h1>
      <p class="text-gray-500 dark:text-gray-400 text-sm max-w-md mx-auto leading-relaxed">
        {{ welcomeMessage || ('我是您的 ' + (branding.default_agent_name || 'NanZi · AI') + '，准备好帮您处理任何任务了。') }}
      </p>
    </div>

    <!-- Core Capabilities：TDesign secondarycontainer 灰底卡 -->
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-10 w-full animate-fade-in-up delay-100">
      <button
        v-for="cap in capabilities"
        :key="cap.title"
        type="button"
        class="p-4 rounded-td-md bg-tdSecondaryContainer dark:bg-tdSecondaryContainer-dark text-left hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors group cursor-pointer"
        :aria-label="cap.title"
        @click="handleCapabilityClick(cap.action)"
      >
        <component
          :is="cap.icon"
          class="w-5 h-5 text-gray-700 dark:text-gray-200 mb-2 group-hover:text-[var(--primary-color)] transition-colors"
          aria-hidden="true"
        />
        <h3 class="text-sm font-medium text-gray-900 dark:text-gray-100 mb-1">{{ cap.title }}</h3>
        <p class="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">{{ cap.desc }}</p>
      </button>
    </div>

    <!-- Recommended Prompts：TDesign 纯文本列表，无方框 -->
    <div class="w-full animate-fade-in-up delay-200" v-if="recommendedPrompts.length > 0">
      <div class="text-xs text-gray-500 dark:text-gray-400 mb-3 px-1">您可以试着问我</div>
      <ul class="space-y-1">
        <li v-for="(cmd, idx) in recommendedPrompts" :key="cmd.id">
          <button
            type="button"
            @click="emit('quick-question', cmd.command)"
            class="w-full flex items-center gap-3 px-3 py-2.5 rounded-td-md text-left hover:bg-tdSecondaryContainer dark:hover:bg-tdSecondaryContainer-dark transition-colors group"
          >
            <span class="w-5 h-5 flex items-center justify-center text-[11px] font-normal text-gray-400 dark:text-gray-500 shrink-0">
              {{ String(idx + 1).padStart(2, '0') }}
            </span>
            <span class="flex-1 min-w-0 text-sm text-gray-700 dark:text-gray-200 truncate group-hover:text-[var(--primary-color)] transition-colors">
              {{ cmd.label }}
            </span>
            <svg
              class="w-4 h-4 text-gray-300 dark:text-gray-600 opacity-0 group-hover:opacity-100 group-hover:translate-x-0.5 transition-all shrink-0"
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3" />
            </svg>
          </button>
        </li>
      </ul>
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
  animation: fade-in-up 0.4s cubic-bezier(0.38, 0, 0.24, 1) both;
}
.delay-100 { animation-delay: 0.08s; }
.delay-200 { animation-delay: 0.16s; }
</style>
