<script setup lang="ts">
import { computed } from 'vue'
import { DB_BRAND_ICONS, normalizeDbType } from './dbBrandIcons'

/**
 * 数据库类型图标：统一用内联 SVG，不再用 emoji。
 *
 * emoji 的问题：不同系统渲染的造型/颜色不一致（macOS 海豚、Windows 鸭子），
 * 而且 Oracle 红球、SQL Server 蓝方块这类「近似图形」并不对应官方 mark。
 * 这里 MySQL / PostgreSQL / ClickHouse / Apache Doris 用官方单色 mark，
 * Oracle / SQL Server 官方没有可缩放的独立图形，用品牌色示意并在 label 标注。
 */
const props = withDefaults(defineProps<{ type: string; size?: number }>(), {
  size: 20,
})

const brand = computed(() => DB_BRAND_ICONS[normalizeDbType(props.type)] || null)
</script>

<template>
  <svg
    v-if="brand"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    role="img"
    :aria-label="brand.label"
    focusable="false"
  >
    <path
      v-for="(shape, index) in brand.paths"
      :key="index"
      :d="shape.d"
      :fill="shape.fill || brand.color"
      fill-rule="evenodd"
    />
  </svg>
  <svg
    v-else
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    role="img"
    aria-label="数据库"
    focusable="false"
    class="text-gray-400"
  >
    <ellipse cx="12" cy="6.4" rx="7" ry="2.9" stroke="currentColor" stroke-width="1.6" />
    <path
      d="M5 6.4v11.2c0 1.6 3.1 2.9 7 2.9s7-1.3 7-2.9V6.4"
      stroke="currentColor"
      stroke-width="1.6"
    />
    <path
      d="M5 11.4c0 1.6 3.1 2.9 7 2.9s7-1.3 7-2.9M5 16.2c0 1.6 3.1 2.9 7 2.9s7-1.3 7-2.9"
      stroke="currentColor"
      stroke-width="1.6"
    />
  </svg>
</template>
