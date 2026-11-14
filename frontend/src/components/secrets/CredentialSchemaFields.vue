<script setup lang="ts">
import {
  credentialFieldKind, credentialFieldKinds, newCredentialField, setCredentialFieldKind,
  type CredentialFieldDraft, type CredentialFieldKind,
} from '@/utils/credentialSchemaDraft'

const props = withDefaults(defineProps<{ modelValue: CredentialFieldDraft[]; depth?: number }>(), { depth: 0 })
const emit = defineEmits<{ 'update:modelValue': [CredentialFieldDraft[]] }>()

const inputClass = 'mt-1 w-full min-w-0 rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-sm text-slate-900 focus-visible:ring-2 focus-visible:ring-blue-500'

function setWriteOnly(row: CredentialFieldDraft, checked: boolean) {
  if (checked) {
    row.spec.writeOnly = true
    delete row.spec.default
  } else {
    delete row.spec.writeOnly
    delete row.spec.format
  }
}

function setDefault(row: CredentialFieldDraft, value: string) {
  if (value === '') delete row.spec.default
  else if (row.spec.type === 'boolean') row.spec.default = value === 'true'
  else if (row.spec.type === 'string') row.spec.default = value
  else row.spec.default = Number(value)
}
</script>

<template>
  <div class="space-y-3">
    <p v-if="!modelValue.length" class="rounded-xl border border-dashed border-slate-300 px-4 py-3 text-sm text-slate-600">
      还没有字段。点击「添加字段」开始定义录入表单。
    </p>
    <div v-for="(row, index) in modelValue" :key="row.id" role="group" :aria-label="'字段 ' + (index + 1)"
      class="min-w-0 space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
      <div class="grid items-end gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(7rem,0.8fr)_12rem]">
        <label class="min-w-0 text-xs font-medium text-slate-700">
          字段标识 <span class="text-rose-600">*</span>
          <input v-model="row.name" maxlength="64" placeholder="api_endpoint" spellcheck="false" autocomplete="off" :class="[inputClass, 'font-mono']" />
        </label>
        <label class="min-w-0 text-xs font-medium text-slate-700">
          显示名
          <input v-model="row.spec.title" maxlength="512" placeholder="服务地址" :class="inputClass" />
        </label>
        <label class="min-w-0 text-xs font-medium text-slate-700">
          字段类型
          <select :value="credentialFieldKind(row.spec)" :class="inputClass"
            @change="setCredentialFieldKind(row, ($event.target as HTMLSelectElement).value as CredentialFieldKind)">
            <option v-for="(label, kind) in credentialFieldKinds" :key="kind" :value="kind"
              :disabled="kind === 'object' && depth >= 4">{{ label }}</option>
          </select>
        </label>
        <div class="flex min-h-9 flex-wrap items-center gap-x-3 gap-y-2 text-xs text-slate-700">
          <label class="flex items-center gap-1.5"><input v-model="row.required" type="checkbox" />必填</label>
          <label v-if="!row.spec.enum" class="flex items-center gap-1.5">
            <input type="checkbox" :checked="row.spec.writeOnly || row.spec.format === 'password'"
              @change="setWriteOnly(row, ($event.target as HTMLInputElement).checked)" />敏感字段
          </label>
          <button type="button" :aria-label="'删除字段 ' + (row.name || index + 1)"
            class="rounded-lg border border-rose-200 bg-white px-2.5 py-1.5 text-rose-700 hover:bg-rose-50"
            @click="emit('update:modelValue', modelValue.filter((_, i) => i !== index))">删除</button>
        </div>
      </div>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="min-w-0 text-xs font-medium text-slate-700" :class="{ 'sm:col-span-2': !row.spec.enum && (row.spec.writeOnly || row.spec.type === 'object' || row.spec.type === 'array') }">
          说明
          <input v-model="row.spec.description" maxlength="512" placeholder="录入时显示的说明（可空）" :class="inputClass" />
        </label>
        <label v-if="row.spec.enum" class="min-w-0 text-xs font-medium text-slate-700">
          枚举选项 <span class="text-rose-600">*</span>
          <textarea v-model="row.enumText" rows="3" placeholder="每行一个选项" :class="[inputClass, 'resize-y']" />
        </label>
        <label v-else-if="!row.spec.writeOnly && row.spec.type !== 'object' && row.spec.type !== 'array'" class="min-w-0 text-xs font-medium text-slate-700">
          默认值
          <select v-if="row.spec.type === 'boolean'" :value="row.spec.default === undefined ? '' : String(row.spec.default)" :class="inputClass"
            @change="setDefault(row, ($event.target as HTMLSelectElement).value)">
            <option value="">无默认值</option>
            <option value="true">是</option><option value="false">否</option>
          </select>
          <input v-else :value="row.spec.default ?? ''" :type="row.spec.type === 'string' ? 'text' : 'number'"
            :step="row.spec.type === 'integer' ? 1 : 'any'" placeholder="可空" :class="inputClass"
            @input="setDefault(row, ($event.target as HTMLInputElement).value)" />
        </label>
      </div>
      <div v-if="row.spec.type === 'object'" class="space-y-2 border-l-2 border-blue-200 pl-3">
        <p class="text-xs font-medium text-slate-700">子字段</p>
        <CredentialSchemaFields v-model="row.children" :depth="depth + 1" />
      </div>
    </div>
    <button type="button" class="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"
      @click="emit('update:modelValue', [...props.modelValue, newCredentialField()])">
      + {{ depth ? '添加子字段' : '添加字段' }}
    </button>
  </div>
</template>
