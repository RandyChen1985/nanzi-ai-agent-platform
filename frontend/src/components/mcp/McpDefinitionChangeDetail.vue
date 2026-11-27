<script setup lang="ts">
/**
 * 单条定义变更的差异详情（抽屉与服务内提示条共用）。
 *
 * 抽成独立组件是因为预览要看的东西完全一样，而「两处各写一份 diff 渲染」最容易
 * 出现的就是一边改了字段、另一边还显示旧口径。
 */
import { computed } from 'vue'
import { ExclamationTriangleIcon } from '@heroicons/vue/24/outline'

interface DefinitionChange {
  id: string
  tool_name: string
  tool_short_name: string
  change_kind: string
  severity: 'safe' | 'notice' | 'breaking' | string
  before_value: Record<string, any> | null
  after_value: Record<string, any> | null
  summary: string
  detected_at?: string | null
  detected_by?: string | null
}

const props = defineProps<{ change: DefinitionChange }>()

const SOURCE_LABELS: Record<string, string> = {
  nightly: '夜间巡检',
  page: '进页面探测',
  hot_call: '活跃会话热刷新',
  manual: '手动探测',
}

const isDescriptionKind = computed(() =>
  props.change.change_kind === 'description_changed' || props.change.change_kind === 'param_description')

const isParamKind = computed(() => props.change.change_kind.startsWith('param_'))

const paramName = computed(() =>
  String(props.change.after_value?.param || props.change.before_value?.param || ''))

const diffRows = computed(() => {
  const before = props.change.before_value || {}
  const after = props.change.after_value || {}
  // 必须与后端 `_param_snapshot` 采集的字段对齐：漏一项就意味着「后端判定为变更、
  // 点进来却看不到任何标红行」，用户会以为这个待办是空的。
  const fields: Array<{ key: string, label: string }> = [
    { key: 'type', label: '类型' },
    { key: 'anyOf', label: '联合类型（anyOf）' },
    { key: 'oneOf', label: '联合类型（oneOf）' },
    { key: 'allOf', label: '组合类型（allOf）' },
    { key: '$ref', label: '引用类型' },
    { key: 'const', label: '固定值' },
    { key: 'required', label: '必填' },
    { key: 'enum', label: '枚举' },
    { key: 'items', label: '元素' },
    { key: 'properties', label: '嵌套字段' },
    { key: 'nested_required', label: '嵌套必填项' },
    { key: 'additionalProperties', label: '扩展字段策略' },
    { key: 'format', label: '格式' },
    { key: 'minimum', label: '最小值' },
    { key: 'maximum', label: '最大值' },
    { key: 'minLength', label: '最小长度' },
    { key: 'maxLength', label: '最大长度' },
    { key: 'pattern', label: '正则约束' },
    { key: 'default', label: '默认值' },
    { key: 'description', label: '描述' },
  ]
  return fields
    .filter(({ key }) => key in before || key in after)
    .map(({ key, label }) => ({
      label,
      before: formatValue(before[key]),
      after: formatValue(after[key]),
      changed: JSON.stringify(before[key] ?? null) !== JSON.stringify(after[key] ?? null),
    }))
})

/** 元素转文本：anyOf / oneOf / items 都是对象数组，join 出来会是「[object Object]」。 */
function formatItem(item: any): string {
  if (item === undefined || item === null) return '—'
  if (typeof item === 'object') return JSON.stringify(item)
  return String(item)
}

function formatValue(value: any): string {
  if (value === undefined || value === null) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) {
    if (!value.length) return '—'
    // 纯标量（枚举值最常见）用顿号更易读；混进对象就整体按 JSON 展开
    return value.every((item) => item === null || typeof item !== 'object')
      ? value.map((item) => String(item)).join('、')
      : value.map(formatItem).join('、')
  }
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const formatTime = (value?: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}
</script>

<template>
  <div>
    <div v-if="isDescriptionKind" class="grid grid-cols-1 gap-2 sm:grid-cols-2">
      <div class="rounded border border-gray-200 bg-white p-2">
        <div class="mb-1 text-[9px] font-bold uppercase tracking-wider text-slate-400">之前</div>
        <p class="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-slate-600">
          {{ change.before_value?.description || '—' }}
        </p>
      </div>
      <div class="rounded border border-violet-200 bg-white p-2">
        <div class="mb-1 text-[9px] font-bold uppercase tracking-wider text-violet-500">之后</div>
        <p class="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-slate-700">
          {{ change.after_value?.description || '—' }}
        </p>
      </div>
    </div>

    <table v-else-if="isParamKind" class="w-full text-[11px]">
      <thead>
        <tr class="text-left text-[9px] uppercase tracking-wider text-slate-400">
          <th class="w-24 pb-1 font-bold">字段</th>
          <th class="pb-1 font-bold">之前</th>
          <th class="pb-1 font-bold">之后</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in diffRows" :key="row.label">
          <td class="py-0.5 pr-2 align-top text-slate-500">{{ row.label }}</td>
          <td class="py-0.5 pr-2 align-top font-mono" :class="row.changed ? 'text-red-600 line-through' : 'text-slate-500'">
            {{ row.before }}
          </td>
          <td class="py-0.5 align-top font-mono" :class="row.changed ? 'font-bold text-violet-700' : 'text-slate-500'">
            {{ row.after }}
          </td>
        </tr>
        <tr v-if="!diffRows.length">
          <td colspan="3" class="py-1 text-slate-400">
            参数 <span class="font-mono">{{ paramName || '（未命名）' }}</span> 的结构已变化
          </td>
        </tr>
      </tbody>
    </table>

    <p v-else class="text-[11px] text-slate-500">{{ change.summary }}</p>

    <div class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-slate-400">
      <span>探测于 {{ formatTime(change.detected_at) }}</span>
      <span>· {{ SOURCE_LABELS[change.detected_by || 'manual'] || change.detected_by }}</span>
      <span v-if="change.severity === 'breaking'" class="inline-flex items-center gap-0.5 text-red-600">
        <ExclamationTriangleIcon class="h-3 w-3" />已发布的智能体下次调用可能失败
      </span>
    </div>
  </div>
</template>
