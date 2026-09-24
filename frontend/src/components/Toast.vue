<template>
  <div
    class="group relative overflow-hidden px-4 py-2.5 rounded-2xl border flex items-center justify-between gap-3 select-none pointer-events-auto transition-all duration-200 backdrop-blur-xl shadow-lg shadow-zinc-950/5 dark:shadow-black/40 max-w-[calc(100vw-2rem)] sm:max-w-md w-auto"
    :class="[
      inline ? '' : 'fixed top-6 left-1/2 -translate-x-1/2 z-[100000]',
      type === 'success'
        ? 'bg-white/95 dark:bg-zinc-900/90 border-emerald-500/25 dark:border-emerald-500/35 text-emerald-950 dark:text-emerald-50 shadow-emerald-500/5 dark:shadow-emerald-950/20'
        : type === 'warning'
          ? 'bg-white/95 dark:bg-zinc-900/90 border-amber-500/25 dark:border-amber-500/35 text-amber-950 dark:text-amber-50 shadow-amber-500/5 dark:shadow-amber-950/20'
          : type === 'error'
            ? 'bg-white/95 dark:bg-zinc-900/90 border-rose-500/25 dark:border-rose-500/35 text-rose-950 dark:text-rose-50 shadow-rose-500/5 dark:shadow-rose-950/20'
            : 'bg-white/95 dark:bg-zinc-900/90 border-sky-500/25 dark:border-sky-500/35 text-sky-950 dark:text-sky-50 shadow-sky-500/5 dark:shadow-sky-950/20',
    ]"
    @mouseenter="pauseTimer"
    @mouseleave="resumeTimer"
  >
    <!-- Left: Status Icon with refined glow -->
    <div
      class="w-5 h-5 rounded-full flex items-center justify-center shrink-0 shadow-sm transition-transform duration-200 group-hover:scale-105"
      :class="[
        type === 'success'
          ? 'bg-emerald-500 text-white shadow-emerald-500/30'
          : type === 'warning'
            ? 'bg-amber-500 text-white shadow-amber-500/30'
            : type === 'error'
              ? 'bg-rose-500 text-white shadow-rose-500/30'
              : 'bg-sky-500 text-white shadow-sky-500/30',
      ]"
    >
      <!-- Success Icon -->
      <svg v-if="type === 'success'" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
      </svg>
      <!-- Error Icon -->
      <svg v-else-if="type === 'error'" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
      <!-- Warning Icon -->
      <svg v-else-if="type === 'warning'" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01" />
      </svg>
      <!-- Info Icon -->
      <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01" />
      </svg>
    </div>

    <!-- Center: Content Area (Support Title + Description or single Message) -->
    <div class="flex-1 min-w-0 pr-1 flex flex-col justify-center">
      <template v-if="title">
        <div class="font-semibold text-xs sm:text-sm tracking-tight text-zinc-900 dark:text-zinc-100 truncate">
          {{ title }}
        </div>
        <div v-if="description" class="text-[11px] sm:text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 leading-relaxed break-words">
          {{ description }}
        </div>
      </template>
      <div
        v-else
        class="font-medium text-xs sm:text-sm tracking-tight leading-snug break-words"
        :class="[
          type === 'success'
            ? 'text-emerald-950 dark:text-emerald-100'
            : type === 'warning'
              ? 'text-amber-950 dark:text-amber-100'
              : type === 'error'
                ? 'text-rose-950 dark:text-rose-100'
                : 'text-sky-950 dark:text-sky-100',
        ]"
      >
        {{ message }}
      </div>
    </div>

    <!-- Right: Optional Action Button -->
    <button
      v-if="action"
      type="button"
      @click.stop="handleAction"
      class="shrink-0 px-2.5 py-1 text-xs font-medium rounded-lg bg-zinc-100 hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700 text-zinc-800 dark:text-zinc-200 transition-colors focus:outline-none"
    >
      {{ action.label }}
    </button>

    <!-- Right: Close Button -->
    <button
      type="button"
      @click.stop="close"
      class="shrink-0 p-1 -mr-1 rounded-full text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors focus:outline-none"
      aria-label="关闭"
    >
      <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
      </svg>
    </button>

    <!-- Bottom: Subtle Progress Bar -->
    <div
      v-if="activeDuration > 0"
      class="absolute bottom-0 left-3 right-3 h-[2px] rounded-full overflow-hidden bg-zinc-200/40 dark:bg-zinc-800/60"
    >
      <div
        class="h-full rounded-full transition-all ease-linear"
        :class="[
          type === 'success'
            ? 'bg-emerald-500/80'
            : type === 'warning'
              ? 'bg-amber-500/80'
              : type === 'error'
                ? 'bg-rose-500/80'
                : 'bg-sky-500/80',
        ]"
        :style="{
          width: `${progressPercent}%`,
          transitionDuration: isPaused ? '0ms' : '100ms',
        }"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

interface Props {
  message?: string
  title?: string
  description?: string
  type?: 'success' | 'error' | 'warning' | 'info'
  duration?: number
  action?: {
    label: string
    onClick: () => void
  }
  /** 在 ToastContainer 内堆叠时关闭自身 fixed 定位 */
  inline?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  message: '',
  type: 'info',
  inline: false,
})

const emit = defineEmits<{
  close: []
}>()

// 成功与普通提示 2000ms（2秒），警告与报错 3500ms（3.5秒）
const resolveDuration = () => {
  if (props.duration !== undefined) return props.duration
  return props.type === 'error' || props.type === 'warning' ? 3500 : 2000
}

const activeDuration = resolveDuration()
const remainingTime = ref(activeDuration)
const progressPercent = ref(100)
const isPaused = ref(false)

let timer: ReturnType<typeof setTimeout> | null = null
let progressInterval: ReturnType<typeof setInterval> | null = null
let startTime = 0

const startTimer = () => {
  if (remainingTime.value <= 0) return
  startTime = Date.now()
  isPaused.value = false
  timer = setTimeout(() => {
    close()
  }, remainingTime.value)

  if (progressInterval) clearInterval(progressInterval)
  progressInterval = setInterval(() => {
    if (isPaused.value) return
    const elapsedSinceStart = Date.now() - startTime
    const currentRemaining = Math.max(0, remainingTime.value - elapsedSinceStart)
    progressPercent.value = Math.max(0, (currentRemaining / (activeDuration || 1)) * 100)
  }, 100)
}

const pauseTimer = () => {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  isPaused.value = true
  const elapsed = Date.now() - startTime
  remainingTime.value = Math.max(0, remainingTime.value - elapsed)
}

const resumeTimer = () => {
  if (activeDuration <= 0 || remainingTime.value <= 0) return
  startTimer()
}

const handleAction = () => {
  props.action?.onClick()
  close()
}

const close = () => {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  if (progressInterval) {
    clearInterval(progressInterval)
    progressInterval = null
  }
  emit('close')
}

onMounted(() => {
  if (activeDuration > 0) {
    startTimer()
  }
})

onUnmounted(() => {
  if (timer) clearTimeout(timer)
  if (progressInterval) clearInterval(progressInterval)
})
</script>
