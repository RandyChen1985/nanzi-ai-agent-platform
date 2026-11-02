<template>
  <div class="flex flex-wrap items-center gap-2.5 rounded-xl border border-amber-200 bg-amber-50/70 px-3.5 py-[11px] text-[12.5px] text-amber-900">
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
  </div>
</template>

<script setup lang="ts">
/**
 * 概览页异常摘要条。
 *
 * 只说「12 次失败」的话，用户还得自己去翻日志找是哪个工具；这里把失败集中在
 * 哪些对象一起给出来。
 *
 * 刻意不带跳转按钮：失败明细就在下方「工具调用分布」卡片的 footer 里，
 * 逐条可点进具体 trace，条上再放一个「查看失败详情」是重复入口。
 */
withDefaults(
  defineProps<{
    count?: number
    /** 失败集中的对象，如「sub_agent_call 与 mcp-public-admin」 */
    subjects?: string
  }>(),
  { count: 0 },
)
</script>
