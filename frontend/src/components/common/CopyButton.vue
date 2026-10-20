<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { CheckIcon, ClipboardDocumentIcon } from '@heroicons/vue/24/outline'
import { copyToClipboard } from '../../utils/clipboard'

const props = withDefaults(defineProps<{
  text: string
  label?: string
  dark?: boolean
}>(), {
  label: '内容',
  dark: false,
})

const copied = ref(false)
const failed = ref(false)
let resetTimer: ReturnType<typeof setTimeout> | null = null

const clearResetTimer = () => {
  if (resetTimer !== null) {
    clearTimeout(resetTimer)
    resetTimer = null
  }
}

const handleCopy = async () => {
  if (!props.text) return
  clearResetTimer()
  const ok = await copyToClipboard(props.text)
  copied.value = ok
  failed.value = !ok
  resetTimer = setTimeout(() => {
    copied.value = false
    failed.value = false
    resetTimer = null
  }, 1800)
}

onBeforeUnmount(clearResetTimer)
</script>

<template>
  <button
    type="button"
    class="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-1 text-[10px] font-medium shadow-sm backdrop-blur-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50"
    :class="dark
      ? 'border-white/15 bg-slate-800/85 text-slate-200 hover:bg-slate-700'
      : 'border-gray-200 bg-white/95 text-gray-500 hover:border-primary/40 hover:text-primary'"
    :disabled="!text"
    :title="copied ? '已复制' : failed ? '复制失败，点击重试' : `复制${label}`"
    :aria-label="copied ? '已复制' : failed ? '复制失败' : `复制${label}`"
    @click.stop="handleCopy"
  >
    <CheckIcon v-if="copied" class="h-3.5 w-3.5 text-emerald-500" />
    <ClipboardDocumentIcon v-else class="h-3.5 w-3.5" />
    <span aria-live="polite">{{ copied ? '已复制' : failed ? '重试' : '复制' }}</span>
  </button>
</template>
