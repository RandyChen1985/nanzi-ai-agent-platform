<script setup lang="ts">
import { ref } from 'vue';
import { renderMarkdown } from '../utils/markdown';

const props = withDefaults(defineProps<{
  modelValue?: string;
  placeholder?: string;
  height?: string;
  fill?: boolean;
  theme?: 'light' | 'dark';
}>(), {
  theme: 'light',
  fill: false,
});

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
}>();

const activeTab = ref<'edit' | 'preview'>('edit');

const updateValue = (event: Event) => {
  const target = event.target as HTMLTextAreaElement;
  emit('update:modelValue', target.value);
};
</script>

<template>
  <div
    class="flex flex-col border border-gray-200 rounded-xl overflow-hidden bg-white shadow-sm transition-all focus-within:ring-2 focus-within:ring-primary/30 focus-within:border-primary"
    :class="fill ? 'flex-1 min-h-0 h-full' : ''"
  >
    <!-- Toolbar -->
    <div class="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-200 flex-shrink-0">
      <div class="flex p-0.5 rounded-lg bg-gray-100 border border-gray-200">
        <button
          type="button"
          @click="activeTab = 'edit'"
          class="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md transition-all"
          :class="activeTab === 'edit' ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:text-gray-700'"
        >
          <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
          </svg>
          编辑
        </button>
        <button
          type="button"
          @click="activeTab = 'preview'"
          class="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md transition-all"
          :class="activeTab === 'preview' ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:text-gray-700'"
        >
          <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
          </svg>
          预览
        </button>
      </div>
      <div class="text-[10px] text-gray-400 font-mono">
        Markdown
      </div>
    </div>

    <!-- Content Area -->
    <div
      class="relative w-full min-h-0"
      :class="fill ? 'flex-1' : ''"
      :style="!fill ? { height: height || '450px' } : {}"
    >
      <textarea
        v-show="activeTab === 'edit'"
        :value="modelValue || ''"
        @input="updateValue"
        :placeholder="placeholder"
        class="absolute inset-0 w-full h-full p-4 text-sm font-mono outline-none border-none resize-none custom-scrollbar leading-relaxed z-10"
        :class="theme === 'dark'
          ? 'bg-gray-900 text-gray-100'
          : 'bg-white text-gray-800'"
      ></textarea>

      <div
        v-show="activeTab === 'preview'"
        class="absolute inset-0 w-full h-full overflow-y-auto p-6 bg-white custom-scrollbar z-10"
      >
        <div
          v-if="modelValue"
          class="markdown-body prose prose-sm max-w-none prose-slate prose-headings:font-bold prose-a:text-blue-600 prose-pre:bg-gray-50 prose-pre:border prose-pre:border-gray-200"
          v-html="renderMarkdown(modelValue)"
        ></div>
        <div v-else class="h-full flex flex-col items-center justify-center text-gray-300">
          <svg class="w-12 h-12 mb-2 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <span class="text-sm">暂无内容预览</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
textarea.custom-scrollbar::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
textarea.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
textarea.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}
textarea.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.5);
  border-radius: 3px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background-color: rgba(107, 114, 128, 0.8);
}
</style>
