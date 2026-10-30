<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { AgentType } from '../../api/agent';
import {
  INJECTION_GROUPS,
  PROMPT_STACK,
  STACK_FOOTNOTE,
  type InjectionGroup,
  type InjectionPillKind,
} from '../../constants/promptInjection';

/**
 * 提示词编辑器的「平台还会注入什么」只读说明。
 *
 * 设计前提：注入是平台行为，作者不参与配置。因此这里没有任何可插入的 token、
 * 没有「插入」按钮，只解释运行时会发生什么，避免把内部实现暴露成用户负担。
 */
const props = withDefaults(
  defineProps<{
    agentType?: AgentType;
    /** 用于判断是否具备数据查询能力（通用智能体也可能走数据查询轮次） */
    capabilities?: string[];
    engineType?: string | null;
    /** 记住每个智能体自己的展开状态 */
    storageKey?: string;
  }>(),
  {
    agentType: 'GENERAL',
    capabilities: () => [],
    engineType: 'LOCAL',
    storageKey: 'default',
  },
);

const tabs = [
  { key: 'sections' as const, label: '默认注入的内容' },
  { key: 'order' as const, label: '拼装顺序与缓存边界' },
];

const activeTab = ref<'sections' | 'order'>('sections');
const openGroup = ref<string>('platform');
const expanded = ref(false);

const isLocal = computed(() => props.engineType !== 'RAGFLOW' && props.engineType !== 'OPENCLAW');

const storageKey = computed(() => `nanzi:prompt-injection-guide:${props.storageKey}`);

const readExpanded = () => {
  try {
    expanded.value = window.localStorage.getItem(storageKey.value) === '1';
  } catch {
    expanded.value = false;
  }
};

readExpanded();
watch(storageKey, readExpanded);

watch(expanded, (value) => {
  try {
    window.localStorage.setItem(storageKey.value, value ? '1' : '0');
  } catch {
    /* 隐私模式下不可写，忽略 */
  }
});

/** 数据查询能力可能来自能力标签，而不只是 agent_type */
const effectiveTypes = computed<AgentType[]>(() => {
  const types = new Set<AgentType>([props.agentType]);
  if ((props.capabilities || []).includes('data_query')) types.add('CHATBI');
  return Array.from(types);
});

const visibleGroups = computed<InjectionGroup[]>(() =>
  INJECTION_GROUPS.map((group) => {
    const items = group.items.filter(
      (item) => !item.types || item.types.some((type) => effectiveTypes.value.includes(type)),
    );
    // 摘要按实际可见条目生成，避免 ChatBI 面板里出现「知识库检索结果」这种不适用的描述
    const summary =
      items.length === group.items.length
        ? group.summary
        : items.map((item) => item.name.replace(/[[\]<>【】]/g, '')).join(' / ');
    return { ...group, items, summary };
  }).filter((group) => group.items.length > 0),
);

const stackRows = computed(() => {
  let index = 0;
  return PROMPT_STACK.filter(
    (item) => !item.types || item.types.some((type) => effectiveTypes.value.includes(type)),
  ).map((item) => {
    if (item.tag === 'boundary') return { ...item, idx: 0 };
    index += 1;
    return { ...item, idx: index };
  });
});

const toggleGroup = (key: string) => {
  openGroup.value = openGroup.value === key ? '' : key;
};

const pillClass = (kind: InjectionPillKind) =>
  kind === 'always'
    ? 'border-blue-100 bg-blue-50 text-primary'
    : 'border-amber-100 bg-amber-50 text-amber-700';

/** 说明文案是本地常量，先转义再放行反引号与加粗，避免把 `<USER_PROFILE>` 当标签解析 */
const escapeHtml = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const renderDesc = (text: string) =>
  escapeHtml(text)
    .replace(
      /`([^`]+)`/g,
      '<code class="rounded bg-gray-100 px-1 py-0.5 font-mono text-[10px] text-gray-700">$1</code>',
    )
    .replace(/\*\*([^*]+)\*\*/g, '<b class="font-semibold text-gray-700">$1</b>');
</script>

<template>
  <div v-if="isLocal" class="overflow-hidden rounded-xl border border-gray-200 bg-white">
    <button
      type="button"
      class="flex w-full items-start gap-2.5 px-3.5 py-2.5 text-left transition-colors hover:bg-gray-50/70"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span
        class="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
      >
        <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.4">
          <circle cx="12" cy="12" r="9" />
          <path stroke-linecap="round" d="M12 16v-4.5m0-3v.5" />
        </svg>
      </span>

      <span class="min-w-0 flex-1">
        <span class="flex flex-wrap items-center gap-1.5">
          <span class="text-[13px] font-semibold leading-5 text-gray-800">
            这段提示词之外，平台还会注入什么？
          </span>
          <span class="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10px] text-gray-500">
            只读参考
          </span>
        </span>
        <span class="mt-0.5 block text-[11px] leading-4 text-gray-500">
          只看不写：系统级内容由平台自动拼在你的内容前后，这里仅作说明
        </span>
      </span>

      <svg
        class="mt-1 h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform"
        :class="expanded ? 'rotate-180' : ''"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        stroke-width="2.2"
      >
        <path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6" />
      </svg>
    </button>

    <div v-show="expanded" class="border-t border-gray-100">
      <div class="flex items-center gap-0.5 px-3.5 pt-1.5">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          class="relative px-2.5 py-1.5 text-[12px] font-medium transition-colors"
          :class="activeTab === tab.key ? 'text-primary' : 'text-gray-500 hover:text-gray-700'"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
          <span
            v-if="activeTab === tab.key"
            class="absolute inset-x-2 bottom-0 h-0.5 rounded bg-primary"
          ></span>
        </button>
      </div>

      <div class="custom-scrollbar max-h-[min(424px,42vh)] overflow-y-auto px-3.5 pb-3">
        <div v-if="activeTab === 'sections'" class="pt-1">
          <div v-for="group in visibleGroups" :key="group.key">
            <button
              type="button"
              class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-gray-50"
              @click="toggleGroup(group.key)"
            >
              <svg
                class="h-2.5 w-2.5 shrink-0 text-gray-400 transition-transform"
                :class="openGroup === group.key ? 'rotate-90' : ''"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                stroke-width="2.8"
              >
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 6l6 6-6 6" />
              </svg>
              <span class="shrink-0 text-[12px] font-medium text-gray-700">{{ group.title }}</span>
              <span class="shrink-0 rounded bg-gray-100 px-1.5 text-[10px] leading-4 text-gray-500">
                {{ group.items.length }}
              </span>
              <span class="truncate text-[11px] text-gray-400">{{ group.summary }}</span>
            </button>

            <div v-show="openGroup === group.key" class="ml-3.5 border-l border-gray-100 pb-1 pl-3">
              <div v-for="item in group.items" :key="item.name" class="py-1.5">
                <div class="flex flex-wrap items-center gap-1.5">
                  <span
                    class="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 font-mono text-[10.5px] text-gray-700"
                  >
                    {{ item.name }}
                  </span>
                  <span
                    v-if="item.pill"
                    class="rounded border px-1.5 py-0.5 text-[10px]"
                    :class="pillClass(item.pill.kind)"
                  >
                    {{ item.pill.label }}
                  </span>
                </div>
                <div
                  class="mt-1 text-[11px] leading-[1.65] text-gray-500"
                  v-html="renderDesc(item.desc)"
                ></div>
              </div>
            </div>
          </div>
        </div>

        <div v-else class="pt-1.5">
          <div class="space-y-1">
            <template v-for="row in stackRows" :key="row.label">
              <div v-if="row.tag === 'boundary'" class="flex items-center gap-2 py-1">
                <span class="h-px flex-1 bg-gray-200"></span>
                <span class="shrink-0 text-[10px] text-gray-400">缓存边界（cache reorder 开启时）</span>
                <span class="h-px flex-1 bg-gray-200"></span>
              </div>
              <div
                v-else
                class="flex items-start gap-2 rounded-lg border px-2 py-1.5"
                :class="
                  row.tag === 'you'
                    ? 'border-primary/30 bg-primary/5'
                    : 'border-gray-100 bg-gray-50/60'
                "
              >
                <span class="mt-px w-3 shrink-0 text-[10px] font-semibold text-gray-400">
                  {{ row.idx }}
                </span>
                <span
                  class="flex-1 text-[11.5px] leading-snug"
                  :class="row.tag === 'you' ? 'font-medium text-primary' : 'text-gray-700'"
                >
                  {{ row.label }}
                </span>
                <span
                  class="shrink-0 rounded px-1 text-[10px] leading-4"
                  :class="row.tag === 'you' ? 'bg-primary/10 text-primary' : 'bg-white text-gray-400'"
                >
                  {{ row.tag === 'you' ? '你的内容' : row.tag }}
                </span>
              </div>
            </template>
          </div>
          <p class="mt-2 text-[10.5px] leading-relaxed text-gray-500">{{ STACK_FOOTNOTE }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* MarkdownEditor 的同名样式是 scoped 的，这里需要自己声明一份，否则继承到浏览器默认宽滚动条 */
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #e5e7eb;
  border-radius: 10px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #d1d5db;
}
</style>
