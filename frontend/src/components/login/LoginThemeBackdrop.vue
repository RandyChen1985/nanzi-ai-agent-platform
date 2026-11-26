<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import type { LoginThemeId } from '@/constants/loginThemes'
import { renderLoginThemeFrame } from '@/utils/loginThemeRenderers'

const props = withDefaults(
  defineProps<{
    theme: LoginThemeId
    activeSatellite?: number
    panelWidth?: number
  }>(),
  {
    activeSatellite: 0,
    panelWidth: 460,
  },
)

const emit = defineEmits<{
  (e: 'pointer-change', payload: { x: number; y: number }): void
}>()

const canvasRef = ref<HTMLCanvasElement | null>(null)
const reducedMotion = ref(false)
let motionMediaQuery: MediaQueryList | null = null
let rafId: number | null = null
let width = 0
let height = 0

const ptr = { x: 0.4, y: 0.5, tx: 0.4, ty: 0.5 }

const drawOnce = (timestamp: number) => {
  const canvas = canvasRef.value
  if (!canvas || !width || !height) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  renderLoginThemeFrame(
    ctx,
    props.theme,
    width,
    height,
    reducedMotion.value ? 0 : timestamp,
    ptr.x,
    ptr.y,
    {
      panelWidth: props.panelWidth,
      activeSatellite: props.activeSatellite,
    },
  )
}

const resize = () => {
  const canvas = canvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const nextW = Math.max(1, Math.floor(rect.width || window.innerWidth))
  const nextH = Math.max(1, Math.floor(rect.height || window.innerHeight))
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  width = nextW
  height = nextH
  canvas.width = Math.max(1, Math.floor(nextW * dpr))
  canvas.height = Math.max(1, Math.floor(nextH * dpr))
  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  }
  drawOnce(performance.now())
}

const stopLoop = () => {
  if (rafId !== null) {
    cancelAnimationFrame(rafId)
    rafId = null
  }
}

const startLoop = () => {
  stopLoop()
  if (reducedMotion.value) {
    drawOnce(0)
    return
  }
  const tick = (t: number) => {
    ptr.x += (ptr.tx - ptr.x) * 0.045
    ptr.y += (ptr.ty - ptr.y) * 0.045
    drawOnce(t)
    rafId = requestAnimationFrame(tick)
  }
  rafId = requestAnimationFrame(tick)
}

const TELEMETRY_THEMES = new Set<LoginThemeId>(['f1', 'f2', 'f4', 'h', 'j'])
let lastPointerEmitAt = 0

const handlePointerMove = (event: MouseEvent) => {
  if (reducedMotion.value || !width || !height) return
  ptr.tx = Math.min(1, Math.max(0, event.clientX / width))
  ptr.ty = Math.min(1, Math.max(0, event.clientY / height))
  // Canvas 自身仍按原生帧率平滑插值；向父组件上报坐标读数做 80ms 节流，
  // 避免高刷屏晃动鼠标导致整棵登录表单树频繁重渲染。
  if (!TELEMETRY_THEMES.has(props.theme)) return
  const now = performance.now()
  if (now - lastPointerEmitAt >= 80) {
    lastPointerEmitAt = now
    emit('pointer-change', { x: ptr.tx, y: ptr.ty })
  }
}

const handleMotionChange = (event: MediaQueryListEvent) => {
  reducedMotion.value = event.matches
  startLoop()
}

watch(
  () => [props.theme, props.activeSatellite, props.panelWidth],
  () => {
    if (reducedMotion.value) drawOnce(0)
  },
)

onMounted(() => {
  motionMediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  reducedMotion.value = motionMediaQuery.matches
  motionMediaQuery.addEventListener?.('change', handleMotionChange)
  resize()
  window.addEventListener('resize', resize)
  window.addEventListener('mousemove', handlePointerMove, { passive: true })
  startLoop()
})

onUnmounted(() => {
  stopLoop()
  window.removeEventListener('resize', resize)
  window.removeEventListener('mousemove', handlePointerMove)
  motionMediaQuery?.removeEventListener?.('change', handleMotionChange)
})
</script>

<template>
  <div class="login-backdrop pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
    <canvas ref="canvasRef" class="block h-full w-full" />
    <!-- 方案 I 专属：建筑长虹竖纹压花玻璃折射覆盖层 -->
    <div v-if="theme === 'i'" class="fluted-glass-overlay absolute inset-0" />
  </div>
</template>

<style scoped>
.fluted-glass-overlay {
  background-image: repeating-linear-gradient(
    90deg,
    rgba(255, 255, 255, 0.14) 0px,
    rgba(255, 255, 255, 0.02) 4px,
    rgba(7, 13, 28, 0.22) 10px,
    rgba(255, 255, 255, 0.08) 18px,
    rgba(255, 255, 255, 0.14) 22px
  );
  backdrop-filter: blur(22px) saturate(135%);
  -webkit-backdrop-filter: blur(22px) saturate(135%);
}
</style>
