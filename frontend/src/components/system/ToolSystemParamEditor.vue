<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import PermissionVariableMenu from '../metadata/PermissionVariableMenu.vue'
import { SYSTEM_PARAM_TYPES, type SystemParam } from '../../utils/toolSchema'
import type { UserVariable } from '../../utils/userVariables'

const props = defineProps<{
  modelValue: SystemParam[]
  builtinVariables: UserVariable[]
  extraDataVariables: UserVariable[]
}>()

const emit = defineEmits<{ (e: 'update:modelValue', value: SystemParam[]): void }>()

const openMenuIndex = ref<number | null>(null)

const update = (index: number, patch: Partial<SystemParam>) => {
  emit('update:modelValue', props.modelValue.map((param, i) => (
    i === index ? { ...param, ...patch } : param
  )))
}

const addParam = () => {
  emit('update:modelValue', [
    ...props.modelValue,
    { name: '', type: 'string', value: '', description: '' },
  ])
}

const removeParam = (index: number) => {
  openMenuIndex.value = null
  emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
}

const selectVariable = (index: number, value: string) => {
  update(index, { value })
  openMenuIndex.value = null
}

const toggleMenu = (index: number) => {
  openMenuIndex.value = openMenuIndex.value === index ? null : index
}

const onDocumentClick = () => {
  openMenuIndex.value = null
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>

<template>
  <div class="rounded-lg border border-indigo-200 bg-indigo-50/40 p-3">
    <div class="flex flex-wrap items-start justify-between gap-2">
      <div>
        <p class="text-xs font-semibold text-indigo-900">系统级参数（模型不可见）</p>
        <p class="mt-1 text-[11px] leading-relaxed text-indigo-700">
          这些参数不会出现在模型工具定义里，调用前由平台强制写入；URL 含
          <code class="font-mono">{参数名}</code> 走 path，否则 GET 走 query、其他方法走 JSON body；
          变量取不到时本次调用会明确失败。
        </p>
      </div>
      <button
        type="button"
        class="rounded-md border border-indigo-300 bg-white px-2.5 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50"
        @click="addParam"
      >+ 添加系统级参数</button>
    </div>

    <p v-if="modelValue.length === 0" class="mt-2 text-[11px] italic text-indigo-400">
      暂无系统级参数。申请人、操作人等不能由模型决定的值可以加在这里。
    </p>

    <div
      v-for="(p, index) in modelValue"
      :key="index"
      class="mt-2 flex flex-wrap items-center gap-2 rounded-md bg-white p-2 shadow-sm"
    >
      <input
        v-model="p.name"
        class="w-40 rounded-md border-gray-300 text-xs font-mono shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
        placeholder="参数名"
      />
      <select
        v-model="p.type"
        class="w-24 rounded-md border-gray-300 text-xs font-mono shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
      >
        <option v-for="option in SYSTEM_PARAM_TYPES" :key="option" :value="option">{{ option }}</option>
      </select>
      <div class="relative flex items-center gap-1">
        <input
          v-model="p.value"
          class="w-40 rounded-md border-gray-300 text-xs font-mono shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
          placeholder="{user.user_name}"
        />
        <button
          type="button"
          class="flex h-7 w-7 items-center justify-center rounded-md border border-gray-300 bg-white font-mono text-[11px] font-bold text-gray-600 hover:bg-gray-50"
          :title="`为系统级参数 ${p.name || index + 1} 插入用户变量`"
          :aria-label="`为系统级参数 ${p.name || index + 1} 插入用户变量`"
          :aria-expanded="openMenuIndex === index"
          @click.stop="toggleMenu(index)"
        >{{ '{}' }}</button>
        <PermissionVariableMenu
          v-if="openMenuIndex === index"
          :builtin-variables="builtinVariables"
          :extra-data-variables="extraDataVariables"
          accent="slate"
          @click.stop
          @select="selectVariable(index, $event)"
        />
      </div>
      <input
        v-model="p.description"
        class="min-w-[10rem] flex-1 rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
        placeholder="描述（仅编辑和试跑提示）"
      />
      <button
        type="button"
        class="rounded-md p-1 text-gray-400 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/30"
        :title="`删除系统级参数 ${p.name || index + 1}`"
        :aria-label="`删除系统级参数 ${p.name || index + 1}`"
        @click="removeParam(index)"
      >
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      </button>
    </div>
  </div>
</template>
