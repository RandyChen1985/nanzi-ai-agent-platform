<template>
  <!-- 5 段字段图例：让用户先知道每个位置填什么，再谈语法 -->
  <div class="rounded-xl border border-gray-100 bg-gray-50/70 p-2 space-y-1">
    <div class="grid grid-cols-5 gap-1">
      <div
        v-for="field in CRON_FIELDS"
        :key="field.key"
        class="rounded-lg bg-white px-1 py-1 text-center ring-1 ring-gray-100"
      >
        <p class="text-[11px] font-bold text-gray-600">{{ field.label }}</p>
        <p class="text-[10px] leading-tight text-gray-400">{{ field.range }}</p>
      </div>
    </div>
    <p class="text-[10px] leading-relaxed text-gray-400">
      <span class="font-mono text-gray-500">*</span> 每个单位 ·
      <span class="font-mono text-gray-500">5</span> 指定值 ·
      <span class="font-mono text-gray-500">1-5</span> 范围 ·
      <span class="font-mono text-gray-500">*/10</span> 每 10 个单位 ·
      <span class="font-mono text-gray-500">1,3,5</span> 多个值
    </p>
    <p class="text-[10px] leading-relaxed text-gray-400">
      周：<span class="font-mono text-gray-500">0</span> 或 <span class="font-mono text-gray-500">7</span> = 周日，
      <span class="font-mono text-gray-500">1</span> = 周一 …
      <span class="font-mono text-gray-500">6</span> = 周六
    </p>
    <p class="text-[10px] leading-relaxed text-gray-400">
      例：<span class="font-mono text-gray-500">0 9 * * 1-5</span> → 每周一至周五 09:00；
      <span class="font-mono text-gray-500">*/30 * * * *</span> → 每 30 分钟
    </p>
  </div>
</template>

<script setup lang="ts">
const CRON_FIELDS = [
  { key: 'minute', label: '分', range: '0-59' },
  { key: 'hour', label: '时', range: '0-23' },
  { key: 'day', label: '日', range: '1-31' },
  { key: 'month', label: '月', range: '1-12' },
  { key: 'weekday', label: '周', range: '0-7' },
] as const
</script>
