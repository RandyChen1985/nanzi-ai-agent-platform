<template>
  <teleport to="body">
    <div
      v-if="visible"
      :class="[
        'z-[145]',
        pinned
          ? pinnedContainerClass
          : isMobile
            ? 'fixed inset-0 flex flex-col overflow-hidden'
            : 'fixed inset-0 overflow-hidden',
      ]"
      :style="pinned ? pinnedContainerStyle : undefined"
    >
      <div
        v-if="!pinned"
        :class="[
          'bg-gray-500/30 backdrop-blur-[1px] transition-opacity',
          isMobile ? 'flex-1 min-h-0 w-full' : 'absolute inset-0',
        ]"
        aria-hidden="true"
        @click="emit('close')"
      />
      <aside
        :class="[
          pinned
            ? isMobile
              ? 'w-full h-full flex pointer-events-auto min-h-0'
              : 'h-full flex pointer-events-auto'
            : isMobile
              ? 'w-full h-full flex justify-center min-h-0 shrink-0'
              : isMaximized
                ? 'absolute inset-0 flex'
                : 'absolute inset-y-0 right-0 pl-0 sm:pl-10 max-w-full flex',
        ]"
      >
          <section
          ref="previewPanel"
          :class="[
            'relative z-10 flex min-h-0 min-w-0 flex-col overflow-hidden border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-900',
            isResizing ? 'select-none transition-none' : 'transition-all duration-300',
            isMobile
              ? 'h-full w-full max-w-none rounded-t-2xl border-t'
              : isMaximized
                ? 'h-full w-full max-w-none border'
                : 'h-full w-screen max-w-[calc(100vw-300px)] border-l',
          ]"
          :style="panelStyle"
          aria-label="网页预览"
        >
          <div v-if="isResizing" class="fixed inset-0 z-[300] cursor-col-resize select-none" />
          <div
            v-if="!isMobile"
            class="absolute bottom-0 left-0 top-0 z-50 flex w-3 -translate-x-1/2 cursor-col-resize select-none items-center justify-center touch-none"
            :class="isResizing ? 'bg-primary/30' : 'hover:bg-primary/20'"
            title="按住左右拖拽调整网页预览宽度（双击重置）"
            @mousedown="startResize"
            @dblclick="resetWidth"
          >
            <div
              class="flex h-8 w-1 flex-col items-center justify-center gap-0.5 rounded-full transition-all"
              :class="isResizing ? 'scale-110 bg-primary shadow-sm' : 'bg-gray-300 dark:bg-gray-600'"
            >
              <div class="h-0.5 w-0.5 rounded-full bg-white dark:bg-gray-900" />
              <div class="h-0.5 w-0.5 rounded-full bg-white dark:bg-gray-900" />
              <div class="h-0.5 w-0.5 rounded-full bg-white dark:bg-gray-900" />
            </div>
          </div>
          <div v-if="isMobile" class="flex shrink-0 justify-center pb-1 pt-2" aria-hidden="true">
            <div class="h-1 w-10 rounded-full bg-gray-300 dark:bg-gray-600" />
          </div>

          <header class="flex min-h-14 shrink-0 items-center justify-between gap-3 border-b border-gray-200 bg-gray-50/50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/20">
            <div class="flex min-w-0 items-center gap-2">
              <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary dark:bg-primary-dark/40 dark:text-primary-hover" aria-hidden="true"><GlobeAltIcon class="h-4 w-4" aria-hidden="true" /></span>
              <div class="min-w-0">
                <h2 class="truncate text-sm font-bold text-gray-900 dark:text-gray-100">网页预览</h2>
                <p class="truncate text-[10px] text-gray-400">直接加载网页，不启用后端浏览器自动化</p>
              </div>
            </div>
            <div class="flex shrink-0 items-center gap-1.5">
              <a
                v-if="safeUrl"
                :href="safeUrl"
                target="_blank"
                rel="noopener noreferrer"
                class="inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-xs font-semibold text-primary hover:bg-primary/5 dark:text-primary-hover dark:hover:bg-primary-dark/40"
                @click="handleOpenInNewTab"
              >
                <span>在新窗口打开</span>
                <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M15 3h6v6" />
                  <path d="M10 14 21 3" />
                  <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                </svg>
              </a>
              <button
                v-if="!isMobile && !pinned"
                type="button"
                class="rounded-md px-2 py-1.5 text-xs font-semibold text-gray-500 hover:bg-gray-100 hover:text-primary dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-primary-hover"
                :title="isMaximized ? '退出最大化' : '最大化查看网页'"
                :aria-label="isMaximized ? '退出最大化' : '最大化查看网页'"
                @click="isMaximized = !isMaximized"
              >
                {{ isMaximized ? '退出最大化' : '最大化查看' }}
              </button>
              <button
                v-if="!isMobile"
                type="button"
                class="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-primary dark:hover:bg-gray-800 dark:hover:text-primary-hover"
                :class="pinned ? 'bg-primary/5 text-primary dark:bg-primary/10 dark:text-primary-hover' : ''"
                :title="pinButtonTitle"
                :aria-label="pinned ? '取消钉住' : '钉住侧栏'"
                @click="pinned = !pinned"
              >
                <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M12 17v5" />
                  <path d="M9 10.7a2 2 0 0 1-1.1 1.8l-1.8.9A2 2 0 0 0 5 15.2V16h14v-.8a2 2 0 0 0-1.1-1.8l-1.8-.9A2 2 0 0 1 15 10.7V7a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v3.7Z" />
                </svg>
              </button>
              <button
                type="button"
                class="rounded-md p-1.5 text-xl leading-none text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
                title="关闭网页预览"
                aria-label="关闭网页预览"
                @click="emit('close')"
              >
                ×
              </button>
            </div>
          </header>

          <div class="flex shrink-0 items-center gap-2 border-b border-gray-100 bg-gray-50 px-4 py-2 dark:border-gray-800 dark:bg-gray-950/40">
            <span class="shrink-0 text-[10px] font-semibold uppercase tracking-wider text-gray-400">URL</span>
            <span v-if="safeUrl" class="min-w-0 flex-1 truncate font-mono text-[11px] text-gray-600 dark:text-gray-300" :title="safeUrl">{{ safeUrl }}</span>
            <span v-else class="min-w-0 flex-1 text-xs text-gray-400">没有可预览的 HTTP(S) 地址</span>
            <span
              v-if="safeUrl && probing"
              class="shrink-0 rounded-full bg-primary/5 px-2 py-0.5 text-[10px] font-semibold text-primary dark:bg-primary-dark/40 dark:text-primary-hover"
              title="正在由后端实测目标站点的响应头；实测期间先按可内嵌展示，避免误拦"
            >
              实测内嵌策略中…
            </span>
            <label v-if="safeUrl" class="flex shrink-0 items-center gap-1 text-[11px] font-semibold text-gray-500 dark:text-gray-400">
              <span>缩放</span>
              <select
                v-model="previewZoom"
                class="rounded-md border border-gray-200 bg-white px-1.5 py-1 text-[11px] font-semibold text-gray-600 outline-none hover:border-primary/25 focus:border-primary dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"
                aria-label="网页缩放比例"
                title="调整网页显示比例，解决固定宽度网页的横向溢出"
                @change="savePreviewZoom"
              >
                <option value="auto">铺满窗口（自动适配 {{ effectiveZoom }}%）</option>
                <option v-for="zoom in PREVIEW_ZOOM_OPTIONS" :key="zoom" :value="zoom">{{ zoom }}%</option>
              </select>
              <button
                v-if="previewZoom !== 'auto'"
                type="button"
                class="rounded-md px-1.5 py-1 text-[11px] font-semibold text-primary hover:bg-primary/5 dark:text-primary-hover dark:hover:bg-primary-dark/40"
                title="恢复为铺满窗口的自动适配比例"
                @click="setAutoZoom"
              >
                铺满
              </button>
            </label>
            <button
              v-if="safeUrl"
              type="button"
              class="shrink-0 rounded-md px-2 py-1 text-[11px] font-semibold text-gray-500 hover:bg-white hover:text-primary dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-primary-hover"
              title="重新加载网页"
              @click="reload"
            >
              刷新
            </button>
          </div>

          <div v-if="safeUrl" class="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-gray-100 dark:bg-gray-950">
            <!-- 常见安全防内嵌受限站点提示卡片 -->
            <div
              v-if="shouldShowRestrictedCard"
              class="flex flex-1 flex-col items-center justify-center p-6 text-center"
            >
              <div class="mx-auto flex max-w-sm flex-col items-center rounded-2xl border border-gray-200/80 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900">
                <div class="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-amber-50 text-amber-500 ring-8 ring-amber-50/50 dark:bg-amber-950/40 dark:text-amber-400 dark:ring-amber-950/20">
                  <svg class="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect width="18" height="18" x="3" y="3" rx="2" />
                    <path d="M9 3v18" />
                    <path d="m14 9 3 3-3 3" />
                  </svg>
                </div>
                <div v-if="targetHostname" class="mb-2 inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-0.5 font-mono text-[11px] font-medium text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                  <span>{{ targetHostname }}</span>
                </div>
                <h3 class="text-base font-bold text-gray-900 dark:text-gray-100">
                  {{ restrictedDecision.title }}
                </h3>
                <p class="mt-2 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
                  {{ restrictedDecision.detail }}
                </p>
                <p class="mt-3 text-[10px] font-semibold tracking-wider text-gray-400">
                  依据：{{ restrictedDecision.basis === 'probe' ? '平台实测' : '经验预判' }}
                </p>
                <dl
                  v-if="restrictedSignals.length"
                  class="mt-3 w-full space-y-1 rounded-lg bg-gray-50 p-2 text-left dark:bg-gray-800/60"
                >
                  <div
                    v-for="signal in restrictedSignals"
                    :key="signal.name"
                    class="flex min-w-0 gap-1.5 text-[10px] leading-relaxed"
                  >
                    <dt class="shrink-0 font-mono font-semibold text-gray-500 dark:text-gray-400">{{ signal.name }}</dt>
                    <dd class="min-w-0 break-all font-mono text-gray-600 dark:text-gray-300">{{ signal.value }}</dd>
                  </div>
                </dl>
                <div class="mt-6 flex w-full flex-col gap-2.5">
                  <a
                    :href="safeUrl"
                    target="_blank"
                    rel="noopener noreferrer"
                    class="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white shadow-sm transition hover:bg-primary-hover active:scale-[0.99] dark:bg-primary dark:hover:bg-primary"
                    @click="handleOpenInNewTab"
                  >
                    <span>在新标签页打开网页</span>
                    <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M15 3h6v6" />
                      <path d="M10 14 21 3" />
                      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                    </svg>
                  </a>
                  <button
                    type="button"
                    class="text-xs text-gray-400 transition hover:text-gray-600 dark:hover:text-gray-300"
                    @click="forceShowIframe = true"
                  >
                    仍尝试在内嵌框架中加载
                  </button>
                  <button
                    type="button"
                    class="text-[11px] font-semibold text-primary transition hover:text-primary-dark disabled:cursor-not-allowed disabled:text-gray-400 dark:text-primary-hover dark:hover:text-primary-hover"
                    :disabled="probing"
                    title="忽略缓存，重新由后端实测目标站点的响应头"
                    @click="recheckEmbed"
                  >
                    {{ probing ? '实测中…' : '重新检测' }}
                  </button>
                </div>
              </div>
            </div>

            <!-- iframe 正常展示区及底部友好提示 -->
            <div v-else class="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-white dark:bg-gray-900">
              <div class="relative min-h-0 min-w-0 flex-1 overflow-hidden">
                <iframe
                  :key="frameKey"
                  :src="safeUrl"
                  class="absolute inset-0 border-0 bg-white dark:bg-gray-900"
                  :style="frameScaleStyle"
                  title="网页预览内容"
                  sandbox="allow-forms allow-modals allow-popups allow-presentation allow-scripts"
                  referrerpolicy="no-referrer"
                />
              </div>
              <div class="flex shrink-0 items-center justify-between border-t border-gray-100 bg-gray-50/80 px-3 py-1.5 text-[11px] text-gray-500 dark:border-gray-800 dark:bg-gray-900/60 dark:text-gray-400">
                <span class="truncate">若页面显示空白或拒绝连接，说明目标网站限制了内嵌</span>
                <a
                  :href="safeUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="shrink-0 font-medium text-primary hover:underline dark:text-primary"
                  @click="handleOpenInNewTab"
                >
                  在新窗口打开 ↗
                </a>
              </div>
            </div>
          </div>
          <div v-else class="flex flex-1 items-center justify-center p-6 text-center text-sm text-gray-500 dark:text-gray-400">
            仅支持预览 HTTP(S) 网页地址。
          </div>
        </section>
      </aside>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { GlobeAltIcon } from "@heroicons/vue/24/outline";
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { isBrowserOpenableUrl } from '@/utils/messageBrowserLinks';
import {
  hostnameOf,
  resolveEmbedDecision,
  type EmbedProbeOutcome,
} from '@/utils/webPreviewEmbed';
import { checkWebPreviewEmbed } from '@/api/webPreview';

const props = defineProps<{
  visible: boolean;
  url: string | null;
  pinnedDockRight?: number;
}>();

const emit = defineEmits<{
  (event: 'close'): void;
}>();

/** 页面已转交新标签页/新窗口，预览面板留着只会继续占位，点击外链后一并关闭。
 *
 * 这里不重置 `pinned`：钉住表达的是「下次仍以常驻侧栏打开」的偏好，
 * 属于跨次会话的用户选择，不该被一次外部跳转清掉。
 */
const handleOpenInNewTab = () => {
  emit('close');
};

const pinned = defineModel<boolean>('pinned', { default: false });
const panelWidth = defineModel<number>('panelWidth', { default: 448 });

const frameKey = ref(0);
const previewPanel = ref<HTMLElement | null>(null);
const isMobile = ref(
  typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches,
);
let mobileMq: MediaQueryList | null = null;
const customWidth = ref<number | null>(null);
const isResizing = ref(false);
const isMaximized = ref(false);
const renderedPanelWidth = ref<number | null>(null);
const previewZoom = ref<'auto' | number>('auto');
const PREVIEW_ZOOM_OPTIONS = [40, 50, 60, 67, 75, 80, 90, 100];
const DESKTOP_PAGE_BASE_WIDTH = 1040;
const WEB_PREVIEW_PANEL_WIDTH_STORAGE_KEY = 'nanzi_web_preview_panel_width';
const WEB_PREVIEW_ZOOM_STORAGE_KEY = 'nanzi_web_preview_zoom_v2';
const viewportWidth = ref(typeof window !== 'undefined' ? window.innerWidth : 1024);
let panelResizeObserver: ResizeObserver | null = null;

const forceShowIframe = ref(false);

/** 后端实测结论；`null` 表示尚未取得（探测中或探测失败）。 */
const probeOutcome = ref<EmbedProbeOutcome | null>(null);
/** 实测进行中：期间一律乐观渲染 iframe，避免「先弹卡片、稍后才显示网页」。 */
const probing = ref(false);
/** 同一 URL 的实测结论在会话内复用，避免来回切换时反复出网。 */
const probeCache = new Map<string, EmbedProbeOutcome>();
let probeToken = 0;

const safeUrl = computed(() => {
  const value = props.url?.trim() || '';
  return isBrowserOpenableUrl(value) ? value : '';
});

const targetHostname = computed(() => hostnameOf(safeUrl.value));

/**
 * 是否展示受限提示卡片：**实测结论优先，经验名单只在拿不到实测时降级使用**。
 *
 * 这是本次修复的核心：旧实现只看硬编码名单，`www.baidu.com` 因此被误拦。
 */
const restrictedDecision = computed(() =>
  resolveEmbedDecision(targetHostname.value, probeOutcome.value, { pending: probing.value }),
);

const shouldShowRestrictedCard = computed(
  () => restrictedDecision.value.blocked && !forceShowIframe.value,
);

/** 判定依据的响应头片段（如 `x-frame-options: deny`），让结论可被用户核对。 */
const restrictedSignals = computed(() =>
  Object.entries(restrictedDecision.value.signals).map(([name, value]) => ({ name, value })),
);

/**
 * 由后端实测目标站点是否允许内嵌。
 *
 * 实测失败不拦人：统一折算成「未取得结论」，再由经验名单降级预判。
 */
const probeEmbeddable = async (url: string, options: { force?: boolean } = {}) => {
  if (!url) {
    probeOutcome.value = null;
    return;
  }
  if (!options.force) {
    const cached = probeCache.get(url);
    if (cached) {
      probeOutcome.value = cached;
      return;
    }
  }
  const token = ++probeToken;
  probing.value = true;
  try {
    const outcome = await checkWebPreviewEmbed(url);
    if (token !== probeToken) return;
    probeCache.set(url, outcome);
    probeOutcome.value = outcome;
  } catch {
    if (token !== probeToken) return;
    probeOutcome.value = { embeddable: null, reason: '实测请求异常' };
  } finally {
    if (token === probeToken) probing.value = false;
  }
};

/** 忽略缓存重新实测：目标站点的防嵌套策略随时可能调整。 */
const recheckEmbed = () => {
  const url = safeUrl.value;
  if (!url) return;
  probeCache.delete(url);
  forceShowIframe.value = false;
  void probeEmbeddable(url, { force: true });
};

const frameScaleStyle = computed(() => {
  const scale = effectiveZoom.value / 100;
  return {
    width: `${100 / scale}%`,
    height: `${100 / scale}%`,
    transform: `scale(${scale})`,
    transformOrigin: 'top left',
  };
});

const effectiveZoom = computed(() => {
  if (previewZoom.value !== 'auto') return previewZoom.value;
  if (isMobile.value) return 100;

  const maxPanelWidth = Math.max(320, viewportWidth.value - 300);
  const fallbackWidth = Math.min(customWidth.value || panelWidth.value, maxPanelWidth);
  const availableWidth = renderedPanelWidth.value || fallbackWidth;
  const steppedZoom = Math.floor((availableWidth / DESKTOP_PAGE_BASE_WIDTH) * 100 / 5) * 5;
  return Math.max(40, Math.min(100, steppedZoom));
});

const syncMobile = () => {
  const wasMobile = isMobile.value;
  isMobile.value = !!mobileMq?.matches;
  if (!wasMobile && isMobile.value && pinned.value) pinned.value = false;
  if (isMobile.value) isMaximized.value = false;
};

const pinButtonTitle = computed(() => {
  if (pinned.value) return isMobile.value ? '取消钉住（恢复全屏抽屉）' : '取消钉住（恢复遮罩模式）';
  return isMobile.value ? '钉住底部抽屉（去掉遮罩，可继续聊天）' : '钉住侧栏（去掉遮罩，可继续浏览聊天）';
});

const pinnedContainerClass = computed(() => {
  if (!pinned.value) return '';
  return isMobile.value
    ? 'fixed inset-x-0 bottom-0 h-full max-w-full flex flex-col justify-end pointer-events-none'
    : 'fixed inset-y-0 max-w-full flex pointer-events-none';
});

const pinnedContainerStyle = computed(() => {
  if (isMobile.value) return {};
  return { right: `${Math.max(0, props.pinnedDockRight || 0)}px` };
});

const panelStyle = computed(() => {
  if (isMobile.value) return {};
  if (isMaximized.value) {
    return {
      width: '100vw',
      maxWidth: '100vw',
    };
  }
  const width = customWidth.value || panelWidth.value;
  return {
    width: `${width}px`,
    maxWidth: 'calc(100vw - 300px)',
  };
});

const loadCustomWidth = () => {
  if (typeof window === 'undefined') return;
  const saved = localStorage.getItem(WEB_PREVIEW_PANEL_WIDTH_STORAGE_KEY);
  if (!saved) return;
  const parsed = parseInt(saved, 10);
  if (!Number.isNaN(parsed) && parsed >= 320) {
    customWidth.value = parsed;
    panelWidth.value = parsed;
  }
};

const loadPreviewZoom = () => {
  if (typeof window === 'undefined') return;
  const saved = localStorage.getItem(WEB_PREVIEW_ZOOM_STORAGE_KEY);
  if (!saved) return;
  if (saved === 'auto') {
    previewZoom.value = 'auto';
    return;
  }
  const parsed = Number(saved);
  if (PREVIEW_ZOOM_OPTIONS.includes(parsed)) previewZoom.value = parsed;
};

const savePreviewZoom = () => {
  if (typeof window === 'undefined') return;
  localStorage.setItem(WEB_PREVIEW_ZOOM_STORAGE_KEY, String(previewZoom.value));
};

const setAutoZoom = () => {
  previewZoom.value = 'auto';
  savePreviewZoom();
};

const startResize = (event: MouseEvent) => {
  if (isMobile.value) return;
  event.preventDefault();
  isResizing.value = true;
  document.body.classList.add('select-none');
  window.addEventListener('mousemove', handleResizing);
  window.addEventListener('mouseup', stopResize);
};

const handleResizing = (event: MouseEvent) => {
  if (!isResizing.value) return;
  const viewportWidth = window.innerWidth;
  const minWidth = 320;
  const maxWidth = Math.max(minWidth, viewportWidth - 300);
  const nextWidth = Math.min(maxWidth, Math.max(minWidth, viewportWidth - event.clientX));
  customWidth.value = nextWidth;
  panelWidth.value = nextWidth;
};

const stopResize = () => {
  if (!isResizing.value) return;
  isResizing.value = false;
  document.body.classList.remove('select-none');
  window.removeEventListener('mousemove', handleResizing);
  window.removeEventListener('mouseup', stopResize);
  if (customWidth.value) localStorage.setItem(WEB_PREVIEW_PANEL_WIDTH_STORAGE_KEY, String(customWidth.value));
};

const resetWidth = () => {
  customWidth.value = null;
  panelWidth.value = 448;
  localStorage.removeItem(WEB_PREVIEW_PANEL_WIDTH_STORAGE_KEY);
};

const reload = () => {
  frameKey.value += 1;
  // 刷新语义包含「重新实测」：目标站点可能刚好调整了防嵌套策略。
  const url = safeUrl.value;
  if (url) {
    probeCache.delete(url);
    void probeEmbeddable(url, { force: true });
  }
};

const syncViewportWidth = () => {
  viewportWidth.value = window.innerWidth;
};

const syncRenderedPanelWidth = () => {
  const width = previewPanel.value?.getBoundingClientRect().width || 0;
  renderedPanelWidth.value = width || null;
};

const observePreviewPanel = async () => {
  await nextTick();
  panelResizeObserver?.disconnect();
  if (!previewPanel.value) {
    renderedPanelWidth.value = null;
    return;
  }
  syncRenderedPanelWidth();
  panelResizeObserver?.observe(previewPanel.value);
};

watch(() => props.visible, (visible) => {
  if (visible) {
    setAutoZoom();
    void observePreviewPanel();
  } else {
    panelResizeObserver?.disconnect();
    renderedPanelWidth.value = null;
  }
});

watch(() => props.url, () => {
  forceShowIframe.value = false;
  frameKey.value += 1;
});

watch(
  [() => props.visible, () => safeUrl.value],
  ([visible, url]) => {
    if (!visible || !url) {
      probeOutcome.value = null;
      probing.value = false;
      return;
    }
    // 关键：先清空结论，让探测期间乐观渲染 iframe。
    // 否则命中经验名单的站点（如 baidu.com）会先弹卡片、实测回来后才变成网页，
    // 正是「先拦、点了又能加载」这一困惑的来源。
    probeOutcome.value = null;
    void probeEmbeddable(url);
  },
  { immediate: true },
);

onMounted(() => {
  loadCustomWidth();
  loadPreviewZoom();
  if (props.visible) setAutoZoom();
  mobileMq = window.matchMedia('(max-width: 639px)');
  syncMobile();
  mobileMq.addEventListener('change', syncMobile);
  window.addEventListener('resize', syncViewportWidth);
  panelResizeObserver = typeof ResizeObserver !== 'undefined'
    ? new ResizeObserver(syncRenderedPanelWidth)
    : null;
  void observePreviewPanel();
});

onUnmounted(() => {
  stopResize();
  mobileMq?.removeEventListener('change', syncMobile);
  window.removeEventListener('resize', syncViewportWidth);
  panelResizeObserver?.disconnect();
});
</script>
