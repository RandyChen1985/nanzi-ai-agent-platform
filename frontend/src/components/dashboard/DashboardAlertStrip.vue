<template>
  <div
    v-if="!dismissed"
    class="flex flex-wrap items-center gap-2.5 rounded-xl border border-amber-200 bg-amber-50/70 px-3.5 py-[11px] text-[12.5px] text-amber-900"
  >
    <svg class="h-[15px] w-[15px] shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M10.29 3.86a2 2 0 0 1 3.42 0l8.6 14.86A2 2 0 0 1 20.6 21H3.4a2 2 0 0 1-1.71-2.28z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </svg>
    <span class="min-w-0">
      <slot>
        今日 <strong class="font-bold tabular-nums">{{ count }}</strong> 次调用失败<span v-if="subjects">，集中在 <strong>{{ subjects }}</strong></span>
      </slot>
    </span>
    <button
      type="button"
      class="ml-auto grid h-[22px] w-[22px] shrink-0 place-items-center rounded-md text-amber-700 opacity-60 transition-all hover:bg-amber-900/10 hover:text-amber-800 hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
      title="今天不再提示"
      aria-label="关闭失败提示"
      @click="dismiss"
    >
      <svg class="h-[13px] w-[13px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">
        <path stroke-linecap="round" d="M6 6l12 12M18 6L6 18" />
      </svg>
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

/**
 * 概览页异常摘要条。
 *
 * 只说「12 次失败」的话，用户还得自己去翻日志找是哪个工具；这里把失败集中在
 * 哪些对象一起给出来。
 *
 * 刻意不带跳转按钮：失败明细就在下方「工具调用分布」卡片的 footer 里，
 * 逐条可点进具体 trace，条上再放一个「查看失败详情」是重复入口。
 *
 * 但必须能关掉：这是常驻告警，已经知道哪个工具在报错、正在处理时，
 * 它就一直占着首屏最好的位置。关闭状态按「当天 + 当前统计周期」记：
 * 跨天或切到近 7 天会重新出现，因为失败数本来就是这两个维度的读数。
 */
const props = withDefaults(
  defineProps<{
    count?: number
    /** 失败集中的对象，如「sub_agent_call 与 mcp-public-admin」 */
    subjects?: string
    /** 统计周期（today / 7d …），参与关闭状态的记忆范围 */
    scope?: string
  }>(),
  { count: 0, scope: 'today' },
)

const dismissed = ref(false)

const storageKey = computed(() => {
  // sv-SE 输出 YYYY-MM-DD，正好当日期前缀，且用的是本地时区
  const day = new Date().toLocaleDateString('sv-SE')
  return `ov:alert-dismissed:${day}:${props.scope}`
})

const readDismissed = () => {
  try {
    dismissed.value = localStorage.getItem(storageKey.value) === '1'
  } catch {
    // 隐私模式 / 配额满：存不下就当没关过，不能因此让页面报错
    dismissed.value = false
  }
}

onMounted(readDismissed)
watch(storageKey, readDismissed)

const dismiss = () => {
  dismissed.value = true
  try {
    localStorage.setItem(storageKey.value, '1')
  } catch {
    /* 存不下就只在本次会话里隐藏 */
  }
}
</script>
