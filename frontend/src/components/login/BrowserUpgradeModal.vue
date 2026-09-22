<script setup lang="ts">
import { computed } from 'vue'
import {
  ExclamationTriangleIcon,
  XMarkIcon,
  ArrowTopRightOnSquareIcon,
  ShieldCheckIcon
} from '@heroicons/vue/24/outline'
import type { BrowserDetectResult } from '@/utils/browserDetect'
import { RECOMMENDED_CHROME_URL, RECOMMENDED_EDGE_URL } from '@/utils/browserDetect'

const props = defineProps<{
  show: boolean
  detectResult: BrowserDetectResult
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'dismiss'): void
}>()

const isIE = computed(() => props.detectResult?.name === 'Internet Explorer')

const handleDismiss = () => {
  emit('dismiss')
  emit('close')
}

const openChromeDownload = () => {
  window.open(props.detectResult?.downloadUrl || RECOMMENDED_CHROME_URL, '_blank', 'noopener,noreferrer')
}

const openEdgeDownload = () => {
  window.open(RECOMMENDED_EDGE_URL, '_blank', 'noopener,noreferrer')
}
</script>

<template>
  <div
    v-if="show"
    class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/65 backdrop-blur-sm animate-fade-in"
    role="dialog"
    aria-modal="true"
    aria-labelledby="browser-upgrade-title"
  >
    <!-- Modal Card -->
    <div class="relative w-full max-w-lg bg-white rounded-2xl shadow-2xl border border-slate-100 overflow-hidden animate-fade-slide-up">
      <!-- Top Decorative Accent Strip -->
      <div class="h-1.5 w-full bg-gradient-to-r from-amber-500 via-orange-500 to-blue-600"></div>

      <!-- Close Button (only when not strictly blocking) -->
      <button
        v-if="!isIE"
        type="button"
        @click="handleDismiss"
        class="absolute top-4 right-4 p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
        aria-label="关闭提示"
      >
        <XMarkIcon class="w-5 h-5" />
      </button>

      <div class="p-6 sm:p-7">
        <!-- Header with Badge -->
        <div class="flex items-start gap-4">
          <div class="w-12 h-12 rounded-xl bg-amber-50 border border-amber-200/60 flex items-center justify-center flex-shrink-0 text-amber-600 shadow-sm">
            <ExclamationTriangleIcon class="w-6 h-6" />
          </div>
          <div class="flex-1 min-w-0 pr-4">
            <h3 id="browser-upgrade-title" class="text-lg font-bold text-slate-900 tracking-tight">
              {{ isIE ? '浏览器不受支持提示' : '建议升级您的浏览器' }}
            </h3>
            <p class="text-xs text-slate-500 mt-1 leading-relaxed">
              平台使用了前沿 Web 技术与流式交互引擎。为了保障您的访问速度、界面排版与数据安全，建议使用现代主流浏览器。
            </p>
          </div>
        </div>

        <!-- Current Detected Environment -->
        <div class="mt-5 p-3.5 bg-slate-50 rounded-xl border border-slate-200/80 flex items-center justify-between gap-3">
          <div class="flex items-center gap-2.5 min-w-0">
            <div class="w-2 h-2 rounded-full" :class="isIE ? 'bg-red-500' : 'bg-amber-500'"></div>
            <div class="text-xs font-medium text-slate-700 truncate">
              当前检测：<span class="font-bold text-slate-900">{{ detectResult.name }}</span>
              <span v-if="detectResult.version" class="text-slate-500 font-mono ml-1">v{{ detectResult.version }}</span>
            </div>
          </div>
          <span
            class="px-2 py-0.5 text-[11px] font-semibold rounded-full flex-shrink-0"
            :class="isIE ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'"
          >
            {{ isIE ? '已淘汰' : '版本偏低' }}
          </span>
        </div>

        <!-- Recommendation Options -->
        <div class="mt-5 space-y-3">
          <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">
            推荐现代化浏览器
          </div>

          <!-- Chrome (Primary Recommendation) -->
          <div class="group p-3.5 rounded-xl border border-blue-200/90 bg-gradient-to-r from-blue-50/50 to-indigo-50/30 hover:border-blue-300 transition-all flex items-center justify-between gap-3">
            <div class="flex items-center gap-3 min-w-0">
              <!-- Chrome Logo Representation -->
              <div class="w-9 h-9 rounded-lg bg-white border border-slate-100 shadow-sm flex items-center justify-center flex-shrink-0">
                <svg class="w-6 h-6" viewBox="0 0 24 24" fill="none">
                  <circle cx="12" cy="12" r="10" fill="#4285F4"/>
                  <circle cx="12" cy="12" r="4" fill="#FFFFFF"/>
                  <path d="M12 2C15.866 2 19.141 4.172 20.785 7.373L15.464 16.59C14.544 18.183 12.871 19.16 11.026 19.16C9.181 19.16 7.508 18.183 6.588 16.59L12 2Z" fill="#EA4335" fill-opacity="0.2"/>
                  <circle cx="12" cy="12" r="3.2" fill="#1A73E8"/>
                </svg>
              </div>
              <div class="min-w-0">
                <div class="flex items-center gap-1.5">
                  <span class="text-sm font-bold text-slate-900">Google Chrome</span>
                  <span class="px-1.5 py-0.2 bg-blue-100 text-blue-700 text-[10px] font-medium rounded">推荐</span>
                </div>
                <p class="text-[11px] text-slate-500 truncate mt-0.5">极速稳定，全面兼容平台所有 AI 协作与可视化能力</p>
              </div>
            </div>
            <button
              type="button"
              @click="openChromeDownload"
              class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-semibold shadow-sm transition-all flex items-center gap-1 flex-shrink-0 active:scale-95"
            >
              <span>前往下载</span>
              <ArrowTopRightOnSquareIcon class="w-3.5 h-3.5" />
            </button>
          </div>

          <!-- Edge (Secondary Recommendation) -->
          <div class="p-3 rounded-xl border border-slate-200 bg-white hover:border-slate-300 transition-all flex items-center justify-between gap-3">
            <div class="flex items-center gap-3 min-w-0">
              <div class="w-8 h-8 rounded-lg bg-slate-50 border border-slate-100 flex items-center justify-center flex-shrink-0 text-slate-600">
                <ShieldCheckIcon class="w-5 h-5 text-teal-600" />
              </div>
              <div class="min-w-0">
                <div class="text-xs font-bold text-slate-800">Microsoft Edge</div>
                <p class="text-[11px] text-slate-400 truncate">Windows 10/11 原生预装，Chromium 内核保障体验</p>
              </div>
            </div>
            <button
              type="button"
              @click="openEdgeDownload"
              class="px-2.5 py-1 text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-lg text-xs font-medium transition-all flex items-center gap-1 flex-shrink-0"
            >
              <span>官方主页</span>
              <ArrowTopRightOnSquareIcon class="w-3 h-3" />
            </button>
          </div>
        </div>

        <!-- Action Footer -->
        <div class="mt-6 pt-4 border-t border-slate-100 flex items-center justify-end gap-3">
          <button
            v-if="!isIE"
            type="button"
            @click="handleDismiss"
            class="px-4 py-2 text-xs font-semibold text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
          >
            我已知晓，继续访问
          </button>
          <button
            type="button"
            @click="openChromeDownload"
            class="px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold shadow-md shadow-blue-600/15 transition-all flex items-center gap-1.5 active:scale-98"
          >
            <span>立即下载 Chrome</span>
            <ArrowTopRightOnSquareIcon class="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.animate-fade-in {
  animation: fadeIn 0.25s ease-out;
}
@keyframes fadeIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.animate-fade-slide-up {
  animation: fadeSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes fadeSlideUp {
  from {
    opacity: 0;
    transform: translateY(16px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
</style>
