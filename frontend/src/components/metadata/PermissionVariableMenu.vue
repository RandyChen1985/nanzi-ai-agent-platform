<template>
  <div
    class="absolute right-0 top-full mt-1 w-56 bg-white rounded-lg shadow-xl border border-gray-100 z-[9999] max-h-60 overflow-y-auto custom-scrollbar"
  >
    <div class="px-2.5 py-1.5 text-[9px] font-bold text-gray-500 uppercase tracking-wide bg-gray-50 border-b border-gray-100">
      内置用户属性
    </div>
    <button
      v-for="v in builtinVariables"
      :key="v.value"
      type="button"
      class="w-full text-left px-3 py-2 border-b border-gray-50 transition-colors"
      :class="hoverClass"
      @click="emit('select', v.value)"
    >
      <span class="text-[10px] font-medium text-gray-800">{{ v.label }}</span>
      <span class="block text-[9px] text-primary font-mono mt-0.5">{{ v.value }}</span>
    </button>

    <div class="px-2.5 py-1.5 text-[9px] font-bold text-primary-dark uppercase tracking-wide bg-primary/5 border-y border-primary/10">
      extra_data 扩展字段
    </div>
    <template v-if="extraDataVariables.length">
      <button
        v-for="v in extraDataVariables"
        :key="v.value"
        type="button"
        class="w-full text-left px-3 py-2 border-b border-primary/5 transition-colors hover:bg-primary/5"
        @click="emit('select', v.value)"
      >
        <div class="flex items-center gap-1.5">
          <span class="text-[10px] font-medium text-primary-dark">{{ v.label }}</span>
          <span class="text-[8px] px-1 py-0.5 rounded bg-primary/10 text-primary font-bold">扩展</span>
        </div>
        <span class="block text-[9px] text-primary font-mono mt-0.5">{{ v.value }}</span>
        <span v-if="v.sample" class="block text-[8px] text-gray-400 truncate mt-0.5">示例：{{ v.sample }}</span>
      </button>
    </template>
    <div v-else class="px-3 py-3 text-[9px] text-gray-400 leading-relaxed">
      暂无扩展字段。可在用户管理或第三方用户同步中配置 extra_data。
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface PermissionVariable {
  label: string
  value: string
  sample?: string
}

const props = withDefaults(
  defineProps<{
    builtinVariables: PermissionVariable[]
    extraDataVariables: PermissionVariable[]
    accent?: 'amber' | 'emerald' | 'slate'
  }>(),
  { accent: 'emerald' },
)

const emit = defineEmits<{ select: [value: string] }>()

const hoverClass = computed(() => {
  if (props.accent === 'amber') return 'hover:bg-amber-50'
  if (props.accent === 'slate') return 'hover:bg-slate-50'
  return 'hover:bg-emerald-50'
})
</script>
