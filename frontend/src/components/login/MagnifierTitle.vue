<script setup lang="ts">
/**
 * 放大镜标题：鼠标在标题文字上移动时，用一个圆形镜片放大所指的字形，
 * 形成"探索"的手感。
 *
 * 实现要点：镜片内展示的是同一标题的副本，副本按 `zoom` 缩放，
 * 且 `transform-origin` 与 `clip-path` 圆心共用同一组镜片坐标，
 * 因此指针所在字形在镜片内保持原位、四周向外扩散——即真实放大镜的光学表现。
 * 原始 `<h1>` 全程静止不动，只有被裁剪的副本层在缩放。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
    text: string
    tone?: 'dark' | 'light'
    zoom?: number
}>(), {
    tone: 'dark',
    zoom: 1.6,
})

const wrapRef = ref<HTMLElement | null>(null)
const lensActive = ref(false)
const reducedMotion = ref(false)
let motionMediaQuery: MediaQueryList | null = null

const titleClass = computed(() => [
    'text-5xl xl:text-7xl font-bold tracking-tighter drop-shadow-2xl',
    props.tone === 'light' ? 'text-slate-900' : 'text-white',
])

// 镜片内的玻璃底：必须完全不透明。半透明会让底下未放大的原始文字透出来，
// 与放大后的字形叠成重影——真实放大镜里只会看到放大后的像。
// 镜片的"玻璃感"由光环的内阴影与高光承担，不靠透明度。
const glassBackdrop = computed(() => (props.tone === 'light'
    ? 'radial-gradient(circle at 50% 50%, rgba(248,250,252,1) 0%, rgba(226,232,240,1) 100%)'
    : 'radial-gradient(circle at 50% 50%, rgba(10,22,44,1) 0%, rgba(2,6,23,1) 100%)'))

const ringClass = computed(() => (props.tone === 'light'
    ? 'border-slate-900/20 shadow-[0_0_22px_rgba(37,99,235,0.28),inset_0_0_18px_rgba(15,23,42,0.08)]'
    : 'border-white/45 shadow-[0_0_28px_rgba(96,165,250,0.42),inset_0_0_22px_rgba(255,255,255,0.16)]'))

const highlightClass = computed(() => (props.tone === 'light' ? 'bg-white/70' : 'bg-white/25'))

// 指针坐标直接写进 CSS 变量：不经过 Vue 响应式，避免每帧重渲染轮播子树
const handleMouseMove = (event: MouseEvent) => {
    if (reducedMotion.value) return
    const wrap = wrapRef.value
    if (!wrap) return
    const rect = wrap.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    const x = Math.min(rect.width, Math.max(0, event.clientX - rect.left))
    const y = Math.min(rect.height, Math.max(0, event.clientY - rect.top))
    wrap.style.setProperty('--lens-x', `${x}px`)
    wrap.style.setProperty('--lens-y', `${y}px`)
}

const resetLens = () => {
    lensActive.value = false
    const wrap = wrapRef.value
    if (!wrap) return
    wrap.style.setProperty('--lens-x', '50%')
    wrap.style.setProperty('--lens-y', '50%')
}

// 轮播切帧后标题内容会变，避免残留上一帧的镜片状态
watch(() => props.text, resetLens)

const handleMotionPreferenceChange = (event: MediaQueryListEvent) => {
    reducedMotion.value = event.matches
    if (event.matches) resetLens()
}

onMounted(() => {
    motionMediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotion.value = motionMediaQuery.matches
    motionMediaQuery.addEventListener?.('change', handleMotionPreferenceChange)
})

onUnmounted(() => {
    motionMediaQuery?.removeEventListener?.('change', handleMotionPreferenceChange)
})
</script>

<template>
    <div
        ref="wrapRef"
        class="magnifier-title relative inline-block align-top"
        @mouseenter="lensActive = true"
        @mousemove="handleMouseMove"
        @mouseleave="resetLens"
    >
        <!-- 原始标题：全程静止，只作为镜片外的正常文字 -->
        <h1 :class="titleClass">{{ text }}</h1>

        <template v-if="!reducedMotion">
            <!-- 镜片层：裁剪成圆形，内含缩放后的标题副本 -->
            <div
                aria-hidden="true"
                class="pointer-events-none absolute inset-0 z-10 transition-opacity duration-200"
                :class="lensActive ? 'opacity-100' : 'opacity-0'"
                :style="{ clipPath: `circle(var(--lens-r) at var(--lens-x) var(--lens-y))` }"
            >
                <div
                    class="absolute"
                    :style="{ inset: 'calc(var(--lens-r) * -1)', background: glassBackdrop }"
                ></div>
                <h1
                    :class="titleClass"
                    class="absolute inset-0"
                    :style="{ transform: `scale(${zoom})`, transformOrigin: 'var(--lens-x) var(--lens-y)' }"
                >{{ text }}</h1>
            </div>

            <!-- 镜片光环 -->
            <div
                aria-hidden="true"
                class="pointer-events-none absolute z-20 -translate-x-1/2 -translate-y-1/2 rounded-full border transition-opacity duration-200"
                :class="[ringClass, lensActive ? 'opacity-100' : 'opacity-0']"
                :style="{
                    left: 'var(--lens-x)',
                    top: 'var(--lens-y)',
                    width: 'calc(var(--lens-r) * 2)',
                    height: 'calc(var(--lens-r) * 2)',
                }"
            >
                <span
                    aria-hidden="true"
                    class="absolute left-[24%] top-[13%] h-[16%] w-[38%] rounded-full blur-[8px]"
                    :class="highlightClass"
                ></span>
            </div>
        </template>
    </div>
</template>

<style scoped>
.magnifier-title {
    --lens-r: 40px;
    --lens-x: 50%;
    --lens-y: 50%;
}

@media (min-width: 1024px) {
    .magnifier-title {
        --lens-r: 52px;
    }
}

@media (min-width: 1280px) {
    .magnifier-title {
        --lens-r: 60px;
    }
}
</style>
