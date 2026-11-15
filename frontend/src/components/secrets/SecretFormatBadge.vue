<script setup lang="ts">
/**
 * 值形态徽标：一眼分清「一个 token」和「一份配置」。
 *
 * 个人凭据中心与管理端是两套页面、两种布局（列表行 / 卡片 / 表格），但「这是单值
 * 还是结构化」的表述必须一致——两边各写一份，文案和配色很快就会漂。
 */
import { computed } from 'vue'
import { credentialTemplateLabel } from '../../utils/credentialTemplates'

const props = withDefaults(
  defineProps<{
    format?: 'text' | 'json' | string | null
    template?: string | null
    size?: 'sm' | 'xs'
    /**
     * 紧凑模式：只写「结构化」或「结构化 · smtp」，模板全称退到 title。
     *
     * 窄格子里放不下全称——「结构化 · 邮箱 SMTP 发送」有 12 个字，会把旁边的
     * 凭据标识挤到只剩前几个字符，等于丢掉了更有用的信息（是哪条凭据）。
     */
    compact?: boolean
  }>(),
  { format: 'text', template: null, size: 'xs', compact: false },
)

const structured = computed(() => props.format === 'json')
const label = computed(() => {
  if (!structured.value) return '单值'
  if (!props.compact) return `结构化 · ${credentialTemplateLabel(props.template)}`
  // 紧凑模式优先给模板 id（短且唯一），全称放 title
  return props.template ? `结构化 · ${props.template}` : '结构化'
})
const title = computed(() => {
  if (!structured.value) return '单值：整段就是一个值（token / PEM）'
  const full = credentialTemplateLabel(props.template)
  const base = '结构化 JSON：可以被 ${secret:标识.字段} 引用单个字段'
  return props.compact && props.template ? `${full} · ${base}` : base
})
</script>

<template>
  <span
    class="shrink-0 whitespace-nowrap rounded px-1 py-0.5 font-normal"
    :class="[
      structured ? 'bg-indigo-50 text-indigo-700' : 'bg-gray-100 text-gray-400',
      size === 'sm' ? 'text-[11px]' : 'text-[10px]',
    ]"
    :title="title"
  >{{ label }}</span>
</template>
