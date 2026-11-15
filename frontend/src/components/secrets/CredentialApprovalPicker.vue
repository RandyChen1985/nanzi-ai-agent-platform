<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import CredentialFieldForm from './CredentialFieldForm.vue'
import { credentialFieldsComplete, type CredentialSchema, type FieldValue } from '@/composables/useCredentialFields'
import type { CredentialCandidate } from '@/utils/mcpCredentialProfiles'

const props = defineProps<{
  requestId: string
  modelValue: string
  disabled?: boolean
  authHeaders?: Record<string, string>
  retryError?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [string]; created: [string] }>()
const items = ref<CredentialCandidate[]>([])
const loading = ref(false)
const error = ref('')
const selectionReason = ref('')
const selectionMessages: Record<string, string> = {
  credential_missing: '当前没有符合此工具权限和类型的凭据。',
  credential_ambiguous: '有多个符合条件的凭据，请选择本次调用使用的一条。',
  credential_selection_invalid: '凭据或账号不可用。请选择其他凭据，或前往凭据中心修复。',
}
const creation = ref<{
  type_name: string
  version: number
  schema: CredentialSchema
  host: string
  account_hint: string | null
  can_create: boolean
  creation_disabled_reason: string | null
} | null>(null)
const creating = ref(false)
const saving = ref(false)
const account = ref('')
const name = ref('')
const fields = ref<Record<string, FieldValue>>({})
let requestSequence = 0
const optionsPath = computed(() => '/api/v1/chat/permissions/' + encodeURIComponent(props.requestId) + '/credential-options')
const selectionDisabled = computed(() => props.disabled || loading.value || creating.value || saving.value)
const sourceLabel = (item: CredentialCandidate) => item.owner_scope === 'system' ? '系统共享' : '个人'
const canCreate = computed(() => creation.value?.can_create === true)
const complete = computed(() => canCreate.value && !!creation.value && !!account.value.trim()
  && credentialFieldsComplete(creation.value.schema, fields.value))

function clearForm() {
  creating.value = false
  fields.value = {}
  account.value = ''
  name.value = ''
}

function openForm() {
  if (!canCreate.value || props.disabled || loading.value || saving.value) return
  clearForm()
  error.value = ''
  emit('update:modelValue', '')
  account.value = creation.value?.account_hint || ''
  creating.value = true
}

function failureMessage(body: { detail?: string | { message?: string }; message?: string }, fallback: string) {
  return typeof body.detail === 'string' ? body.detail : body.detail?.message || body.message || fallback
}

async function refresh() {
  const sequence = ++requestSequence
  clearForm()
  emit('update:modelValue', '')
  items.value = []
  creation.value = null
  selectionReason.value = ''
  error.value = ''
  loading.value = true
  try {
    const response = await fetch(optionsPath.value, {
      credentials: 'include', headers: props.authHeaders, cache: 'no-store',
    })
    if (sequence !== requestSequence) return
    const body = await response.json()
    if (!response.ok) throw new Error(failureMessage(body, '凭据列表无法加载'))
    if (sequence !== requestSequence) return
    items.value = body.data || []
    creation.value = body.creation || null
    selectionReason.value = body.selection_reason || ''
  } catch (err) {
    if (sequence === requestSequence) error.value = err instanceof Error ? err.message : '凭据列表无法加载'
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

async function saveCredential() {
  if (!complete.value || props.disabled || saving.value) return
  const sequence = requestSequence
  error.value = ''
  saving.value = true
  try {
    const pending = fetch(optionsPath.value, {
      method: 'POST', credentials: 'include', cache: 'no-store',
      headers: { ...props.authHeaders, 'Content-Type': 'application/json' },
      body: JSON.stringify({ account: account.value.trim(), name: name.value.trim(), fields: fields.value }),
    })
    fields.value = {}
    const response = await pending
    const body = await response.json()
    if (sequence !== requestSequence) return
    if (!response.ok) throw new Error(failureMessage(body, '凭据创建失败'))
    clearForm()
    emit('update:modelValue', body.data.id)
    emit('created', body.data.id)
  } catch (err) {
    if (sequence === requestSequence) {
      const message = err instanceof Error ? err.message : '凭据创建失败'
      error.value = message + '。凭据字段已清空，请重新填写。'
    }
  } finally {
    saving.value = false
  }
}

watch(() => props.requestId, () => void refresh(), { immediate: true })
watch(() => props.disabled, disabled => { if (disabled) clearForm() })
watch(() => props.retryError, message => {
  if (message) void refresh()
})
onBeforeUnmount(() => {
  requestSequence++
  clearForm()
})
</script>

<template>
  <div class="space-y-2 rounded-lg border border-current/15 p-2.5 text-xs">
    <fieldset v-if="items.length > 0 && items.length <= 5" :disabled="selectionDisabled">
      <legend class="font-medium">本次调用的凭据</legend>
      <div class="mt-1.5 space-y-1.5">
        <label v-for="item in items" :key="item.id"
          class="flex min-h-11 cursor-pointer items-center gap-2.5 rounded-lg border border-current/15 px-2.5 py-1.5 leading-relaxed focus-within:ring-2 focus-within:ring-current"
          :class="{ 'bg-black/5 dark:bg-white/10': item.id === modelValue, 'opacity-60': selectionDisabled }">
          <input type="radio" class="shrink-0" :name="'credential-' + requestId" :value="item.id"
            :checked="item.id === modelValue" @change="emit('update:modelValue', item.id)" />
          <span class="min-w-0 flex-1 break-words">
            <span class="block font-medium">{{ item.name }}</span>
            <span class="block opacity-80">{{ item.account }}</span>
          </span>
          <span class="shrink-0 opacity-80">{{ sourceLabel(item) }}</span>
        </label>
      </div>
    </fieldset>
    <label v-else-if="items.length" class="block font-medium">
      本次调用的凭据
      <select :value="modelValue" :disabled="selectionDisabled"
        class="mt-1.5 w-full rounded-lg border bg-white px-2 py-2 text-slate-900"
        @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)">
        <option value="">请选择</option>
        <option v-for="item in items" :key="item.id" :value="item.id">
          {{ item.name }} · {{ item.account }} · {{ sourceLabel(item) }}
        </option>
      </select>
    </label>
    <p v-else-if="loading" role="status">正在加载可用凭据…</p>
    <p v-if="error || retryError" role="alert" class="text-rose-700 dark:text-rose-300">{{ error || retryError }}</p>
    <p v-if="!loading && !error && !retryError && selectionMessages[selectionReason]">{{ selectionMessages[selectionReason] }}</p>
    <p v-else-if="!loading && !error && !retryError && !items.length">当前没有符合此工具权限和类型的凭据。</p>
    <div class="flex items-center gap-3">
      <a href="/dashboard/personal?tab=secrets" target="_blank" rel="noopener" class="underline">打开我的凭据</a>
      <button type="button" :disabled="disabled || loading || saving" class="underline disabled:opacity-50" @click="refresh()">刷新列表</button>
      <button v-if="creation && !creating" type="button" :disabled="disabled || loading || saving || !canCreate"
        class="underline disabled:opacity-50" @click="openForm">新建凭据</button>
    </div>
    <div v-if="creation && !creating" class="space-y-1">
      <p>{{ creation.type_name }} v{{ creation.version }} · {{ creation.host }}</p>
      <p v-if="!canCreate" class="text-amber-800 dark:text-amber-200">已禁用：{{ creation.creation_disabled_reason || '此类型暂不可新建，已有凭据仍可使用和维护。' }}</p>
    </div>
    <form v-if="creating && creation" class="space-y-3 rounded-lg bg-white p-3 text-slate-900" @submit.prevent="saveCredential">
      <p class="font-medium">{{ creation.type_name }} v{{ creation.version }} · {{ creation.host }}</p>
      <label class="block">凭据账号
        <input v-model="account" :readonly="!!creation.account_hint" maxlength="128" autocomplete="off"
          class="mt-1 w-full rounded border px-2 py-1.5" required />
      </label>
      <label class="block">名称
        <input v-model="name" maxlength="128" autocomplete="off" class="mt-1 w-full rounded border px-2 py-1.5" />
      </label>
      <CredentialFieldForm v-model="fields" :schema="creation.schema" />
      <p class="text-slate-600">凭据保存至凭据中心，然后继续当前工具调用。</p>
      <div class="flex justify-end gap-3">
        <button type="button" :disabled="saving" class="px-2 py-1" @click="clearForm">取消</button>
        <button type="submit" :disabled="disabled || saving || !complete"
          class="rounded bg-blue-600 px-3 py-1.5 text-white disabled:opacity-50">{{ saving ? '正在保存…' : '保存并继续' }}</button>
      </div>
    </form>
  </div>
</template>
