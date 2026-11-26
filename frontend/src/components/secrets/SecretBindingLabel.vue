<script setup lang="ts">
/**
 * 一条绑定在列表里的完整说法：**凭据标识 + 值形态 + 取用字段**。
 *
 * 只显示标识是不够的。用户看到 `admin.mail.cre` 会以为整条凭据都在用，
 * 实际上注进去的可能只是其中的 `password`——结构化凭据不标出取哪个字段，
 * 排查时就只能去翻凭据原文或日志。
 *
 * 五个列表（API 工具 Header / API 工具参数 / MCP 参数设置 / MCP Header /
 * 脚本环境变量 / 我的 MCP 服务）各写一份必然漂文案，所以收在这里。
 */
import { computed } from 'vue'
import SecretFormatBadge from './SecretFormatBadge.vue'

const props = withDefaults(
  defineProps<{
    secretKey?: string | null
    /** text | json */
    format?: string | null
    /** 结构化模板标识，如 smtp */
    template?: string | null
    /** 取用字段路径；空串 = 整段 */
    valuePath?: string | null
    size?: 'sm' | 'xs'
    /** 窄格子（表格内的值列）用紧凑徽标，把宽度留给凭据标识 */
    compact?: boolean
  }>(),
  { secretKey: '', format: 'text', template: null, valuePath: '', size: 'xs', compact: false },
)

const structured = computed(() => props.format === 'json')

/**
 * 结构化却写着「整段」是**可疑配置**（注进去会是一整份 JSON），标黄；
 * 单值凭据本来就只有整段，不必再提醒。
 */
const fieldLabel = computed(() => {
  if (props.format == null) return ''
  if (props.valuePath) return `取 ${props.valuePath}`
  return structured.value ? '整段 JSON' : ''
})

const fieldClass = computed(() =>
  structured.value && !props.valuePath
    ? 'bg-amber-50 text-amber-700'
    : 'bg-primary/5 text-primary-dark',
)

const fieldTitle = computed(() => {
  if (props.valuePath) {
    return `只把凭据里的 ${props.valuePath} 注入进去`
  }
  return structured.value
    ? '结构化凭据没指定字段：注入的会是整份 JSON，通常不是想要的'
    : ''
})
</script>

<template>
  <span class="inline-flex min-w-0 flex-wrap items-center gap-1">
    <span
      class="min-w-0 truncate"
      :class="size === 'sm' ? 'text-xs' : 'text-[10px]'"
      :title="secretKey || ''"
    >{{ secretKey }}</span>
    <SecretFormatBadge
      v-if="format"
      :format="format"
      :template="template"
      :size="size"
      :compact="compact"
    />
    <span
      v-if="fieldLabel"
      class="shrink-0 whitespace-nowrap rounded px-1 py-0.5 text-[10px] font-medium"
      :class="fieldClass"
      :title="fieldTitle"
    >{{ fieldLabel }}</span>
  </span>
</template>
