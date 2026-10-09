<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  show?: boolean
  size?: string
  zIndex?: number | string
  /** 是否提供「最大化 / 还原」按钮，默认关闭，避免影响既有 32 处弹框。 */
  maximizable?: boolean
}>(), {
  show: true,
  zIndex: 60,
  maximizable: false
})

const emit = defineEmits(['close', 'update:modelValue'])

/**
 * 最大化状态由 Modal 自己持有：按钮、尺寸、关闭复位本就是一个整体，
 * 拆给使用方会导致别处想复用就得把按钮和逻辑再抄一遍。
 * 默认插槽通过 slot prop 把状态透出去，使用方按需调整内容高度。
 */
const isMaximized = ref(false)

const close = () => {
  emit('close')
  emit('update:modelValue', false)
}

/** 关闭即复位：每次打开都回到普通尺寸，不记忆上次状态。 */
watch(
  () => props.show,
  (visible) => {
    if (!visible) isMaximized.value = false
  },
)

const toggleMaximize = async () => {
  isMaximized.value = !isMaximized.value
  // 容器尺寸变了，但浏览器不会因此派发 resize。使用方多是文件预览类组件
  // （pdf.js / docx 之流），若它们只监听 window resize，画面就会停在旧尺寸。
  // 这里补一次事件，效果与用户手动拖窗口一致。实测若确认库自身用
  // ResizeObserver 重排，应删掉这段，免得白让页面上其他 resize 监听者重算。
  await nextTick()
  requestAnimationFrame(() => window.dispatchEvent(new Event('resize')))
}
</script>

<template>
  <teleport to="body">
    <transition name="modal">
      <div
        v-if="show"
        class="fixed inset-0 flex items-center justify-center p-3 sm:p-4"
        :style="{ zIndex }"
      >
        <!-- Backdrop -->
        <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="close"></div>
        
        <!-- Modal Container -->
        <div 
          class="relative flex max-h-[calc(100dvh-1.5rem)] sm:max-h-[calc(100vh-2rem)] w-full flex-col overflow-hidden rounded-2xl bg-white shadow-2xl transition-all duration-300"
          :class="isMaximized
            ? 'max-w-none h-[calc(100dvh-1.5rem)] sm:h-[calc(100vh-2rem)]'
            : (size || 'max-w-md')"
        >
          <!-- Header -->
          <div class="shrink-0 px-4 py-3.5 sm:px-6 sm:py-4 border-b border-gray-100 flex items-center justify-between bg-gray-50/70">
            <h3 class="text-base sm:text-lg font-bold text-gray-900 truncate pr-2">{{ title }}</h3>
            <div class="flex items-center gap-2 sm:gap-3 shrink-0">
              <slot name="header-extra"></slot>
              <!-- 小屏隐藏：Modal 在窄屏已经是 p-3 + w-full，可用区接近满屏，
                   最大化几乎没有视觉差异，留着按钮只会让人点了觉得没反应 -->
              <button
                v-if="maximizable"
                type="button"
                @click="toggleMaximize"
                class="hidden sm:inline-flex p-1.5 sm:p-2 hover:bg-gray-200/80 active:bg-gray-300/80 rounded-lg text-gray-400 hover:text-gray-700 transition-colors cursor-pointer"
                :title="isMaximized ? '还原窗口' : '最大化预览'"
                :aria-label="isMaximized ? '还原窗口' : '最大化预览'"
              >
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    v-if="isMaximized"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M4 14h6v6m10-6h-6v6M4 10h6V4m10 6h-6V4"
                  />
                  <path
                    v-else
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M4 8V4h4m12 4V4h-4M4 16v4h4m12-4v4h-4"
                  />
                </svg>
              </button>
              <button
                type="button"
                @click="close"
                class="p-1.5 sm:p-2 hover:bg-gray-200/80 active:bg-gray-300/80 rounded-lg text-gray-400 hover:text-gray-700 transition-colors cursor-pointer"
                aria-label="关闭弹窗"
              >
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
          
          <!-- Content -->
          <div class="custom-scrollbar min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
            <!-- maximized 透给使用方，让文件预览这类内容自行决定要不要吃满高度 -->
            <slot :maximized="isMaximized"></slot>
          </div>

          <!-- Footer -->
          <div v-if="$slots.footer" class="shrink-0 border-t border-gray-100 bg-gray-50/90 px-4 py-3 sm:px-6 sm:py-3.5">
            <slot name="footer"></slot>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<style scoped>
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.3s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-active > div:nth-child(2),
.modal-leave-active > div:nth-child(2) {
  transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.3s ease;
}

.modal-enter-from > div:nth-child(2),
.modal-leave-to > div:nth-child(2) {
  transform: scale(0.95) translateY(10px);
  opacity: 0;
}

.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: rgba(0,0,0,0.1);
  border-radius: 10px;
}
</style>
