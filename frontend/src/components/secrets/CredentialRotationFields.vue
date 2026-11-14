<script setup lang="ts">
import { ref, watch } from 'vue'
import CredentialFieldForm from './CredentialFieldForm.vue'
import { credentialError, getCredentialVersion, type CredentialMeta, type CredentialSchema, type FieldValue } from '@/composables/useCredentialFields'

const props = defineProps<{ item: CredentialMeta; modelValue: Record<string, FieldValue> }>()
const emit = defineEmits<{ 'update:modelValue': [Record<string, FieldValue>] }>()
const schema = ref<CredentialSchema | null>(null)
const error = ref('')

watch(() => props.item, async (item, _, onCleanup) => {
  let active = true
  onCleanup(() => { active = false })
  schema.value = null
  error.value = ''
  emit('update:modelValue', {})
  if (!item.type_key || !item.type_version) return
  try {
    const type = await getCredentialVersion(item.type_key, item.type_version)
    if (active) schema.value = { ...type.schema, properties: { ...type.schema.properties, ...item.extra_fields } }
  } catch (err) {
    if (active) error.value = credentialError(err, '无法加载凭据类型')
  }
}, { immediate: true })
</script>

<template>
  <div class="space-y-3">
    <p class="text-xs text-slate-600">填写需要轮换的字段，留空字段沿用当前值。新修订保存后立即生效。</p>
    <p v-if="error" role="alert" class="text-xs text-rose-700">{{ error }}</p>
    <CredentialFieldForm v-else-if="schema" :schema="schema" :model-value="modelValue" partial
      @update:model-value="emit('update:modelValue', $event)" />
    <p v-else class="text-xs text-slate-500">正在加载字段…</p>
  </div>
</template>
