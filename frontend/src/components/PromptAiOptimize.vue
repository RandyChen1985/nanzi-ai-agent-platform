<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue';
import axios from 'axios';
import { SparklesIcon, ClipboardDocumentIcon, CheckBadgeIcon, XMarkIcon } from '@heroicons/vue/24/outline';
import ConfirmModal from './ConfirmModal.vue';

export type PromptOptimizeSuggestion = {
  title?: string;
  reason?: string;
  content: string;
};

const props = defineProps<{
  content: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (e: 'apply', content: string): void;
  (e: 'toast', message: string, type?: 'success' | 'error' | 'info'): void;
}>();

const showOptimizeConfirm = ref(false);
const optimizing = ref(false);
const showOptimizeModal = ref(false);
const optimizeSuggestions = ref<PromptOptimizeSuggestion[]>([]);
const activeOptimizeTab = ref(0);
let optimizeAbort: AbortController | null = null;

const canOptimize = computed(() => Boolean((props.content || '').trim()) && !optimizing.value && !props.disabled);

const notify = (message: string, type: 'success' | 'error' | 'info' = 'info') => {
  emit('toast', message, type);
};

const openConfirm = () => {
  if (!(props.content || '').trim()) {
    notify('请先填写提示词内容', 'error');
    return;
  }
  showOptimizeConfirm.value = true;
};

const cancelOptimize = () => {
  if (!optimizing.value) return;
  optimizeAbort?.abort();
  optimizeAbort = null;
  optimizing.value = false;
  notify('已取消 AI 润色', 'info');
};

const runOptimize = async () => {
  if (!(props.content || '').trim()) return;
  showOptimizeConfirm.value = false;
  optimizeAbort?.abort();
  optimizeAbort = new AbortController();
  const signal = optimizeAbort.signal;
  optimizing.value = true;
  try {
    const res = await axios.post(
      '/api/portal/prompts/optimize',
      { content: props.content },
      { signal },
    );
    if (signal.aborted) return;
    optimizeSuggestions.value = res.data.suggestions || [];
    activeOptimizeTab.value = 0;
    if (!optimizeSuggestions.value.length) {
      notify('未生成可用优化方案', 'error');
      return;
    }
    showOptimizeModal.value = true;
  } catch (err: any) {
    if (
      signal.aborted ||
      axios.isCancel?.(err) ||
      err?.code === 'ERR_CANCELED' ||
      err?.name === 'CanceledError' ||
      err?.name === 'AbortError'
    ) {
      return;
    }
    notify(err?.response?.data?.detail || '优化失败', 'error');
  } finally {
    if (optimizeAbort?.signal === signal) {
      optimizeAbort = null;
    }
    if (!signal.aborted) {
      optimizing.value = false;
    }
  }
};

const applySuggestion = (content: string) => {
  emit('apply', content);
  showOptimizeModal.value = false;
  notify('已应用优化建议', 'success');
};

const copyToClipboard = async (content: string) => {
  try {
    await navigator.clipboard.writeText(content || '');
    notify('已复制到剪贴板', 'success');
  } catch {
    notify('复制失败', 'error');
  }
};

onUnmounted(() => {
  optimizeAbort?.abort();
  optimizeAbort = null;
});
</script>

<template>
  <button
    v-has-perm="'element:prompts:optimize'"
    type="button"
    @click="openConfirm"
    :disabled="!canOptimize"
    class="ml-1 flex items-center px-2.5 py-1 text-[11px] font-semibold rounded-md text-indigo-600 hover:bg-indigo-50 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent"
  >
    <SparklesIcon class="w-3.5 h-3.5 mr-1" :class="{ 'animate-spin': optimizing }" />
    AI 润色
  </button>

  <ConfirmModal
    v-if="showOptimizeConfirm"
    title="AI 提示词润色"
    message="AI 将针对当前内容生成 8 个侧重点不同的优化方案（含工具调用、反幻觉、输出契约等高级范式），大约需要几秒钟。是否开始？"
    confirm-text="开始润色"
    cancel-text="取消"
    type="primary"
    @confirm="runOptimize"
    @cancel="showOptimizeConfirm = false"
  />

  <Teleport to="body">
    <transition name="fade">
      <div
        v-if="optimizing"
        class="fixed inset-0 z-[10050] bg-white/50 backdrop-blur-[2px] flex flex-col items-center justify-center"
      >
        <div class="relative p-10 pt-12 bg-white rounded-3xl shadow-2xl border border-indigo-100 flex flex-col items-center min-w-[280px]">
          <button
            type="button"
            class="absolute top-3 right-3 p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
            title="关闭"
            aria-label="关闭润色遮罩"
            @click="cancelOptimize"
          >
            <XMarkIcon class="w-5 h-5" />
          </button>
          <div class="relative w-20 h-20 mb-6">
            <div class="absolute inset-0 rounded-full border-4 border-indigo-50"></div>
            <div class="absolute inset-0 rounded-full border-4 border-indigo-500 border-t-transparent animate-spin"></div>
            <SparklesIcon class="absolute inset-0 m-auto w-8 h-8 text-indigo-500 animate-pulse" />
          </div>
          <div class="text-base font-bold text-gray-900 mb-2">AI 正在深度优化中...</div>
          <div class="text-xs text-gray-400 mb-5">正在为您生成 8 个差异化方案</div>
          <button
            type="button"
            class="px-4 py-2 text-xs font-semibold text-gray-600 bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded-xl transition-colors"
            @click="cancelOptimize"
          >
            取消润色
          </button>
        </div>
      </div>
    </transition>

    <div
      v-if="showOptimizeModal"
      class="fixed inset-0 z-[10000] flex items-center justify-center p-4 bg-gray-900/40 backdrop-blur-sm"
    >
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/80">
          <div class="flex items-center">
            <div class="p-2 bg-indigo-100 rounded-lg mr-3">
              <SparklesIcon class="w-5 h-5 text-indigo-600" />
            </div>
            <div>
              <h3 class="text-sm font-bold text-gray-900">AI 优化建议</h3>
              <p class="text-[10px] text-gray-400 font-medium">共生成了多个侧重点不同的提示词方案</p>
            </div>
          </div>
          <button
            type="button"
            @click="showOptimizeModal = false"
            class="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="flex-1 flex min-h-0">
          <div class="w-48 border-r border-gray-100 bg-gray-50/30 overflow-y-auto">
            <div
              v-for="(item, index) in optimizeSuggestions"
              :key="index"
              @click="activeOptimizeTab = index"
              class="px-4 py-4 cursor-pointer transition-all border-l-4"
              :class="
                activeOptimizeTab === index
                  ? 'bg-white border-indigo-500 text-indigo-700 font-bold'
                  : 'border-transparent text-gray-500 hover:bg-gray-100/50'
              "
            >
              <div class="text-[10px] opacity-60 mb-1 uppercase tracking-tighter">方案 {{ index + 1 }}</div>
              <div class="text-xs truncate">{{ item.title || `方案 ${index + 1}` }}</div>
            </div>
          </div>

          <div class="flex-1 flex flex-col min-h-0 bg-white">
            <div
              v-if="optimizeSuggestions[activeOptimizeTab]"
              class="flex-1 flex flex-col p-6 overflow-hidden"
            >
              <div class="mb-4 p-3 bg-indigo-50 border border-indigo-100 rounded-xl">
                <div class="text-[10px] font-bold text-indigo-700 uppercase mb-1">推荐理由 (Reason)</div>
                <p class="text-xs text-indigo-900 leading-relaxed">
                  {{ optimizeSuggestions[activeOptimizeTab].reason || '暂无说明' }}
                </p>
              </div>

              <div class="flex-1 relative bg-gray-50 rounded-xl overflow-hidden border border-gray-100 min-h-0">
                <div class="absolute top-3 right-3 z-10">
                  <button
                    type="button"
                    @click="copyToClipboard(optimizeSuggestions[activeOptimizeTab].content)"
                    class="p-1.5 bg-white shadow-sm border border-gray-200 rounded-lg text-gray-400 hover:text-indigo-600 transition-all"
                    title="复制此版本"
                  >
                    <ClipboardDocumentIcon class="w-4 h-4" />
                  </button>
                </div>
                <pre class="w-full h-full p-6 text-xs text-gray-700 font-mono overflow-y-auto whitespace-pre-wrap">{{
                  optimizeSuggestions[activeOptimizeTab].content
                }}</pre>
              </div>

              <div class="mt-6 flex justify-end">
                <button
                  type="button"
                  @click="applySuggestion(optimizeSuggestions[activeOptimizeTab].content)"
                  class="px-6 py-2.5 bg-indigo-600 text-white text-xs font-bold rounded-xl hover:bg-indigo-700 shadow-lg shadow-indigo-600/20 active:scale-95 transition-all flex items-center"
                >
                  <CheckBadgeIcon class="w-4 h-4 mr-2" />
                  应用此方案
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
