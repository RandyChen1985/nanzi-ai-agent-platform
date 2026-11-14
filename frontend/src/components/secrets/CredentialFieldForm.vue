<script setup lang="ts">
import type { CredentialField, CredentialSchema, CredentialValues, FieldValue } from '@/composables/useCredentialFields'

const props = defineProps<{
  schema: Pick<CredentialSchema, 'properties' | 'required'>
  modelValue: CredentialValues
  partial?: boolean
  masked?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [CredentialValues] }>()

const controlClass = 'mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-blue-500'

function shown(name: string): FieldValue | '' {
  return props.modelValue[name] ?? (props.partial ? '' : props.schema.properties[name]?.default ?? '')
}

function setValue(name: string, value?: FieldValue) {
  const next = { ...props.modelValue }
  if (value === undefined) delete next[name]
  else next[name] = value
  emit('update:modelValue', next)
}

function update(name: string, field: CredentialField, event: Event) {
  const raw = (event.target as HTMLInputElement).value
  if (raw === '' && (props.partial || field.type !== 'string' || field.enum)) {
    setValue(name)
  } else {
    setValue(name, field.type === 'boolean' ? raw === 'true' : field.type === 'string' ? raw : Number(raw))
  }
}

function arrayItems(name: string): string[] {
  const value = shown(name)
  return Array.isArray(value) ? value : []
}

function objectValues(name: string): CredentialValues {
  const value = shown(name)
  return typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function setArrayItem(name: string, index: number, value: string) {
  const items = [...arrayItems(name)]
  items[index] = value
  setValue(name, items)
}
</script>

<template>
  <div class="grid gap-3 sm:grid-cols-2">
    <template v-for="(field, name) in schema.properties" :key="name">
      <fieldset v-if="field.type === 'object' || field.type === 'array'" class="min-w-0 space-y-2 rounded-lg border border-slate-200 p-3 sm:col-span-2">
        <legend class="px-1 text-xs font-medium text-slate-700">
          {{ field.title || name }}
          <span v-if="!partial && schema.required?.includes(name)" class="text-rose-600">*</span>
          <span v-if="masked || field.writeOnly" class="ml-1 text-violet-700">敏感字段</span>
        </legend>
        <p v-if="field.description" class="text-xs text-slate-500">{{ field.description }}</p>
        <label class="flex items-center gap-2 text-xs text-slate-700">
          <input type="checkbox" :checked="modelValue[name] !== undefined"
            @change="setValue(name, ($event.target as HTMLInputElement).checked ? (field.type === 'array' ? [...arrayItems(name)] : {}) : undefined)" />
          {{ partial ? '替换整组' : '填写此字段' }}
        </label>
        <div v-if="modelValue[name] !== undefined || (!partial && field.default !== undefined)" class="space-y-2">
          <template v-if="field.type === 'array'">
            <div v-for="(item, index) in arrayItems(name)" :key="index" class="flex items-start gap-2">
              <textarea :value="item" :aria-label="(field.title || name) + ' 第 ' + (index + 1) + ' 项'"
                rows="1" spellcheck="false" autocomplete="off" :class="[controlClass, { 'credential-mask': masked || field.writeOnly }]"
                @input="setArrayItem(name, index, ($event.target as HTMLTextAreaElement).value)" />
              <button type="button" :aria-label="'移除第 ' + (index + 1) + ' 项'" class="mt-1 rounded-lg border px-2 py-2 text-xs text-rose-700"
                @click="setValue(name, arrayItems(name).filter((_, i) => i !== index))">移除</button>
            </div>
            <button type="button" class="rounded-lg border px-3 py-1.5 text-xs text-slate-700"
              @click="setValue(name, [...arrayItems(name), ''])">添加一项</button>
          </template>
          <template v-else>
            <p v-if="partial" class="text-xs text-slate-600">请重新填写此对象的必填子字段。</p>
            <CredentialFieldForm :schema="{ properties: field.properties ?? {}, required: field.required }"
              :model-value="objectValues(name)" :masked="masked || field.writeOnly" @update:model-value="setValue(name, $event)" />
          </template>
        </div>
      </fieldset>
      <label v-else class="block min-w-0">
        <span class="text-xs font-medium text-slate-700">
          {{ field.title || name }}
          <span v-if="!partial && schema.required?.includes(name)" class="text-rose-600">*</span>
          <span v-if="masked || field.writeOnly" class="ml-1 text-violet-700">敏感字段</span>
        </span>
        <select v-if="field.enum || field.type === 'boolean'" :value="String(shown(name))"
          :class="controlClass" @change="update(name, field, $event)">
          <option value="">{{ partial ? '保持当前值' : '请选择' }}</option>
          <template v-if="field.enum">
            <option v-for="choice in field.enum" :key="choice" :value="choice">{{ choice }}</option>
          </template>
          <template v-else>
            <option value="true">是</option>
            <option value="false">否</option>
          </template>
        </select>
        <textarea v-else-if="field.type === 'string' && (masked || field.writeOnly)"
          :value="String(shown(name))" @input="update(name, field, $event)" rows="2" spellcheck="false" autocomplete="off"
          :placeholder="partial ? '保持当前值' : undefined" :maxlength="field.maxLength"
          :class="[controlClass, 'credential-mask resize-y font-mono']" />
        <input v-else :type="field.type === 'string' ? 'text' : 'number'" :value="String(shown(name))" @input="update(name, field, $event)"
          :step="field.type === 'integer' ? 1 : 'any'" :min="field.minimum" :max="field.maximum"
          :minlength="field.minLength" :maxlength="field.maxLength" :placeholder="partial ? '保持当前值' : undefined"
          autocomplete="off" spellcheck="false" :class="[controlClass, { 'credential-mask': masked || field.writeOnly }]" />
        <span v-if="field.description" class="mt-1 block text-xs text-slate-500">{{ field.description }}</span>
      </label>
    </template>
  </div>
</template>

<style scoped>
.credential-mask { -webkit-text-security: disc; }
</style>
