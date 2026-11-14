<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { credentialError, type CredentialSchema, type CredentialType } from '@/composables/useCredentialFields'
import { buildCredentialSchema, credentialFieldRows, downloadCredentialSchema } from '@/utils/credentialSchemaDraft'
import CredentialSchemaFields from './CredentialSchemaFields.vue'
import CredentialTypeFormPreview from './CredentialTypeFormPreview.vue'

const props = defineProps<{ mode: 'create' | 'publish'; source?: CredentialType; saving: boolean }>()
const emit = defineEmits<{
  cancel: []
  save: [{ type_key: string; name: string; enabled: boolean; schema: CredentialSchema }]
}>()

const emptySchema: CredentialSchema = { type: 'object', properties: {}, required: [], additionalProperties: false }
const typeKey = ref('')
const name = ref('')
const enabled = ref(true)
const icon = ref('')
const base = ref<CredentialSchema>(emptySchema)
const fields = ref(credentialFieldRows(emptySchema))
const advanced = ref(false)
const schemaText = ref('')
let originalJson = ''
const showJson = ref(false)
const previewSchema = ref<CredentialSchema | null>(null)
const checked = ref(false)
const checking = ref(false)
const error = ref('')
const typeKeyInput = ref<HTMLInputElement | null>(null)
const actionClass = 'rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50'
const controlClass = 'mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 read-only:bg-slate-50'

const draft = computed<{ schema: CredentialSchema | null; error: string }>(() => {
  try {
    if (advanced.value) {
      const parsed = JSON.parse(schemaText.value)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { schema: null, error: 'JSON Schema 须为对象' }
      }
      return { schema: parsed, error: '' }
    }
    const schema = buildCredentialSchema(base.value, fields.value)
    if (icon.value.trim()) schema['x-icon'] = icon.value.trim()
    else delete schema['x-icon']
    return { schema, error: '' }
  } catch (err) {
    return {
      schema: null,
      error: err instanceof SyntaxError ? 'JSON Schema 格式无效' : err instanceof Error ? err.message : '字段定义无效',
    }
  }
})
const busy = computed(() => checking.value || props.saving)
const complete = computed(() => props.mode === 'publish' || (!!typeKey.value.trim() && !!name.value.trim()))

function loadDraft(schema: CredentialSchema) {
  base.value = schema
  icon.value = schema['x-icon'] || ''
  fields.value = credentialFieldRows(schema)
}

function reset() {
  typeKey.value = props.mode === 'publish' ? props.source?.type_key || '' : ''
  name.value = props.mode === 'publish' ? props.source?.name || '' : ''
  enabled.value = true
  loadDraft(props.source?.schema || emptySchema)
  advanced.value = false
  schemaText.value = ''
  originalJson = ''
  showJson.value = false
  previewSchema.value = null
  checked.value = false
  error.value = ''
}

reset()
onMounted(() => typeKeyInput.value?.focus())
watch(draft, () => {
  previewSchema.value = null
  checked.value = false
  error.value = ''
})

async function validatedSchema(): Promise<CredentialSchema | null> {
  const snapshot = draft.value
  error.value = snapshot.error
  checked.value = false
  if (!snapshot.schema || busy.value) return null
  checking.value = true
  try {
    const response = await axios.post('/api/portal/credential-types/validate', { schema: snapshot.schema })
    if (draft.value !== snapshot) return null
    checked.value = true
    return response.data.data
  } catch (err) {
    error.value = credentialError(err, '无法校验类型定义，请重试')
    return null
  } finally {
    checking.value = false
  }
}

async function switchMode() {
  if (!advanced.value) {
    const schema = draft.value.schema
    if (!schema) {
      error.value = draft.value.error
      return
    }
    originalJson = JSON.stringify(schema, null, 2)
    schemaText.value = originalJson
    advanced.value = true
  } else if (schemaText.value === originalJson) {
    advanced.value = false
  } else {
    const schema = await validatedSchema()
    if (schema) {
      loadDraft(schema)
      advanced.value = false
    }
  }
}

async function togglePreview() {
  previewSchema.value = previewSchema.value ? null : await validatedSchema()
}

async function importSchema(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    if (file.size > 64 * 1024) {
      error.value = 'JSON Schema 超出 64 KiB'
      return
    }
    schemaText.value = JSON.stringify(JSON.parse(await file.text()), null, 2)
    originalJson = ''
    advanced.value = true
  } catch (err) {
    error.value = err instanceof SyntaxError ? 'JSON Schema 格式无效' : '无法导入该文件'
  } finally {
    input.value = ''
  }
}

function save() {
  error.value = draft.value.error
  if (!complete.value || !draft.value.schema || busy.value) return
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(typeKey.value.trim())) {
    error.value = '类型标识须以小写字母开头，可含数字和下划线'
    return
  }
  emit('save', { type_key: typeKey.value.trim(), name: name.value.trim(), enabled: enabled.value, schema: draft.value.schema })
}
</script>

<template>
  <div class="flex min-h-0 min-w-0 flex-1 flex-col">
    <div class="min-h-0 overflow-y-auto p-4 sm:p-5">
      <fieldset :disabled="busy" class="min-w-0 space-y-5">
        <div>
          <h3 class="text-base font-semibold text-slate-900">{{ mode === 'create' ? '新建自定义类型' : '发布新版本' }}</h3>
          <p v-if="source" class="mt-1 text-xs text-slate-600">
            {{ mode === 'create' ? '复制自' : '基于' }} {{ source.name }} · v{{ source.version }}
          </p>
        </div>
        <div class="grid gap-3 sm:grid-cols-3">
          <label class="min-w-0 text-xs font-medium text-slate-700">
            类型标识 <span v-if="mode === 'create'" class="text-rose-600">*</span>
            <input ref="typeKeyInput" v-model="typeKey" maxlength="64" placeholder="k8s_cluster" spellcheck="false"
              :readonly="mode === 'publish'" :class="[controlClass, 'font-mono']" />
          </label>
          <label class="min-w-0 text-xs font-medium text-slate-700">
            显示名 <span v-if="mode === 'create'" class="text-rose-600">*</span>
            <input v-model="name" maxlength="128" placeholder="K8S 集群" :readonly="mode === 'publish'" :class="controlClass" />
          </label>
          <label v-if="!advanced" class="min-w-0 text-xs font-medium text-slate-700">
            图标（可空）
            <input v-model="icon" maxlength="32" placeholder="☸️" :class="controlClass" />
          </label>
          <label v-if="mode === 'create'" class="flex items-center gap-2 text-xs text-slate-700 sm:col-span-3">
            <input v-model="enabled" type="checkbox" />创建后启用
          </label>
        </div>

        <section aria-label="字段定义" class="space-y-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h4 class="font-semibold text-slate-900">字段定义</h4>
            <div class="flex flex-wrap gap-2">
              <button type="button" :class="actionClass" @click="switchMode">
                {{ advanced ? '切回可视化编辑' : '高级 JSON 编辑' }}
              </button>
              <button type="button" :aria-expanded="showJson" :class="actionClass" @click="showJson = !showJson">
                {{ showJson ? '隐藏生成的 JSON' : '查看生成的 JSON' }}
              </button>
            </div>
          </div>
          <p class="text-xs text-slate-600">密码、令牌等请标记为敏感字段。所有凭据字段均加密保存。</p>
          <CredentialSchemaFields v-if="!advanced" v-model="fields" />
          <label v-else class="block text-xs font-medium text-slate-700">
            JSON Schema
            <textarea v-model="schemaText" rows="13" spellcheck="false" :class="[controlClass, 'resize-y font-mono']" />
          </label>
          <div v-if="showJson" aria-label="生成的 JSON">
            <pre v-if="draft.schema" class="max-h-64 overflow-auto rounded-xl border bg-slate-50 p-3 text-xs">{{ JSON.stringify(draft.schema, null, 2) }}</pre>
            <p v-else class="rounded-lg bg-rose-50 p-3 text-xs text-rose-800">{{ draft.error }}</p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button type="button" :aria-expanded="!!previewSchema" :class="actionClass" @click="togglePreview">
              {{ previewSchema ? '关闭预览' : '预览录入表单' }}
            </button>
            <label :class="[actionClass, 'cursor-pointer focus-within:ring-2 focus-within:ring-blue-500']">
              导入 JSON Schema<input type="file" accept=".json,application/json" class="sr-only" @change="importSchema" />
            </label>
            <button type="button" :disabled="!draft.schema" :class="actionClass"
              @click="draft.schema && downloadCredentialSchema(draft.schema, typeKey)">
              导出 JSON Schema
            </button>
          </div>
          <CredentialTypeFormPreview v-if="previewSchema" :schema="previewSchema" show-account />
        </section>
      </fieldset>
    </div>
    <p v-if="error" role="alert" class="mx-4 mb-2 shrink-0 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-800 sm:mx-5">{{ error }}</p>
    <footer class="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t px-4 py-3 sm:px-5">
      <div class="flex flex-wrap items-center gap-2">
        <button type="button" :disabled="busy" :class="actionClass" @click="validatedSchema">校验 Schema</button>
        <span role="status" class="text-xs text-emerald-700">{{ checking ? '正在校验…' : checked ? '校验通过' : '' }}</span>
      </div>
      <div class="flex flex-wrap gap-2">
        <button type="button" :disabled="busy" :class="actionClass" @click="reset">重置</button>
        <button type="button" :disabled="busy" :class="actionClass" @click="emit('cancel')">取消</button>
        <button type="button" :disabled="busy || !complete"
          class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50"
          @click="save">{{ saving ? '保存中…' : mode === 'create' ? '保存类型' : '发布新版本' }}</button>
      </div>
    </footer>
  </div>
</template>
