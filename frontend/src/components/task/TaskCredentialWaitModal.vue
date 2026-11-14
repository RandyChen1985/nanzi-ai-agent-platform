<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
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
const submittingApproval = ref(false)
const error = ref('')
const labels: Record<TaskWait['status'], string> = {
  pending: '等待授权', resuming: '正在恢复', completed: '已恢复并完成',
  cancelled: '已取消', expired: '已过期', failed: '恢复后未完成',
}

const maxPolls = 15
let pollsRemaining = maxPolls
let sequence = 0
let timer: number | undefined
let activeLoad: AbortController | undefined

function stopPendingLoads() {
  sequence++
  activeLoad?.abort()
  activeLoad = undefined
  if (timer !== undefined) window.clearTimeout(timer)
  timer = undefined
}

function close() {
  stopPendingLoads()
  emit('close')
}

function statusUrl(taskId: number) {
  return '/api/v1/tasks/' + taskId + '/credential-wait'
}

function isCurrent(requestSequence: number, taskId: number) {
  return sequence === requestSequence && props.taskId === taskId
}

function pollWhileResuming() {
  if (wait.value?.status !== 'resuming' || pollsRemaining <= 0) return
  timer = window.setTimeout(() => {
    timer = undefined
    pollsRemaining--
    void load(true)
  }, 2000)
}

async function load(automatic = false) {
  if (busy.value) return
  stopPendingLoads()
  const taskId = props.taskId
  const requestSequence = sequence
  const previous = wait.value
  const controller = new AbortController()
  activeLoad = controller
  if (!automatic) {
    pollsRemaining = maxPolls
    busy.value = true
  }
  try {
    const { data } = await axios.get(statusUrl(taskId), { signal: controller.signal, timeout: 5000 })
    if (!isCurrent(requestSequence, taskId)) return
    const next: TaskWait | null = data.data
    if (previous?.request_id !== next?.request_id) credentialId.value = ''
    wait.value = next
    error.value = ''
    if (previous && (previous.request_id !== next?.request_id || previous.status !== next?.status)) emit('updated')
  } catch (err) {
    if (isCurrent(requestSequence, taskId)) error.value = credentialError(err, '无法加载授权状态')
  } finally {
    if (isCurrent(requestSequence, taskId)) {
      activeLoad = undefined
      busy.value = false
      pollWhileResuming()
    }
  }
}

async function resolve(cancelled: boolean, selectedId = credentialId.value) {
  if (wait.value?.status !== 'pending' || busy.value || (!cancelled && !selectedId)) return
  stopPendingLoads()
  const taskId = props.taskId
  const requestId = wait.value.request_id
  const requestSequence = sequence
  busy.value = true
  submittingApproval.value = !cancelled
  error.value = ''
  try {
    const { data } = await axios.post(statusUrl(taskId), {
      request_id: requestId, cancelled,
      ...(cancelled ? {} : { secret_id: selectedId, remember_credential: rememberCredential.value }),
    }, { timeout: 0 })
    if (!isCurrent(requestSequence, taskId) || wait.value?.request_id !== requestId) return
    wait.value = data.data
    pollsRemaining = maxPolls
    emit('updated')
  } catch (err) {
    if (isCurrent(requestSequence, taskId)) error.value = credentialError(err, '授权处理失败，请刷新状态')
  } finally {
    if (isCurrent(requestSequence, taskId)) {
      credentialId.value = ''
      busy.value = false
      submittingApproval.value = false
      pollWhileResuming()
    }
  }
}

watch(() => props.taskId, () => {
  wait.value = null
  credentialId.value = ''
  rememberCredential.value = true
  busy.value = false
  submittingApproval.value = false
  error.value = ''
  void load()
}, { immediate: true })
onBeforeUnmount(stopPendingLoads)
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-[1100] flex items-center justify-center bg-slate-950/50 p-4" @click.self="close">
      <section role="dialog" aria-modal="true" aria-label="任务凭据授权" class="w-full max-w-lg rounded-2xl bg-white shadow-xl">
        <header class="flex items-start justify-between border-b px-5 py-4">
          <div><h3 class="font-semibold text-slate-900">任务凭据授权</h3><p class="mt-1 text-sm text-slate-600">{{ taskName }}</p></div>
          <button class="rounded-lg px-3 py-1.5 text-sm hover:bg-slate-100" @click="close">关闭</button>
        </header>
        <div class="space-y-3 p-5 text-sm">
          <p v-if="error" role="alert" class="rounded-lg bg-rose-50 px-3 py-2 text-rose-800">{{ error }}</p>
          <p v-if="wait" class="text-slate-700" role="status">
            {{ busy && wait.status === 'pending' ? '处理中…' : labels[wait.status] }}
            <span v-if="wait.status === 'pending'" class="ml-2 text-xs text-slate-500">有效期至 {{ new Date(wait.expires_at).toLocaleString() }}</span>
          </p>
          <p v-else class="text-slate-500">{{ busy ? '正在加载…' : '该任务暂无凭据授权记录。' }}</p>
          <p v-if="submittingApproval || wait?.status === 'resuming'" class="text-slate-600">
            {{ submittingApproval ? '授权提交后，任务会在后台继续。你可以关闭此窗口。' : '任务正在后台继续。你可以关闭此窗口，稍后刷新状态。' }}
          </p>
          <template v-if="wait?.status === 'pending'">
            <label class="flex items-start gap-2 text-slate-700">
              <input v-model="rememberCredential" type="checkbox" :disabled="busy" class="mt-0.5" />
              <span>此任务后续运行默认使用所选凭据。指定账号时按该账号查找。</span>
            </label>
            <CredentialApprovalPicker :key="wait.request_id" v-model="credentialId" :request-id="wait.request_id" :disabled="busy"
              @created="resolve(false, $event)" />
          </template>
          <div class="flex flex-wrap justify-end gap-2">
            <button :disabled="busy" class="rounded-lg border px-3 py-1.5 disabled:opacity-50" @click="load()">刷新状态</button>
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
