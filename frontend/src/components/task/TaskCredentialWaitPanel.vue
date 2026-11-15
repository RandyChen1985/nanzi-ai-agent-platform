<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { isAxiosError } from 'axios'
import axios from '@/utils/axios'
import { credentialError } from '@/composables/useCredentialFields'
import CredentialApprovalPicker from '@/components/secrets/CredentialApprovalPicker.vue'

type TaskWait = {
  request_id: string
  trace_id: string
  expires_at: string
  status: 'pending' | 'resuming' | 'completed' | 'cancelled' | 'expired' | 'failed'
}
const props = defineProps<{ taskId: number; taskName: string; traceId?: string }>()
const emit = defineEmits<{ close: []; updated: [] }>()
const wait = ref<TaskWait | null>(null)
const credentialId = ref('')
const rememberCredential = ref(true)
const busy = ref(false)
const submittingApproval = ref(false)
const error = ref('')
const labels: Record<TaskWait['status'], string> = {
  pending: '待选择凭据', resuming: '正在恢复', completed: '已恢复并完成',
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

function scheduleStatusUpdate() {
  const current = wait.value
  if (current?.status === 'pending') {
    const remaining = Date.parse(current.expires_at) - Date.now()
    if (Number.isFinite(remaining)) {
      timer = window.setTimeout(() => {
        timer = undefined
        if (wait.value !== current) return
        wait.value = { ...current, status: 'expired' }
        credentialId.value = ''
        emit('updated')
      }, Math.max(0, remaining))
    }
  } else if (current?.status === 'resuming' && pollsRemaining > 0) {
    timer = window.setTimeout(() => {
      timer = undefined
      pollsRemaining--
      void load(true)
    }, 2000)
  }
}

function waitForRun(next: TaskWait | null): TaskWait | null {
  return props.traceId && next?.trace_id !== props.traceId ? null : next
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
    const next = waitForRun(data.data)
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
      scheduleStatusUpdate()
    }
  }
}

async function resolve(cancelled: boolean, selectedId = credentialId.value) {
  if (wait.value?.status !== 'pending' || busy.value || (!cancelled && !selectedId)) return
  if (Date.parse(wait.value.expires_at) <= Date.now()) return
  stopPendingLoads()
  const taskId = props.taskId
  const requestId = wait.value.request_id
  const requestSequence = sequence
  let refreshStatus = false
  busy.value = true
  submittingApproval.value = !cancelled
  error.value = ''
  try {
    const { data } = await axios.post(statusUrl(taskId), {
      request_id: requestId, cancelled,
      ...(cancelled ? {} : { secret_id: selectedId, remember_credential: rememberCredential.value }),
    }, { timeout: 0 })
    if (!isCurrent(requestSequence, taskId) || wait.value?.request_id !== requestId) return
    wait.value = waitForRun(data.data)
    pollsRemaining = maxPolls
    emit('updated')
  } catch (err) {
    if (isCurrent(requestSequence, taskId)) {
      error.value = credentialError(err, '授权处理失败，请刷新状态')
      if (isAxiosError(err)) {
        const code = err.response?.data?.detail?.code
        refreshStatus = code === 'TASK_CREDENTIAL_WAIT_CLOSED' || code === 'TASK_CREDENTIAL_WAIT_EXPIRED'
      }
    }
  } finally {
    if (isCurrent(requestSequence, taskId)) {
      credentialId.value = ''
      busy.value = false
      submittingApproval.value = false
      if (refreshStatus) void load()
      else scheduleStatusUpdate()
    }
  }
}

watch([() => props.taskId, () => props.traceId], () => {
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
  <section aria-label="任务运行凭据" class="rounded-xl border border-amber-200 bg-white text-sm">
    <header class="flex items-start justify-between gap-3 border-b border-amber-100 px-4 py-3">
      <div>
        <h3 class="break-words font-semibold text-slate-900">{{ taskName }}</h3>
        <p class="mt-0.5 text-xs text-slate-600">本次运行的凭据</p>
      </div>
      <button type="button" class="shrink-0 rounded-md px-2 py-1 text-xs text-slate-700 hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-primary" @click="close">
        收起
      </button>
    </header>
    <div class="space-y-3 p-4">
      <p v-if="error" role="alert" class="rounded-lg bg-rose-50 px-3 py-2 text-rose-800">{{ error }}</p>
      <p v-if="wait" class="text-slate-700" role="status">
        {{ busy && wait.status === 'pending' ? '处理中…' : labels[wait.status] }}
        <span v-if="wait.status === 'pending'" class="ml-2 text-xs text-slate-500">有效期至 {{ new Date(wait.expires_at).toLocaleString() }}</span>
      </p>
      <p v-else-if="!error" class="text-slate-600" role="status">{{ busy ? '正在加载…' : '该次运行暂无凭据授权记录。' }}</p>
      <p v-if="submittingApproval || wait?.status === 'resuming'" class="text-slate-600">
        任务正在后台继续。稍后可以在执行记录中查看结果。
      </p>
      <template v-if="wait?.status === 'pending'">
        <CredentialApprovalPicker :key="wait.request_id" v-model="credentialId" :request-id="wait.request_id" :disabled="busy"
          @created="resolve(false, $event)" />
        <label class="flex items-start gap-2 text-slate-700">
          <input v-model="rememberCredential" type="checkbox" :disabled="busy" class="mt-0.5" />
          <span>此任务后续运行默认使用所选凭据。指定账号时按该账号查找。</span>
        </label>
      </template>
      <div class="flex flex-wrap justify-end gap-2">
        <button type="button" :disabled="busy" class="rounded-lg border px-3 py-1.5 disabled:opacity-50" @click="load()">刷新状态</button>
        <template v-if="wait?.status === 'pending'">
          <button type="button" :disabled="busy" class="rounded-lg border px-3 py-1.5 disabled:opacity-50" @click="resolve(true)">取消本次运行</button>
          <button type="button" :disabled="busy || !credentialId" class="rounded-lg bg-primary px-3 py-1.5 text-white disabled:opacity-50" @click="resolve(false)">授权并继续</button>
        </template>
      </div>
    </div>
  </section>
</template>
