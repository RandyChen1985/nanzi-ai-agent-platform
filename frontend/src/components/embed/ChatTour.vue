<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { TourStep } from './tourSteps'

/**
 * 聚光灯式功能引导。
 *
 * 只负责「画」和「定位」：四块遮罩围出镂空，气泡贴在目标旁边。
 * 播放到第几步、什么时候算完成，全部由 useEmbedTour 决定，
 * 这样换页面复用时不必改组件。
 */

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    steps: TourStep[]
    initialIndex?: number
  }>(),
  { initialIndex: 0 },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'change', index: number): void
  (e: 'enter', index: number, step: TourStep): void
  (e: 'leave', index: number, step: TourStep): void
  (e: 'finish'): void
  (e: 'skip'): void
  (e: 'missing-target', index: number): void
}>()

interface Rect {
  top: number
  left: number
  width: number
  height: number
}

const index = ref(props.initialIndex)
const viewport = ref({ w: window.innerWidth, h: window.innerHeight })
const targetRect = ref<Rect | null>(null)
const bubbleStyle = ref<Record<string, string>>({})
const arrowStyle = ref<Record<string, string>>({})
const openUp = ref(false)

const BUBBLE_WIDTH = 320
const BUBBLE_MARGIN = 12

const step = computed(() => props.steps[index.value] || null)
const progress = computed(() => `${Math.min(index.value + 1, props.steps.length)} / ${props.steps.length}`)
const isLast = computed(() => index.value >= props.steps.length - 1)

let rafId = 0
let scrollTimer: number | undefined
let missingTimer: number | undefined
let missingEmittedFor = -1
/**
 * 目标缺失要延迟判定：沙箱步骤需要父组件先点开上下文弹层，
 * 首次测量必然落空；450ms 内目标出现（refresh）就取消，避免误跳过该步。
 */
const MISSING_GRACE_MS = 450

const resolveTarget = (): HTMLElement | null => {
  if (!step.value) return null
  try {
    return document.querySelector<HTMLElement>(step.value.selector)
  } catch {
    return null
  }
}

const masks = computed(() => {
  const rect = targetRect.value
  const vw = viewport.value.w
  const vh = viewport.value.h
  if (!rect) {
    return [{ top: '0px', left: '0px', width: `${vw}px`, height: `${vh}px` }]
  }
  return [
    { top: '0px', left: '0px', width: `${vw}px`, height: `${rect.top}px` },
    {
      top: `${rect.top + rect.height}px`,
      left: '0px',
      width: `${vw}px`,
      height: `${Math.max(0, vh - rect.top - rect.height)}px`,
    },
    { top: `${rect.top}px`, left: '0px', width: `${rect.left}px`, height: `${rect.height}px` },
    {
      top: `${rect.top}px`,
      left: `${rect.left + rect.width}px`,
      width: `${Math.max(0, vw - rect.left - rect.width)}px`,
      height: `${rect.height}px`,
    },
  ]
})

const highlightStyle = computed(() => {
  const rect = targetRect.value
  if (!rect) return {}
  return {
    top: `${rect.top}px`,
    left: `${rect.left}px`,
    width: `${rect.width}px`,
    height: `${rect.height}px`,
  }
})

const positionBubble = () => {
  const rect = targetRect.value
  if (!rect) return
  const vw = window.innerWidth
  const vh = window.innerHeight
  const width = Math.min(BUBBLE_WIDTH, vw - 24)
  const estimatedHeight = 168
  const spaceBelow = vh - (rect.top + rect.height)
  const flip = spaceBelow < estimatedHeight + BUBBLE_MARGIN && rect.top > estimatedHeight + BUBBLE_MARGIN
  openUp.value = flip

  const top = flip ? rect.top - BUBBLE_MARGIN : rect.top + rect.height + BUBBLE_MARGIN
  const left = Math.max(
    12,
    Math.min(rect.left + rect.width / 2 - width / 2, vw - width - 12),
  )

  bubbleStyle.value = {
    top: `${Math.max(12, top)}px`,
    left: `${left}px`,
    width: `${width}px`,
    ...(flip ? { transform: 'translateY(-100%)' } : {}),
  }

  const arrowLeft = Math.max(14, Math.min(rect.left + rect.width / 2 - left - 5, width - 24))
  arrowStyle.value = {
    left: `${arrowLeft}px`,
    ...(flip ? { bottom: '-5px' } : { top: '-5px' }),
  }
}

const measure = () => {
  if (!props.modelValue) return
  const el = resolveTarget()
  if (!el) {
    targetRect.value = null
    if (missingEmittedFor === index.value) return
    window.clearTimeout(missingTimer)
    missingTimer = window.setTimeout(() => {
      if (!props.modelValue) return
      if (resolveTarget()) return
      missingEmittedFor = index.value
      emit('missing-target', index.value)
    }, MISSING_GRACE_MS)
    return
  }
  window.clearTimeout(missingTimer)
  missingEmittedFor = -1
  viewport.value = { w: window.innerWidth, h: window.innerHeight }
  const rect = el.getBoundingClientRect()
  const pad = 6
  targetRect.value = {
    top: Math.max(0, rect.top - pad),
    left: Math.max(0, rect.left - pad),
    width: rect.width + pad * 2,
    height: rect.height + pad * 2,
  }
  positionBubble()
}

const scheduleMeasure = () => {
  if (rafId) return
  rafId = window.requestAnimationFrame(() => {
    rafId = 0
    measure()
  })
}

const onScroll = () => {
  if (!props.modelValue) return
  window.clearTimeout(scrollTimer)
  scrollTimer = window.setTimeout(scheduleMeasure, 40)
}

const onResize = () => {
  if (props.modelValue) scheduleMeasure()
}

const close = () => emit('update:modelValue', false)

const goNext = () => {
  if (isLast.value) {
    emit('finish')
    close()
    return
  }
  index.value += 1
}

const goPrev = () => {
  if (index.value > 0) index.value -= 1
}

const goTo = (target: number) => {
  if (target >= 0 && target < props.steps.length) index.value = target
}

const isInteractiveTarget = (target: EventTarget | null) => {
  const el = target as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'BUTTON' || tag === 'SELECT' || el.isContentEditable
}

const onKeydown = (event: KeyboardEvent) => {
  if (!props.modelValue) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('skip')
    close()
    return
  }
  // 焦点还在输入框 / 气泡按钮上时，Enter 归它们自己，避免「发消息 + 下一步」同时发生
  if (isInteractiveTarget(event.target)) return
  if (event.key === 'ArrowRight' || event.key === 'Enter') {
    event.preventDefault()
    goNext()
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault()
    goPrev()
  }
}

const focusTarget = async () => {
  await nextTick()
  const el = resolveTarget()
  el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  window.setTimeout(measure, 220)
}

const enterStep = async () => {
  const current = step.value
  if (!current) return
  emit('enter', index.value, current)
  await focusTarget()
}

/** 父组件在异步展开弹层后调用：只重新测量，不再触发 enter（避免递归） */
const refresh = async () => {
  await focusTarget()
}

defineExpose({ refresh, goNext, goPrev })

watch(
  () => props.modelValue,
  (visible, wasVisible) => {
    if (visible) {
      ;(document.activeElement as HTMLElement | null)?.blur?.()
      index.value = props.initialIndex
      missingEmittedFor = -1
      void enterStep()
    } else if (wasVisible && step.value) {
      emit('leave', index.value, step.value)
    }
  },
  { immediate: true },
)

watch(index, (value, oldValue) => {
  const previous = props.steps[oldValue]
  if (previous) emit('leave', oldValue, previous)
  emit('change', value)
  missingEmittedFor = -1
  void enterStep()
})

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('scroll', onScroll, true)
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', onResize)
  window.clearTimeout(scrollTimer)
  window.clearTimeout(missingTimer)
  if (rafId) window.cancelAnimationFrame(rafId)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue && step && targetRect" class="fixed inset-0" style="z-index: 15000" @click.stop>
      <!-- 四块遮罩围出镂空：点击不关闭，避免误触中断 -->
      <div
        v-for="(mask, maskIndex) in masks"
        :key="maskIndex"
        class="fixed bg-black/60"
        :style="mask"
        aria-hidden="true"
      />

      <!-- 高亮框：不接收指针事件，目标本身仍可被遮罩保护 -->
      <div
        class="pointer-events-none fixed rounded-lg border-2 border-primary"
        :style="highlightStyle"
        aria-hidden="true"
      />

      <div
        class="fixed rounded-xl border border-gray-100 bg-white p-4 shadow-2xl transition-all duration-150 dark:border-gray-700 dark:bg-gray-800"
        :style="bubbleStyle"
        role="dialog"
        aria-modal="true"
        :aria-label="`功能引导：${step.title}`"
      >
        <span
          class="absolute h-2.5 w-2.5 rotate-45 border border-gray-100 bg-white dark:border-gray-700 dark:bg-gray-800"
          :style="arrowStyle"
          aria-hidden="true"
        />
        <h3 class="text-sm font-black text-gray-900 dark:text-gray-100">{{ step.title }}</h3>
        <p class="mt-1.5 text-xs leading-relaxed text-gray-600 dark:text-gray-300">{{ step.body }}</p>

        <div class="mt-3 flex items-center gap-1.5">
          <button
            type="button"
            class="rounded-lg px-2 py-1 text-xs font-bold text-gray-400 transition-colors hover:bg-gray-50 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-200"
            @click="emit('skip'); close()"
          >
            跳过引导
          </button>
          <span class="ml-auto text-[11px] font-mono text-gray-400">{{ progress }}</span>
          <button
            type="button"
            class="rounded-lg border border-gray-200 px-2.5 py-1 text-xs font-bold text-gray-600 transition-colors hover:bg-gray-50 disabled:opacity-40 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
            :disabled="index === 0"
            @click="goPrev"
          >
            上一步
          </button>
          <button
            type="button"
            class="rounded-lg bg-primary px-3 py-1 text-xs font-bold text-white shadow-sm transition-colors hover:bg-primary-hover"
            @click="goNext"
          >
            {{ isLast ? '完成' : '下一步' }}
          </button>
        </div>

        <div class="mt-2 flex items-center gap-1" aria-hidden="true">
          <button
            v-for="(item, dotIndex) in steps"
            :key="item.id"
            type="button"
            class="h-1.5 rounded-full transition-all"
            :class="dotIndex === index ? 'w-4 bg-primary' : dotIndex < index ? 'w-1.5 bg-primary/40' : 'w-1.5 bg-gray-200 dark:bg-gray-600'"
            :aria-label="`第 ${dotIndex + 1} 步`"
            @click="goTo(dotIndex)"
          />
        </div>
      </div>
    </div>
  </Teleport>
</template>
