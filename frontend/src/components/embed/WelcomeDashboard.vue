<script setup lang="ts">
import { computed } from 'vue';
import {
  BookOpenIcon,
  ChartBarIcon,
  ChatBubbleLeftRightIcon,
  ComputerDesktopIcon,
  DocumentTextIcon,
  ExclamationTriangleIcon,
  SparklesIcon,
} from '@heroicons/vue/24/outline';
import { useBranding } from '@/composables/useBranding';
import WorkbenchPersonalResources from '@/components/workbench/WorkbenchPersonalResources.vue';
import { formatWorkbenchRelativeTime } from '@/utils/workbenchDisplay';
import type { WorkbenchPersonalResource } from '@/types/workbench';

const { branding } = useBranding();

type CapabilityAction = 'data-portal' | 'knowledge-base' | 'workspace';
type ActionCardKind = 'configured' | 'capability' | 'recommendation';

interface WelcomeActionCard {
  icon: string;
  title: string;
  subtitle: string;
  prompt: string;
  kind: ActionCardKind;
  action?: CapabilityAction;
  source?: string;
}

interface WelcomeRecommendationCard {
  icon: string;
  title: string;
  subtitle: string;
  prompt: string;
  source?: string;
}

interface WelcomeRecentConversation {
  conversation_id: string;
  title: string;
  subtitle?: string;
  occurred_at?: string;
  agent_id?: string;
}

const props = defineProps<{
  welcomeMessage: string;
  slashCommands: any[];
  welcomeCards?: Array<{ icon: string; title: string; subtitle: string; prompt: string }>;
  personalResources?: WorkbenchPersonalResource[];
  personalResourcesRefreshing?: boolean;
  recentConversations?: WelcomeRecentConversation[];
  recommendations?: WelcomeRecommendationCard[];
  recommendationsLoading?: boolean;
  recommendationSource?: string;
  recommendationsError?: string;
}>();

const emit = defineEmits<{
  (e: 'quick-question', command: string): void;
  (e: 'fill-question', prompt: string): void;
  (e: 'open-data-portal'): void;
  (e: 'select-knowledge-base'): void;
  (e: 'open-workspace'): void;
  (e: 'open-personal-resources', tab: string): void;
  (e: 'refresh-personal-resources'): void;
  (e: 'generate-recommendations'): void;
  (e: 'open-recent-conversation', conversationId: string): void;
}>();

const capabilities: Array<{
  icon: string;
  title: string;
  desc: string;
  action: CapabilityAction;
}> = [
  {
    icon: 'chart',
    title: '查一个数',
    desc: '用中文问清机房 PUE、负载与能耗趋势。',
    action: 'data-portal',
  },
  {
    icon: 'knowledge',
    title: '找一份资料',
    desc: '从 SOP、手册和内部规范里找答案。',
    action: 'knowledge-base',
  },
  {
    icon: 'workspace',
    title: '整理工作文件',
    desc: '浏览、上传并整理 AI 工作目录。',
    action: 'workspace',
  },
];

const handleCapabilityClick = (action?: CapabilityAction) => {
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

const greeting = computed(() => {
  const hour = new Date().getHours();
  if (hour < 6) return '凌晨好';
  if (hour < 9) return '早上好';
  if (hour < 12) return '上午好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
});

const actionCards = computed<WelcomeActionCard[]>(() => {
  if (props.recommendations?.length === 3) {
    return props.recommendations.slice(0, 3).map((card) => ({
      ...card,
      kind: 'recommendation' as const,
    }));
  }
  if (props.welcomeCards?.length === 3) {
    return props.welcomeCards.slice(0, 3).map((card) => ({
      ...card,
      kind: 'configured' as const,
    }));
  }
  return capabilities.map((capability) => ({
    icon: capability.icon,
    title: capability.title,
    subtitle: capability.desc,
    prompt: '',
    kind: 'capability' as const,
    action: capability.action,
  }));
});

const welcomeCardSetKey = computed(() => {
  const set = actionCards.value
    .map((card) => [card.kind, card.icon, card.title, card.subtitle, card.prompt, card.source || ''].join(':'))
    .join('|');
  return `${props.recommendationsLoading ? 'loading' : 'ready'}:${set}`;
});

const actionCardKicker = (card: WelcomeActionCard) => {
  if (card.kind === 'recommendation') return '为你推荐';
  if (card.kind === 'configured') return '推荐问题';
  if (card.action === 'data-portal') return '数据门户';
  if (card.action === 'knowledge-base') return '知识库';
  return '工作空间';
};

const actionCardFooter = (card: WelcomeActionCard) => {
  if (card.kind === 'recommendation') return card.source || '综合最近会话';
  if (card.kind === 'configured') return '点击立即提问';
  if (card.action === 'data-portal') return '打开数据门户';
  if (card.action === 'knowledge-base') return '打开知识库';
  return '打开工作空间';
};

const handleActionCard = (card: WelcomeActionCard) => {
  if (card.kind === 'recommendation') {
    emit('fill-question', card.prompt);
    return;
  }
  handleCapabilityClick(card.action);
};

const relativeTime = (value?: string) => formatWorkbenchRelativeTime(value || '');

const recommendationButtonLabel = computed(() => {
  if (props.recommendationsLoading) return '正在读会话…';
  if (props.recommendations?.length) return '换一批';
  return '生成推荐';
});
</script>

<template>
  <div class="w-full mx-auto max-w-[1080px] px-4 sm:px-6 py-6 sm:py-8 flex flex-col items-stretch">
    <!-- Header：先回答“现在能做什么”，家底放在行动之后 -->
    <header class="mb-6 sm:mb-7 animate-fade-in-up">
      <p class="text-[10px] font-bold uppercase tracking-[0.16em] text-gray-300">对话首页</p>
      <h1 class="mt-1.5 text-3xl sm:text-[34px] font-black tracking-[-0.045em] text-gray-900 dark:text-gray-100">
        {{ greeting }}
      </h1>
      <p class="mt-2 max-w-xl text-sm leading-relaxed text-gray-500 dark:text-gray-400">
        {{ welcomeMessage || ('我是您的' + (branding.default_agent_name || 'NanZi · AI') + '，准备好帮您处理任何任务了。') }}
      </p>

      <div v-if="recentConversations?.length" class="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span class="text-[10px] font-bold uppercase tracking-widest text-gray-300">继续</span>
        <button
          v-for="conversation in recentConversations.slice(0, 1)"
          :key="conversation.conversation_id"
          type="button"
          class="min-w-0 text-left text-[10.5px] text-gray-500 transition-colors hover:text-primary dark:text-gray-400"
          @click="emit('open-recent-conversation', conversation.conversation_id)"
        >
          <span class="font-medium text-gray-700 dark:text-gray-300">{{ conversation.title }}</span>
          <span class="ml-1 text-gray-300 dark:text-gray-500">{{ relativeTime(conversation.occurred_at) }}</span>
        </button>
      </div>
    </header>

    <!-- 开始一件事：默认入口卡 + 基于近 3 天会话的生成推荐 -->
    <section class="w-full animate-fade-in-up delay-100">
      <div class="mb-3 flex min-w-0 items-center gap-2 px-1">
        <p class="shrink-0 text-[10px] font-black uppercase tracking-widest text-gray-300">开始一件事</p>
        <span class="hidden text-[10px] text-gray-300 sm:inline">点卡片开始</span>
        <span class="flex-1"></span>
        <span
          v-if="recommendationsLoading"
          class="hidden items-center gap-1 text-[10px] text-gray-400 sm:inline-flex"
        >正在读取近 3 天会话</span>
        <span
          v-else-if="recommendationSource"
          class="hidden items-center gap-1 text-[10px] text-gray-400 sm:inline-flex"
        >{{ recommendationSource }}</span>
        <span
          v-else
          class="hidden items-center gap-1 text-[10px] text-gray-300 sm:inline-flex"
        >基于近 3 天会话</span>
        <button
          type="button"
          class="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-[10px] font-medium text-gray-300 transition-colors hover:bg-gray-100 hover:text-gray-500 disabled:cursor-default disabled:opacity-50 dark:hover:bg-gray-800"
          :disabled="recommendationsLoading"
          :aria-busy="recommendationsLoading"
          title="只读取最近 3 天的会话标题与摘要，生成 3 个可执行的起点"
          @click="emit('generate-recommendations')"
        >
          <SparklesIcon class="h-3 w-3" :class="{ 'animate-spin': recommendationsLoading }" aria-hidden="true" />
          <span>{{ recommendationButtonLabel }}</span>
        </button>
      </div>

      <Transition name="welcome-card-set" mode="out-in">
        <div :key="welcomeCardSetKey" class="w-full">
          <div
            data-tour="welcome-cards"
            aria-label="快捷入口"
            class="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4"
          >
            <template v-if="recommendationsLoading">
              <div
                v-for="index in 3"
                :key="`recommendation-skeleton-${index}`"
                class="flex min-h-[132px] flex-col rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-700/70 dark:bg-gray-800/50"
              >
                <div class="flex items-start gap-3">
                  <span class="h-10 w-10 shrink-0 animate-pulse rounded-xl bg-gray-100 dark:bg-gray-700/60"></span>
                  <span class="mt-1 h-3 w-16 animate-pulse rounded bg-gray-100 dark:bg-gray-700/60"></span>
                </div>
                <span class="mt-4 h-4 w-3/4 animate-pulse rounded bg-gray-100 dark:bg-gray-700/60"></span>
                <span class="mt-2 h-3 w-11/12 animate-pulse rounded bg-gray-50 dark:bg-gray-800"></span>
              </div>
            </template>
            <template v-else>
              <button
                v-for="(card, index) in actionCards"
                :key="`${card.kind}-${index}-${card.title}`"
                type="button"
                :aria-label="card.title"
                :style="{ '--welcome-card-index': index }"
                class="welcome-card-item group flex min-h-[132px] flex-col rounded-2xl border border-gray-100 bg-white p-4 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:bg-blue-50/30 hover:shadow-md dark:border-gray-700 dark:bg-gray-800/50 dark:hover:bg-blue-900/10 lg:p-5"
                @click="card.kind === 'configured' ? emit('quick-question', card.prompt) : handleActionCard(card)"
              >
                <div class="flex items-start gap-3">
                  <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary transition-transform group-hover:scale-110">
                    <component :is="welcomeCardIcon(card.icon)" class="h-5 w-5" aria-hidden="true" />
                  </span>
                  <div class="min-w-0 flex-1">
                    <p class="text-[9.5px] font-bold uppercase tracking-[0.1em] text-gray-300">{{ actionCardKicker(card) }}</p>
                    <h3 class="mt-0.5 text-[15px] font-bold leading-snug text-gray-800 transition-colors group-hover:text-primary dark:text-gray-200">
                      {{ card.title }}
                    </h3>
                    <p class="mt-1 text-[11.5px] leading-relaxed text-gray-400">{{ card.subtitle }}</p>
                  </div>
                </div>
                <div class="mt-auto flex min-w-0 items-center gap-2 pt-3">
                  <span class="min-w-0 truncate text-[10px] text-gray-400">{{ actionCardFooter(card) }}</span>
                  <span class="ml-auto text-sm text-gray-300 transition-all group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-primary">↗</span>
                </div>
              </button>
            </template>
          </div>
          <p v-if="recommendationsError" class="mt-2 px-1 text-[10.5px] text-amber-500">{{ recommendationsError }}</p>
        </div>
      </Transition>
    </section>

    <!-- 我的资源：压成一条可扫读的状态条，放在行动之后 -->
    <section v-if="personalResources?.length" class="mt-6 w-full animate-fade-in-up delay-200">
      <div class="mb-2.5 flex items-center gap-1.5 px-1">
        <p class="text-[10px] font-black uppercase tracking-widest text-gray-300">我的资源</p>
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
    </section>
  </div>
</template>

<style scoped>
@keyframes fade-in-up {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
.animate-fade-in-up {
  animation: fade-in-up 0.55s cubic-bezier(0.16, 1, 0.3, 1) both;
}
.delay-100 { animation-delay: 0.08s; }
.delay-200 { animation-delay: 0.16s; }

.welcome-card-set-enter-active {
  transition: opacity 240ms cubic-bezier(0.16, 1, 0.3, 1), transform 240ms cubic-bezier(0.16, 1, 0.3, 1);
}
.welcome-card-set-leave-active {
  transition: opacity 120ms ease-out, transform 120ms ease-out;
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
  animation-delay: calc(25ms + var(--welcome-card-index) * 40ms);
}
@media (prefers-reduced-motion: reduce) {
  .welcome-card-set-enter-active,
  .welcome-card-set-leave-active {
    transition-duration: 1ms;
  }
  .welcome-card-item,
  .animate-fade-in-up {
    animation: none;
  }
}
</style>
