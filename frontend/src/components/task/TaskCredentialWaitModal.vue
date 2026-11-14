<script setup lang="ts">
import { onMounted, ref } from 'vue'
import axios from '@/utils/axios'
import { credentialError } from '@/composables/useCredentialFields'
import CredentialApprovalPicker from '@/components/secrets/CredentialApprovalPicker.vue'

type TaskWait = {
  request_id: string
  trace_id: string
  expires_at: string
  status: 'pending' | 'resuming' | 'completed' | 'cancelled' | 'expired' | 'failed'
}
const props = defineProps<{ taskId: number; taskName: string }>()
const emit = defineEmits<{ close: []; updated: [] }>()
const wait = ref<TaskWait | null>(null)
const credentialId = ref('')
const rememberCredential = ref(true)
const busy = ref(false)
const error = ref('')
const url = '/api/v1/tasks/' + props.taskId + '/credential-wait'
const labels: Record<TaskWait['status'], string> = {
  pending: '等待授权', resuming: '正在恢复', completed: '已恢复并完成',
  cancelled: '已取消', expired: '已过期', failed: '恢复后未完成',
}

async function load() {
  busy.value = true
  error.value = ''
  try {
    wait.value = (await axios.get(url)).data.data
  } catch (err) {
    error.value = credentialError(err, '无法加载授权状态')
  } finally {
    busy.value = false
  }
}

async function resolve(cancelled: boolean, selectedId = credentialId.value) {
  if (!wait.value || busy.value || (!cancelled && !selectedId)) return
  busy.value = true
  error.value = ''
  try {
    const { data } = await axios.post(url, {
      request_id: wait.value.request_id, cancelled,
      ...(cancelled ? {} : { secret_id: selectedId, remember_credential: rememberCredential.value }),
    }, { timeout: 0 })
    credentialId.value = ''
    wait.value = data.data
    emit('updated')
  } catch (err) {
    error.value = credentialError(err, '授权处理失败，请刷新状态')
    credentialId.value = ''
  } finally {
    busy.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-[1100] flex items-center justify-center bg-slate-950/50 p-4" @click.self="emit('close')">
      <section role="dialog" aria-modal="true" aria-label="任务凭据授权" class="w-full max-w-lg rounded-2xl bg-white shadow-xl">
        <header class="flex items-start justify-between border-b px-5 py-4">
          <div><h3 class="font-semibold text-slate-900">任务凭据授权</h3><p class="mt-1 text-sm text-slate-600">{{ taskName }}</p></div>
          <button class="rounded-lg px-3 py-1.5 text-sm hover:bg-slate-100" @click="emit('close')">关闭</button>
        </header>
        <div class="space-y-3 p-5 text-sm">
          <p v-if="error" role="alert" class="rounded-lg bg-rose-50 px-3 py-2 text-rose-800">{{ error }}</p>
          <p v-if="wait" class="text-slate-700">
            {{ busy && wait.status === 'pending' ? '处理中…' : labels[wait.status] }}
            <span v-if="wait.status === 'pending'" class="ml-2 text-xs text-slate-500">有效期至 {{ new Date(wait.expires_at).toLocaleString() }}</span>
          </p>
          <p v-else class="text-slate-500">{{ busy ? '正在加载…' : '该任务暂无凭据授权记录。' }}</p>
          <template v-if="wait?.status === 'pending'">
            <label class="flex items-start gap-2 text-slate-700">
              <input v-model="rememberCredential" type="checkbox" :disabled="busy" class="mt-0.5" />
              <span>此任务后续运行默认使用所选凭据。指定账号时按该账号查找。</span>
            </label>
            <CredentialApprovalPicker v-model="credentialId" :request-id="wait.request_id" :disabled="busy"
              @created="resolve(false, $event)" />
          </template>
          <div class="flex flex-wrap justify-end gap-2">
            <button :disabled="busy" class="rounded-lg border px-3 py-1.5 disabled:opacity-50" @click="load">刷新状态</button>
            <template v-if="wait?.status === 'pending'">
              <button :disabled="busy" class="rounded-lg border px-3 py-1.5 disabled:opacity-50" @click="resolve(true)">取消本次运行</button>
              <button :disabled="busy || !credentialId" class="rounded-lg bg-blue-600 px-3 py-1.5 text-white disabled:opacity-50" @click="resolve(false)">授权并继续</button>
            </template>
          </div>
        </div>
      </section>
    </div>
  </Teleport>
</template>
