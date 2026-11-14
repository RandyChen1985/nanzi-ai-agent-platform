<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import axios from '@/utils/axios'
import CredentialTypeEditor from './CredentialTypeEditor.vue'
import CredentialTypeFormPreview from './CredentialTypeFormPreview.vue'
import { credentialError, getCredentialVersion, isStructuredType, listCredentialTypes, type CredentialType, type CredentialSchema } from '@/composables/useCredentialFields'
import { credentialFieldKind, credentialFieldKinds, downloadCredentialSchema } from '@/utils/credentialSchemaDraft'

const props = withDefaults(defineProps<{ manage?: boolean }>(), { manage: false })
const emit = defineEmits<{ close: [] }>()

const types = ref<CredentialType[]>([])
const filter = ref<'all' | 'enabled' | 'disabled'>('all')
const selected = ref<CredentialType | null>(null)
const editor = ref<{ mode: 'create' | 'publish'; source?: CredentialType } | null>(null)
const preview = ref(false)
const busy = ref(false)
const error = ref('')
const closeButton = ref<HTMLButtonElement | null>(null)
const visible = computed(() => types.value.filter(item =>
  filter.value === 'all' || item.can_create === (filter.value === 'enabled'),
))
const versions = computed(() => Array.from({ length: selected.value?.latest_version || 0 }, (_, i) => i + 1).reverse())

async function load() {
  types.value = await listCredentialTypes(props.manage)
  select(types.value.find(type => type.type_key === selected.value?.type_key) ?? types.value[0] ?? null)
}

async function perform(task: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await task()
  } catch (err) {
    error.value = credentialError(err, '操作失败，请刷新后重试')
  } finally {
    busy.value = false
  }
}

function select(item: CredentialType | null) {
  selected.value = item
  editor.value = null
  preview.value = false
  error.value = ''
}

function changeVersion(event: Event) {
  const item = selected.value
  const version = Number((event.target as HTMLSelectElement).value)
  if (item) void perform(async () => select(await getCredentialVersion(item.type_key, version)))
}

function openEditor(mode: 'create' | 'publish', source?: CredentialType) {
  preview.value = false
  error.value = ''
  editor.value = { mode, source }
}

function close() {
  if (!busy.value) emit('close')
}

function cancelEditor() {
  select(selected.value)
  closeButton.value?.focus()
}

function save(draft: { type_key: string; name: string; enabled: boolean; schema: CredentialSchema }) {
  const current = editor.value
  if (!current) return
  void perform(async () => {
    const item = current.source
    const response = current.mode === 'publish' && item
      ? await axios.post('/api/portal/credential-types/' + item.type_key + '/versions', {
        schema: draft.schema, expected_revision: item.revision,
      })
      : await axios.post('/api/portal/credential-types', draft)
    const saved = response.data.data as CredentialType
    if (filter.value !== 'all' && saved.can_create !== (filter.value === 'enabled')) filter.value = 'all'
    select(saved)
    await load()
  })
}

function toggle(item: CredentialType) {
  void perform(async () => {
    await axios.patch('/api/portal/credential-types/' + item.type_key + '/status', {
      enabled: !item.enabled, expected_revision: item.revision,
    })
    await load()
  })
}

onMounted(() => {
  closeButton.value?.focus()
  void perform(load)
})
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-[1400] flex items-center justify-center bg-slate-950/50 p-3 sm:p-4" @click.self="close">
      <section role="dialog" aria-modal="true" aria-labelledby="credential-types-title"
        class="flex max-h-[90vh] w-full flex-col rounded-2xl bg-white text-sm shadow-xl"
        :class="editor ? 'max-w-6xl' : 'max-w-5xl'"
        @keydown.esc.stop="!busy && (editor ? cancelEditor() : close())">
        <header class="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3 sm:px-5 sm:py-4">
          <div>
            <h2 id="credential-types-title" class="text-lg font-semibold text-slate-900">凭据类型</h2>
            <p v-if="!editor" class="mt-1 text-slate-600">列表状态表示是否可新建。已有凭据仍按各自的授权和状态使用、维护。</p>
          </div>
          <div class="flex items-center gap-2">
            <button v-if="manage && !editor" type="button" :disabled="busy"
              class="rounded-lg bg-blue-600 px-3 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              @click="openEditor('create')">+ 自定义类型</button>
            <button ref="closeButton" type="button" :disabled="busy"
              class="rounded-lg px-3 py-2 text-slate-700 hover:bg-slate-100 disabled:opacity-50" @click="close">关闭</button>
          </div>
        </header>
        <div v-if="error" role="alert" class="mx-4 mt-3 rounded-lg bg-rose-50 px-3 py-2 text-rose-800 sm:mx-5">{{ error }}</div>
        <CredentialTypeEditor v-if="editor" :mode="editor.mode" :source="editor.source" :saving="busy"
          @cancel="cancelEditor" @save="save" />
        <div v-else class="min-h-0 overflow-y-auto p-4 sm:grid sm:grid-cols-[18rem_minmax(0,1fr)] sm:gap-5 sm:p-5">
          <div>
            <div v-if="manage" class="mb-3">
              <select v-model="filter" aria-label="按状态筛选" class="rounded-lg border px-2 py-1.5">
                <option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已禁用</option>
              </select>
            </div>
            <div class="divide-y rounded-lg border">
              <button v-for="item in visible" :key="item.type_key" :disabled="busy" type="button"
                :class="selected?.type_key === item.type_key ? 'bg-blue-50' : 'hover:bg-slate-50'"
                class="flex w-full items-start justify-between gap-2 px-3 py-2.5 text-left" @click="select(item)">
                <span class="min-w-0">
                  <b class="block break-words"><span v-if="item.schema['x-icon']" class="mr-1" aria-hidden="true">{{ item.schema['x-icon'] }}</span>{{ item.name }}</b>
                  <code class="break-all text-xs text-slate-600">{{ item.type_key }} · v{{ item.latest_version }}</code>
                </span>
                <span :class="item.can_create ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-600'"
                  :title="item.creation_disabled_reason || undefined"
                  class="shrink-0 rounded px-1.5 py-0.5 text-xs">{{ item.can_create ? '已启用' : '已禁用' }}</span>
              </button>
              <p v-if="!visible.length" class="p-3 text-slate-500">{{ busy ? '加载中…' : '没有符合条件的类型' }}</p>
            </div>
          </div>
          <div class="mt-4 min-w-0 space-y-3 sm:mt-0">
            <template v-if="selected">
              <div class="flex flex-wrap items-center gap-2">
                <h3 class="mr-auto break-words font-semibold">{{ selected.name }}</h3>
                <select :value="selected.version" aria-label="查看版本" :disabled="busy" class="rounded border p-1.5" @change="changeVersion">
                  <option v-for="version in versions" :key="version" :value="version">v{{ version }}</option>
                </select>
                <button v-if="manage" type="button" :disabled="busy" title="切换类型自身的新建配置，不改变全局开关" class="rounded-lg border px-2 py-1.5" @click="toggle(selected)">
                  {{ selected.enabled ? '禁用' : '启用' }}
                </button>
              </div>
              <p class="text-xs text-slate-600">类型配置：{{ selected.enabled ? '已启用' : '已禁用' }} · 当前新建：{{ selected.can_create ? '可创建' : '已禁用' }}</p>
              <p v-if="!selected.can_create" class="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
                {{ selected.creation_disabled_reason || '此类型暂不可新建，已有凭据仍可使用和维护。' }}。字段、版本、Schema 与表单预览仍可查看。
              </p>
              <dl class="divide-y rounded-lg border text-xs">
                <div v-for="(field, keyName) in selected.schema.properties" :key="keyName" class="grid grid-cols-3 gap-2 px-3 py-2">
                  <code class="break-all">{{ keyName }}</code><span class="break-words">{{ field.title || field.type }}</span>
                  <span>{{ credentialFieldKinds[credentialFieldKind(field)] }}{{ field.writeOnly ? ' · 敏感字段' : '' }}{{ selected.schema.required?.includes(keyName) ? ' · 必填' : '' }}</span>
                </div>
              </dl>
              <div class="flex flex-wrap gap-2">
                <button type="button" :aria-expanded="preview" class="rounded-lg border border-blue-200 px-3 py-1.5 text-blue-700 hover:bg-blue-50"
                  @click="preview = !preview">{{ preview ? '关闭预览' : '预览录入表单' }}</button>
                <button v-if="manage" type="button" class="rounded-lg border px-3 py-1.5"
                  @click="downloadCredentialSchema(selected.schema, selected.type_key)">导出 JSON Schema</button>
                <button v-if="manage" type="button" :disabled="busy" class="rounded-lg border px-3 py-1.5" @click="openEditor('create', selected)">复制为自定义</button>
                <button v-if="manage && selected.source === 'custom'" type="button" :disabled="busy"
                  class="rounded-lg border px-3 py-1.5" @click="openEditor('publish', selected)">发布新版本</button>
              </div>
              <CredentialTypeFormPreview v-if="preview" :schema="selected.schema" :show-account="isStructuredType(selected.type_key)" />
              <details class="text-xs">
                <summary class="cursor-pointer text-blue-700">查看 JSON Schema</summary>
                <pre class="mt-2 max-h-48 overflow-auto rounded-lg bg-slate-50 p-3">{{ JSON.stringify(selected.schema, null, 2) }}</pre>
              </details>
            </template>
          </div>
        </div>
      </section>
    </div>
  </Teleport>
</template>
