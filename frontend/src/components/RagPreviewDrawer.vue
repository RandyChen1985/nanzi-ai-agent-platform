<template>
  <teleport to="body">
    <div
      class="fixed top-0 right-0 h-full w-full sm:w-[45%] bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-800 shadow-2xl z-[260] flex flex-col transition-transform duration-300 ease-in-out transform"
      :class="modelValue ? 'translate-x-0' : 'translate-x-full'"
    >
      <!-- Header -->
      <div class="flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-gray-800 flex-shrink-0 bg-gray-50/50 dark:bg-gray-800/40">
        <div class="min-w-0">
          <h3 class="text-sm font-bold text-gray-800 dark:text-gray-100 truncate" :title="docName">
            {{ docName }}
          </h3>
          <p class="text-[11px] text-gray-400 mt-0.5">
            {{ subtitle }}
          </p>
        </div>
        <button
          type="button"
          class="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors flex-shrink-0"
          title="关闭预览"
          @click="close"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <!-- Collapsible panels -->
      <div class="flex-1 min-h-0 flex flex-col">
        <!-- Preview panel -->
        <section
          class="flex flex-col min-h-0 border-b border-gray-100 dark:border-gray-800 transition-[flex-grow] duration-300 ease-in-out"
          :class="previewExpanded ? 'flex-1' : 'flex-shrink-0'"
        >
          <button
            type="button"
            class="flex items-center justify-between gap-2 px-4 py-2.5 flex-shrink-0 bg-gray-50/80 dark:bg-gray-800/50 hover:bg-gray-100/90 dark:hover:bg-gray-800 transition-colors text-left"
            @click="togglePreview"
          >
            <span class="text-[11px] font-bold text-gray-600 dark:text-gray-300 tracking-wide flex items-center gap-1.5 min-w-0">
              <svg class="w-3.5 h-3.5 text-primary flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <span class="truncate">原档预览</span>
            </span>
            <svg
              class="w-4 h-4 text-gray-400 flex-shrink-0 transition-transform duration-200"
              :class="{ 'rotate-180': previewExpanded }"
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          <div
            v-show="previewExpanded"
            class="flex-1 min-h-0 bg-gray-100/30 relative flex flex-col items-center justify-center overflow-hidden"
          >
            <DocumentViewer
              v-if="canPreview && modelValue && fileUrl"
              :url="fileUrl"
              :filename="docName"
              :meta="{ filename: docName, mime: resolveDocumentViewerMime(docName) }"
              :theme="viewerTheme"
              @fallback-download="downloadOriginalFile"
            />
            <div v-else-if="isDownloadOnlyOffice" class="p-8 text-center max-w-sm space-y-4 flex flex-col items-center">
              <div class="inline-flex p-4 bg-amber-50 dark:bg-amber-950/20 text-amber-600 dark:text-amber-400 rounded-full">
                <svg class="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <div>
                <h4 class="text-sm font-bold text-gray-800 dark:text-gray-100">Office 文档暂不支持在线预览</h4>
                <p class="text-xs text-gray-400 mt-1.5 leading-relaxed">
                  由于您的文件是 Word/Excel 类型，系统已自动拉起浏览器进行本地下载。若未开始，可点击下方按钮重新发起下载。
                </p>
              </div>
              <button
                type="button"
                class="inline-flex items-center px-4 py-2 bg-blue-600 text-white text-xs font-bold rounded-xl shadow-sm hover:bg-blue-700 active:scale-95 transition-all"
                @click="downloadOriginalFile"
              >
                重新下载原档
              </button>
            </div>

            <div
              v-else-if="modelValue && fileUrl"
              class="relative w-full h-full"
            >
              <iframe
                :src="`${fileUrl}#page=${pageNo}`"
                class="w-full h-full border-none"
                @load="onPreviewLoaded"
              />
              <!-- 原生 PDF 阅读器自带零反馈：从打开到第 N 页画出来之间是纯空白 -->
              <div
                v-if="previewLoading"
                class="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-gray-100/70 dark:bg-gray-900/70 backdrop-blur-[1px] pointer-events-none"
              >
                <div class="h-6 w-6 animate-spin rounded-full border-2 border-gray-200 border-t-primary dark:border-gray-700"></div>
                <span class="text-xs text-gray-400 dark:text-gray-500">正在加载原档预览…</span>
              </div>
            </div>
            <div v-else class="absolute inset-0 flex items-center justify-center text-gray-400 text-xs">
              正在加载文档预览...
            </div>
          </div>
        </section>

        <!-- Citation panel -->
        <section
          class="flex flex-col min-h-0 transition-[flex-grow] duration-300 ease-in-out"
          :class="citationExpanded ? 'flex-1' : 'flex-shrink-0'"
        >
          <button
            type="button"
            class="flex items-center justify-between gap-2 px-4 py-2.5 flex-shrink-0 bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800/60 transition-colors text-left border-t border-gray-100 dark:border-gray-800"
            @click="toggleCitation"
          >
            <span class="text-[11px] font-bold text-gray-600 dark:text-gray-300 tracking-wide flex items-center gap-1.5 min-w-0">
              <svg class="w-3.5 h-3.5 text-primary flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <span class="truncate">被引用原文段落</span>
            </span>
            <svg
              class="w-4 h-4 text-gray-400 flex-shrink-0 transition-transform duration-200"
              :class="{ 'rotate-180': citationExpanded }"
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          <div
            v-show="citationExpanded"
            class="flex-1 min-h-0 flex flex-col bg-white dark:bg-gray-900 px-4 pb-4 overflow-hidden"
          >
            <div
              class="flex-1 min-h-0 overflow-y-auto p-3 bg-blue-50/30 dark:bg-blue-900/5 border border-blue-100/50 dark:border-blue-900/10 rounded-xl text-xs text-gray-600 dark:text-gray-300 leading-relaxed custom-table-render scrollbar-thin"
              v-html="content"
            />
          </div>
        </section>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import DocumentViewer from "@/components/embed/DocumentViewer.vue";
import { useDarkThemeFlag } from "@/composables/useDarkThemeFlag";
import {
  canPreviewWithDocumentViewer,
  shouldDownloadInsteadOfPreview,
  resolveDocumentViewerMime,
} from "@/utils/workspaceFilePreview";

const modelValue = defineModel<boolean>({ default: false });

const props = defineProps<{
  docName: string;
  pageNo: string | number;
  fileUrl: string;
  content: string;
}>();

/** Office 与文本格式：交给 DocumentViewer 内联预览 */
const canPreview = computed(() => canPreviewWithDocumentViewer(props.docName));
/** 只有旧版二进制 .ppt 会命中：保持「暂不支持 + 下载」 */
const isDownloadOnlyOffice = computed(() => shouldDownloadInsteadOfPreview(props.docName));
/** 抽屉跟随全局暗色主题（本组件已有 dark: 适配）；composable 必须在 setup 顶层调用一次 */
const isDark = useDarkThemeFlag();
const viewerTheme = computed<'light' | 'dark'>(() => (isDark.value ? 'dark' : 'light'));
/** Office 不跳页，文案不能声称「第 N 页」 */
const subtitle = computed(() =>
  canPreview.value ? 'RAG 关联原档预览' : `第 ${props.pageNo} 页 RAG 关联原档智能高亮预览`,
);

/**
 * iframe 加载反馈。
 *
 * PDF 为了能用 `#page=N` 跳到引用页，只能交给浏览器原生阅读器（file-viewer 的 pdf
 * 渲染器不认这个 fragment），而原生阅读器没有任何加载指示：从打开到目标页画出来
 * 之间就是一片空白。这里自己补一层遮罩，由 iframe 的 load 事件收起。
 */
const PREVIEW_LOAD_TIMEOUT_MS = 8000;
const previewLoading = ref(false);
let previewLoadingTimer: ReturnType<typeof setTimeout> | null = null;

const clearPreviewLoadingTimer = () => {
  if (previewLoadingTimer) {
    clearTimeout(previewLoadingTimer);
    previewLoadingTimer = null;
  }
};

const startPreviewLoading = () => {
  clearPreviewLoadingTimer();
  previewLoading.value = true;
  // 兜底：iframe 的 load 在个别情况下不会触发（插件未就绪、请求被中断），
  // 没有兜底就会让遮罩永久盖住文档，那比不加加载反馈更糟。
  previewLoadingTimer = setTimeout(() => {
    previewLoadingTimer = null;
    previewLoading.value = false;
  }, PREVIEW_LOAD_TIMEOUT_MS);
};

const onPreviewLoaded = () => {
  clearPreviewLoadingTimer();
  previewLoading.value = false;
};

/** 只有走原生 iframe 的分支需要遮罩；Office/文本走 DocumentViewer，它自带 loading */
const usesNativeIframe = computed(
  () => !canPreview.value && !isDownloadOnlyOffice.value,
);

watch(
  () => [modelValue.value, props.fileUrl] as const,
  ([visible, url]) => {
    if (visible && url && usesNativeIframe.value) startPreviewLoading();
  },
  { immediate: true },
);

onUnmounted(clearPreviewLoadingTimer);

const previewExpanded = ref(true);
const citationExpanded = ref(true);

watch(modelValue, (visible) => {
  if (visible) {
    previewExpanded.value = true;
    citationExpanded.value = true;
  }
});

const togglePreview = () => {
  previewExpanded.value = !previewExpanded.value;
  if (!previewExpanded.value && !citationExpanded.value) {
    citationExpanded.value = true;
  }
};

const toggleCitation = () => {
  citationExpanded.value = !citationExpanded.value;
  if (!citationExpanded.value && !previewExpanded.value) {
    previewExpanded.value = true;
  }
};

const close = () => {
  modelValue.value = false;
};

const downloadOriginalFile = () => {
  if (!props.fileUrl) return;
  const link = document.createElement("a");
  link.href = props.fileUrl;
  link.download = props.docName;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
};
</script>

<style scoped>
.scrollbar-thin::-webkit-scrollbar {
  width: 4px;
}
.scrollbar-thin::-webkit-scrollbar-thumb {
  background-color: rgba(156, 163, 175, 0.35);
  border-radius: 9999px;
}
</style>
