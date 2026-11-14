<script setup lang="ts">
import { onMounted, ref } from 'vue'
import axios from '@/utils/axios'
import { credentialError } from '@/composables/useCredentialFields'
import { credentialProfileUrl, listMcpCredentialCandidates, type McpCredentialCandidates } from '@/utils/mcpCredentialProfiles'

const props = defineProps<{ toolId: string; toolName: string }>()
const emit = defineEmits<{ close: [] }>()
const data = ref<McpCredentialCandidates | null>(null)
const selected = ref('')
const busy = ref(false)
const error = ref('')

async function load() {
  busy.value = true
  try {
    data.value = await listMcpCredentialCandidates(props.toolId)
    selected.value = data.value.fixed_secret_id || ''
  } catch (err) {
    error.value = credentialError(err, '无法加载此工具的凭据')
  } finally {
    busy.value = false
  }
}
async function save() {
  if (busy.value || !data.value) return
  busy.value = true
  error.value = ''
  try {
    await axios.put(credentialProfileUrl(props.toolId) + '/fixed', {
      secret_id: selected.value || null, expected_revision: data.value.profile.revision,
      expected_profile_id: data.value.profile.profile_id,
    })
    emit('close')
  } catch (err) {
    error.value = credentialError(err, '无法保存固定凭据')
  } finally {
    busy.value = false
  }
}
onMounted(() => void load())
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-[110] flex items-center justify-center bg-slate-950/45 p-4" @click.self="emit('close')">
      <section role="dialog" aria-modal="true" aria-label="固定凭据" class="w-full max-w-lg space-y-3 rounded-2xl bg-white p-5 shadow-xl">
        <h3 class="font-semibold text-slate-900">为 {{ toolName }} 设置固定凭据</h3>
        <p class="text-xs text-slate-600">此设置应用于你调用该工具时的凭据选择。</p>
        <p v-if="error" role="alert" class="text-xs text-rose-800">{{ error }}</p>
        <select v-model="selected" :disabled="busy" aria-label="选择固定凭据" class="w-full rounded-lg border p-2 text-sm">
          <option value="">未绑定</option>
          <option v-for="item in data?.items" :key="item.id" :value="item.id">{{ item.name }} · {{ item.account }}{{ item.owner_scope === 'system' ? ' · 系统共享' : '' }}</option>
        </select>
        <div class="flex justify-end gap-2 text-sm">
          <button type="button" class="rounded-lg border px-3 py-1.5" @click="emit('close')">取消</button>
          <button type="button" :disabled="busy || !data" class="rounded-lg bg-blue-600 px-3 py-1.5 text-white disabled:opacity-50" @click="save">保存</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>
