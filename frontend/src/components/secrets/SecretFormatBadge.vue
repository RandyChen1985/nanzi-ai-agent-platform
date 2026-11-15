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
  }>(),
  { format: 'text', template: null, size: 'xs' },
)

const structured = computed(() => props.format === 'json')
const label = computed(() =>
  structured.value ? `结构化 · ${credentialTemplateLabel(props.template)}` : '单值',
)
const title = computed(() =>
  structured.value
    ? '结构化 JSON：可以被 ${secret:标识.字段} 引用单个字段'
    : '单值：整段就是一个值（token / PEM）',
)
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
