<script setup lang="ts">
/**
 * 凭据标识（secret_key）+ 悬停浮出的复制按钮。
 *
 * **只复制标识，不复制值。** `corp.wecom.secret` 这类标识本身不是机密，它是要
 * 填到别处（工具参数、脚本环境变量、`${secret:标识.字段}` 引用）里的**引用名**，
 * 以前只能手抄或选中再复制，长标识很容易抄错一位。而值（token / 密码）在这个
 * 组件里根本不存在——页面也没有回读它的路径。
 *
 * 按钮做成悬停浮出而不是常显：标识在卡片上是次要信息，旁边再立一个图标按钮会
 * 和名称抢宽度。三条可达性兜底：
 *
 * - `group-focus-within` + `focus-visible`：键盘 Tab 得到；
 * - `max-sm:opacity-100`：触屏没有 hover，窄屏直接常显；
 * - 复制结果就地反馈（✓ + title 变成「已复制」），不依赖父页的 toast 通道。
 */
import { onBeforeUnmount, ref } from 'vue'
import { copyToClipboard } from '../../utils/clipboard'

const props = withDefaults(
  defineProps<{
    secretKey: string
    /** 展示用的字号档：卡片上比表格里略大一点 */
    size?: 'sm' | 'md'
  }>(),
  { size: 'sm' },
)

const copied = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

async function copyKey() {
  const ok = await copyToClipboard(props.secretKey)
  if (!ok) return
  copied.value = true
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    copied.value = false
  }, 1200)
}

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <span class="group inline-flex min-w-0 items-center gap-0.5">
    <code
      class="truncate rounded bg-gray-100 px-1.5 py-0.5 font-mono text-gray-600"
      :class="size === 'md' ? 'text-[11.5px]' : 'text-[11px]'"
      :title="secretKey"
    >{{ secretKey }}</code>
    <button
      type="button"
      class="shrink-0 rounded p-0.5 text-gray-400 opacity-0 transition-opacity hover:bg-gray-200 hover:text-gray-700 focus-visible:opacity-100 group-hover:opacity-100 group-focus-within:opacity-100 max-sm:opacity-100"
      :title="copied ? '已复制' : `复制标识：${secretKey}`"
      :aria-label="copied ? '已复制凭据标识' : `复制凭据标识 ${secretKey}`"
      @click.stop="copyKey"
    >
      <svg
        v-if="copied"
        class="h-3 w-3 text-emerald-600"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M20 6 9 17l-5-5" />
      </svg>
      <svg
        v-else
        class="h-3 w-3"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <rect x="9" y="9" width="11" height="11" rx="2" />
        <path d="M5 15V5a2 2 0 0 1 2-2h8" />
      </svg>
    </button>
  </span>
</template>
