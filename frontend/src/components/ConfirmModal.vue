<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

interface Props {
  title: string
  message?: string
  confirmText?: string
  cancelText?: string
  type?: 'danger' | 'primary' | 'warning'
  loading?: boolean
  details?: string[]
  detailsLabel?: string
  detailsLimit?: number
}

const props = withDefaults(defineProps<Props>(), {
  confirmText: '确认',
  cancelText: '取消',
  type: 'danger',
  loading: false,
  details: () => [],
  detailsLabel: '影响配置',
  detailsLimit: 5
})

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()

const confirmButtonRef = ref<HTMLButtonElement | null>(null)
const showAllDetails = ref(false)
const visibleDetails = computed(() => props.details?.slice(0, props.detailsLimit) || [])
const hasHiddenDetails = computed(() => (props.details?.length || 0) > props.detailsLimit)

const toggleDetails = () => {
  showAllDetails.value = !showAllDetails.value
}

const handleKeydown = (e: KeyboardEvent) => {
  if (props.loading) return
  if (e.key === 'Enter') {
    e.preventDefault()
    e.stopPropagation()
    emit('confirm')
  } else if (e.key === 'Escape') {
    e.preventDefault()
    e.stopPropagation()
    emit('cancel')
  }
}

onMounted(() => {
  confirmButtonRef.value?.focus()
  document.addEventListener('keydown', handleKeydown, true)
})

onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown, true)
})
</script>

<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-[13000] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      @click.self="!loading && $emit('cancel')"
    >
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-sm w-full overflow-hidden scale-100 transition-transform duration-200">
        <div class="p-6 text-center">
          <div class="mx-auto flex items-center justify-center h-12 w-12 rounded-full mb-4" :class="{
            'bg-red-100 text-red-600': type === 'danger',
            'bg-primary/10 text-blue-600': type === 'primary',
            'bg-yellow-100 text-yellow-600': type === 'warning'
          }">
            <svg v-if="type === 'danger'" class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
            <svg v-else-if="type === 'warning'" class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
            <svg v-else class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
          </div>
          <h3 class="text-lg leading-6 font-medium text-gray-900 dark:text-gray-100">{{ title }}</h3>
          <!-- slot 优先：有 slot 内容时左对齐展示结构化信息；否则回退到 message 文本 -->
          <div v-if="$slots.default" class="mt-3 text-left">
            <slot />
          </div>
          <p v-else-if="message" class="text-sm text-gray-500 dark:text-gray-400 mt-2 whitespace-pre-line">{{ message }}</p>
          <div v-if="details?.length" class="mt-3 text-left">
            <p class="mb-1 text-xs font-semibold text-gray-500 dark:text-gray-400">
              {{ detailsLabel }}（{{ details.length }}）
            </p>
            <ul class="max-h-36 space-y-1 overflow-y-auto rounded-lg bg-gray-50 p-2 text-xs text-gray-600 dark:bg-gray-900/40 dark:text-gray-300">
              <li v-for="(detail, index) in (showAllDetails ? details : visibleDetails)" :key="`${index}-${detail}`" class="truncate" :title="detail">
                {{ detail }}
              </li>
            </ul>
            <button
              v-if="hasHiddenDetails"
              type="button"
              class="mt-1 text-xs font-medium text-primary hover:underline"
              @click="toggleDetails"
            >
              {{ showAllDetails ? '收起列表' : `还有 ${details.length - detailsLimit} 个，展开查看` }}
            </button>
          </div>
        </div>
        <div class="bg-gray-50 dark:bg-gray-900/50 px-4 py-3 sm:px-6 sm:flex sm:flex-row-reverse">
          <button
            ref="confirmButtonRef"
            @click="!loading && $emit('confirm')"
            type="button"
            class="w-full inline-flex justify-center items-center gap-1.5 rounded-md border border-transparent shadow-sm px-4 py-2 text-base font-medium text-white sm:ml-3 sm:w-auto sm:text-sm transition-all focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-60 disabled:cursor-not-allowed"
            :class="{
              'bg-red-600 hover:bg-red-700 focus:ring-red-500': type === 'danger',
              'bg-primary hover:bg-primary-dark focus:ring-primary': type === 'primary',
              'bg-yellow-600 hover:bg-yellow-700 focus:ring-yellow-500': type === 'warning'
            }"
            :disabled="loading"
          >
            <!-- SVG Loading Spinner -->
            <svg v-if="loading" class="animate-spin -ml-1 mr-1 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span>{{ loading ? '处理中...' : confirmText }}</span>
          </button>
          <button 
            @click="!loading && $emit('cancel')" 
            type="button" 
            class="mt-3 w-full inline-flex justify-center rounded-md border border-gray-300 dark:border-gray-600 shadow-sm px-4 py-2 bg-white dark:bg-gray-800 text-base font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 sm:mt-0 sm:ml-3 sm:w-auto sm:text-sm focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="loading"
          >
            {{ cancelText }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
