<script setup lang="ts">
import { ref, watch } from 'vue'
import CredentialFieldForm from './CredentialFieldForm.vue'
import type { CredentialSchema, FieldValue } from '@/composables/useCredentialFields'

const props = defineProps<{
  schema: CredentialSchema
  showAccount?: boolean
}>()
const account = ref('')
const values = ref<Record<string, FieldValue>>({})

watch(() => props.schema, () => {
  account.value = ''
  values.value = {}
})
</script>

<template>
  <section aria-label="录入表单预览" class="space-y-3 rounded-lg border bg-slate-50 p-3">
    <p class="text-xs text-slate-600">可用示例值试填。关闭预览会清空试填内容。</p>
    <label v-if="showAccount" class="block text-xs font-medium text-slate-700">
      凭据账号 <span class="text-rose-600">*</span>
      <input v-model="account" maxlength="128" autocomplete="off" spellcheck="false"
        class="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
    </label>
    <CredentialFieldForm v-model="values" :schema="schema" />
  </section>
</template>
