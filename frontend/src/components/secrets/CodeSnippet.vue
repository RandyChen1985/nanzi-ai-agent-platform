<script setup lang="ts">
/**
 * 只读代码片段 + 一键复制。
 *
 * 为什么单独成组件：页面（`SecretCenter` / `AdminSecretCenter`）有一条硬约束
 * ——**不直接调复制工具**，所有复制都经组件走。这样「顺手给凭据的值也加个复制」
 * 这种事没有入口可加；契约测试扫到 `copyToClipboard(` 就能立刻拦住。
 *
 * 这里复制的是**代码示例**，不是任何凭据值：`code` 由调用方给常量，组件不读接口、
 * 不碰 store，也不接受异步取值。
 */
import { onBeforeUnmount, ref } from 'vue'

import { copyToClipboard } from '../../utils/clipboard'

const props = defineProps<{
  /** 要复制的完整文本（也是展示内容，逐字一致才不至于「看到的和复制的不一样」）。 */
  code: string
  /** 左上角标题，如 `Bash / Shell`。 */
  label?: string
  /** 右上角一句口径说明，如「值走 stdout」。 */
  hint?: string
}>()

const copied = ref(false)
let resetTimer: number | undefined

async function copy(): Promise<void> {
  const ok = await copyToClipboard(props.code)
  if (!ok) return
  copied.value = true
  window.clearTimeout(resetTimer)
  resetTimer = window.setTimeout(() => {
    copied.value = false
  }, 1500)
}

onBeforeUnmount(() => window.clearTimeout(resetTimer))
</script>

<template>
  <div>
    <div v-if="label || hint" class="mb-1.5 flex items-baseline justify-between gap-2">
      <span class="text-xs font-semibold text-gray-700">{{ label }}</span>
      <code v-if="hint" class="text-[10.5px] text-gray-400">{{ hint }}</code>
    </div>
    <div class="relative rounded-lg bg-gray-900 px-3 py-2.5">
      <button
        type="button"
        class="absolute right-2 top-2 rounded px-1.5 py-0.5 text-[10px] transition-colors"
        :class="copied
          ? 'text-emerald-400'
          : 'text-gray-400 hover:bg-white/10 hover:text-gray-100'"
        :aria-label="copied ? '已复制' : '复制代码'"
        @click="copy"
      >{{ copied ? '✓ 已复制' : '复制' }}</button>
      <pre class="overflow-x-auto whitespace-pre pr-14 font-mono text-[11px] leading-relaxed text-gray-300">{{ code }}</pre>
    </div>
  </div>
</template>
