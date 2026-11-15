<script setup lang="ts">
/**
 * 凭据值编辑器：自由输入 / 结构化模板 双档。
 *
 * 为什么单独成组件：个人凭据中心与管理端系统级凭据是两套页面，但「怎么录入一个值」
 * 必须是同一套规则——两边各写一遍，早晚会有一边漏掉 secret 声明，让密码漏进摘要。
 *
 * 三条不许破的边界：
 *
 * 1. **入库仍是字符串**：结构化只是把字段拼成 JSON 文本写回 `value`，父组件照旧
 *    提交一个字符串；加密、掩码、不可读这些约束一行都不变。
 * 2. **不预填示例值**：模板里的 example 只当 placeholder。预填等于「用户没动过的字段
 *    也有一条看起来像真值的数据」，而这条凭据提交后就再也改不了。
 * 3. **secret 字段名要交出去**：父组件把它作为 `value_secret_fields` 传给后端，
 *    后端据此把敏感字段从列表摘要里排除。漏交 = 摘要可能带出密码。
 */
import { computed, nextTick, ref, watch } from 'vue'
import {
  CREDENTIAL_JSON_TEMPLATE_ID,
  CREDENTIAL_TEMPLATES,
  CREDENTIAL_TEMPLATE_GROUPS,
  findCredentialTemplate,
  type CredentialField,
  type CredentialTemplate,
} from '../../utils/credentialTemplates'

const props = withDefaults(
  defineProps<{
    /** 只影响提示文案：变量可读可复制，凭证建完不可读 */
    secretType?: 'credential' | 'variable'
    /**
     * 轮换态：结构由既有凭据定死。
     *
     * 换模板会换掉整个字段集合，切回单值会把 JSON 拍平成一段文本——两者都会让
     * 别处已经写好的 `${secret:标识.字段}` 引用在无人察觉的情况下失效。轮换只
     * 该换值，不该换形状。
     */
    lockStructure?: boolean
  }>(),
  { secretType: 'credential', lockStructure: false },
)

/** 最终提交的那个字符串 */
const value = defineModel<string>({ default: '' })
/** text = 自由输入；json = 结构化（模板字段或自定义 JSON） */
const format = defineModel<'text' | 'json'>('format', { default: 'text' })
/** 结构化模板标识；custom 表示用户直接手编 JSON */
const template = defineModel<string | null>('template', { default: null })
/** 模板里声明为 secret 的字段名，交给后端做摘要排除 */
const secretFields = defineModel<string[]>('secretFields', { default: () => [] })
/** 值本身是否可提交（父组件的「下一步」用它） */
const valid = defineModel<boolean>('valid', { default: false })

const tpl = computed<CredentialTemplate | null>(() => findCredentialTemplate(template.value))
const isRaw = computed(() => Boolean(tpl.value?.raw))
const isStructured = computed(() => format.value === 'json')

// ── 编辑态 ───────────────────────────────────────────────────────────
const values = ref<Record<string, unknown>>({})
const advancedOpen = ref(false)
/** 自由输入档默认打码；点「显示」才看得清。只切样式，绝不回读已保存的值。 */
const revealPlain = ref(false)
/** 一次性提示：模式切换、键会被丢弃这类「用户需要知道但不算校验错误」的事 */
const notice = ref('')
const revealed = ref<Record<string, boolean>>({})
const jsonOpen = ref(false)
const pickerQuery = ref('')
const pickerGroup = ref('全部')
const pasteOpen = ref(false)
const pasteText = ref('')
const pasteMsg = ref('')
const pasteError = ref(false)

let pushingOut = false
/** 由 chooseTemplate 主动切换模板时置位，避免 watch(template) 再用旧文本回解一遍 */
let applyingTemplate = false

/**
 * 空值的形态。
 *
 * ``select`` 取第一个选项：界面上它本来就是「已选中」的样子，如果数据里留空，
 * 提交出去的 JSON 就会少一个用户以为已经填了的字段（SMTP 的加密方式就是这样漏的）。
 * 显示与落库必须一致，要么都空、要么都有。
 */
function emptyValueOf(field: CredentialField): unknown {
  if (field.type === 'switch') return false
  if (field.type === 'select') return field.options?.[0]?.value ?? ''
  return ''
}

/** 只保留有内容的字段，数字转 Number：写进 JSON 的应该是 465 而不是 "465"。 */
function pickedValues(): Record<string, unknown> {
  const current = tpl.value
  const out: Record<string, unknown> = {}
  if (!current || current.raw) return out
  for (const field of current.fields) {
    if (field.showIf && !field.showIf(values.value)) continue
    const raw = values.value[field.key]
    if (raw === undefined || raw === null) continue
    if (typeof raw === 'string' && raw.trim() === '') continue
    if (field.type === 'number') {
      const num = Number(raw)
      if (Number.isNaN(num)) continue
      out[field.key] = num
    } else if (field.type === 'switch') {
      out[field.key] = Boolean(raw)
    } else {
      out[field.key] = raw
    }
  }
  return out
}

function serialize(): string {
  const picked = pickedValues()
  return Object.keys(picked).length ? JSON.stringify(picked, null, 2) : ''
}

function pushOut() {
  if (!isStructured.value || isRaw.value) return
  pushingOut = true
  value.value = serialize()
  nextTick(() => {
    pushingOut = false
  })
}

/** 用一段 JSON 回填表单：只认模板里认识的名字，其余键不动（避免悄悄丢数据） */
function hydrate(text: string) {
  const current = tpl.value
  if (!current || current.raw) return
  let parsed: unknown
  try {
    parsed = JSON.parse(text || '{}')
  } catch {
    return
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return
  const source = parsed as Record<string, unknown>
  const next: Record<string, unknown> = {}
  for (const field of current.fields) {
    next[field.key] = source[field.key] === undefined ? emptyValueOf(field) : source[field.key]
  }
  values.value = next
}

/** 模板里所有 secret 字段（含当前隐藏的）：值里可能残留，摘要必须一律排除 */
const computedSecretFields = computed<string[]>(() => {
  const current = tpl.value
  if (!isStructured.value || !current) return []
  return current.fields.filter((field) => field.secret).map((field) => field.key)
})

watch(
  computedSecretFields,
  (next) => {
    secretFields.value = next
  },
  { immediate: true },
)

watch(
  values,
  () => pushOut(),
  { deep: true },
)

watch(value, (next) => {
  if (!isStructured.value || isRaw.value || pushingOut) return
  const current = tpl.value
  if (!current) return
  // 外部塞进来的值（轮换回填、草稿恢复）才需要回解；本组件自己推出去的跳过
  if (next === serialize()) return
  hydrate(next)
})

watch(
  template,
  (next) => {
    if (applyingTemplate) return
    const current = findCredentialTemplate(next)
    if (!current) {
      values.value = {}
      return
    }
    if (current.raw) {
      // 自定义 JSON：沿用当前文本，语法合法才算可提交（不自动塞占位内容）
      return
    }
    // 挂载与外部改模板都走这里：用当前文本回解，空文本就铺默认值
    hydrate(value.value)
  },
  { immediate: true },
)

// ── 校验 ─────────────────────────────────────────────────────────────
function fieldError(field: CredentialField, raw: unknown): string | null {
  const text = raw === undefined || raw === null ? '' : String(raw).trim()
  if (field.required && text === '') return '这个字段必填'
  if (text === '') return null
  if (field.type === 'number' && Number.isNaN(Number(text))) return '需要填数字'
  if (field.type === 'url' && !/^https?:\/\//.test(text)) return '需要以 http:// 或 https:// 开头'
  return null
}

const errors = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  if (!isStructured.value) return out
  const current = tpl.value
  if (!current) return out
  if (current.raw) {
    const text = value.value.trim()
    // 空文本要报错而不是放行：否则会出现「显示可以提交、按钮却是灰的」
    if (!text) {
      out.__raw = '需要填写一个 JSON 对象'
      return out
    }
    try {
      const parsed = JSON.parse(text)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        out.__raw = '顶层必须是 JSON 对象（不支持数组）'
      } else if (Object.keys(parsed).length === 0) {
        // 空对象和「什么都没填」是一回事，不该显示「可以提交」
        out.__raw = '至少要有一个字段，不能是空对象'
      }
    } catch (err) {
      out.__raw = `JSON 语法错误：${String((err as Error).message).slice(0, 60)}`
    }
    return out
  }
  for (const field of visibleFields(current)) {
    const message = fieldError(field, values.value[field.key])
    if (message) out[field.key] = message
  }
  return out
})

const isValid = computed(() => {
  if (!isStructured.value) return value.value.trim().length > 0
  const current = tpl.value
  if (!current) return false
  if (Object.keys(errors.value).length) return false
  if (current.raw) return Object.keys(errors.value).length === 0
  return Object.keys(pickedValues()).length > 0
})

watch(
  isValid,
  (next) => {
    valid.value = next
  },
  { immediate: true },
)

// ── 模板操作 ─────────────────────────────────────────────────────────
function visibleFields(current: CredentialTemplate): CredentialField[] {
  return current.fields.filter((field) => !field.showIf || field.showIf(values.value))
}

const mainFields = computed(() => (tpl.value ? visibleFields(tpl.value).filter((f) => !f.advanced) : []))
const advancedFields = computed(() => (tpl.value ? visibleFields(tpl.value).filter((f) => f.advanced) : []))

const filteredTemplates = computed(() => {
  const query = pickerQuery.value.trim().toLowerCase()
  return CREDENTIAL_TEMPLATES.filter((item) => {
    if (pickerGroup.value !== '全部' && item.group !== pickerGroup.value) return false
    if (!query) return true
    const haystack = `${item.name} ${item.desc} ${item.group} ${item.fields.map((f) => `${f.key} ${f.label}`).join(' ')}`
    return haystack.toLowerCase().includes(query)
  })
})

function chooseTemplate(id: string) {
  if (props.lockStructure) return
  const next = findCredentialTemplate(id)
  if (!next) return
  const previous = values.value
  const carried: Record<string, unknown> = {}
  for (const field of next.fields) {
    const old = previous[field.key]
    if (old !== undefined && old !== '' && old !== false) carried[field.key] = old
  }
  values.value = {}
  for (const field of next.fields) {
    values.value[field.key] = carried[field.key] !== undefined ? carried[field.key] : emptyValueOf(field)
  }
  advancedOpen.value = false
  notice.value = ''
  // 换模板后「已显示」不再有意义：同名 password 字段会在新表单里直接明文可见
  revealed.value = {}
  applyingTemplate = true
  template.value = next.id
  nextTick(() => {
    applyingTemplate = false
  })
  if (next.raw) {
    // 不预填 `{}`：预填会让「一个字段都没写」看起来可以提交
    value.value = value.value.trim()
    return
  }
  pushOut()
}

function clearTemplate() {
  if (props.lockStructure) return
  template.value = null
  values.value = {}
  value.value = ''
  // 提示与「已显示」都是上一条模板留下的状态，不清会张冠李戴
  notice.value = ''
  revealed.value = {}
}

function clearFields() {
  if (props.lockStructure) return
  const current = tpl.value
  if (!current) return
  // 先清状态再分支：raw 分支会提前 return，写在末尾就漏了
  notice.value = ''
  // 清空后密码框不该还停在明文状态
  revealed.value = {}
  if (current.raw) {
    value.value = ''
    return
  }
  const next: Record<string, unknown> = {}
  for (const field of current.fields) next[field.key] = emptyValueOf(field)
  values.value = next
  advancedOpen.value = false
}

function setField(field: CredentialField, raw: string) {
  if (field.type === 'number') {
    // 输入到一半（"-" / "1."）或填了字母时保留原文，让校验给出人话，
    // 而不是把 NaN 一路带进表单和预览
    const num = Number(raw)
    values.value[field.key] = raw === '' ? '' : Number.isNaN(num) ? raw : num
  } else {
    values.value[field.key] = raw
  }
  const current = tpl.value
  if (!current) return
  const linked = current.fields.find((item) => item.auto && item.auto.when === field.key)
  if (linked && linked.auto && linked.auto.map[raw] !== undefined) {
    values.value[linked.key] = linked.auto.map[raw]
  }
}

function toggleSwitch(field: CredentialField) {
  values.value[field.key] = !values.value[field.key]
}

function toggleReveal(key: string) {
  revealed.value[key] = !revealed.value[key]
}

// ── 模式切换 ─────────────────────────────────────────────────────────

/** 解析成 JSON 对象；数组、标量、语法错误都返回 null。 */
function parseObjectText(text: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    return parsed as Record<string, unknown>
  } catch {
    return null
  }
}

/**
 * 按模板字段把一份现成的对象回填进表单。
 *
 * 必须走这一步：`chooseTemplate` 只从**当前**表单值继承，而切模式时表单是空的，
 * 光调它会把用户刚写的 JSON 换成一份默认值——数据就这么丢了。
 */
function fillTemplate(templateId: string, source: Record<string, unknown>) {
  chooseTemplate(templateId)
  const current = findCredentialTemplate(templateId)
  const next: Record<string, unknown> = {}
  for (const field of current?.fields || []) {
    next[field.key] = source[field.key] === undefined ? emptyValueOf(field) : source[field.key]
  }
  values.value = next
  pushOut()
}

function switchFormat(next: 'text' | 'json') {
  if (props.lockStructure) return
  if (next === format.value) return
  notice.value = ''

  if (next !== 'json') {
    // 回到自由输入：值原样保留（就是刚才那份 JSON 文本），用户自己决定要不要改
    format.value = 'text'
    return
  }

  const raw = value.value
  const text = raw.trim()
  format.value = 'json'

  if (!text) {
    // 空值：沿用已选模板并铺默认值；没选过模板就等用户挑
    if (template.value) fillTemplate(template.value, {})
    return
  }

  const source = parseObjectText(text)
  if (!source) {
    // 不是 JSON 对象（PEM / 裸 token）：交给「自定义 JSON」承载原文，绝不静默清空
    chooseTemplate(CREDENTIAL_JSON_TEMPLATE_ID)
    // 写回原文而不是 trim 后的：PEM 这类内容的缩进与空行是有意义的
    value.value = raw
    notice.value = '当前内容不是 JSON 对象，已原样放进「自定义 JSON」，改好才能提交；也可以切回自由输入'
    return
  }

  const recognized = recognize(text)
  if (!recognized) {
    // 是合法 JSON，只是没有对应的内置模板——这不是错误，别吓用户
    chooseTemplate(CREDENTIAL_JSON_TEMPLATE_ID)
    // 写回原文而不是 trim 后的：PEM 这类内容的缩进与空行是有意义的
    value.value = raw
    notice.value = '这段 JSON 没有对应的内置模板，已原样放进「自定义 JSON」（键会完整保留）；想用表单可以手动选一个模板'
    return
  }

  // 关键：把 text 里的值回填进表单，而不是让模板默认值覆盖掉
  fillTemplate(recognized, source)
  const extra = extraKeys(text, recognized)
  if (extra.length) {
    const name = findCredentialTemplate(recognized)?.name || recognized
    notice.value = `已按「${name}」回填；这些键不在模板里，保存时会丢弃：${extra.join('、')}。需要原样保留请改用「自定义 JSON」`
  }
}

/** 这段 JSON 里有、但模板不认识的键 */
function extraKeys(text: string, templateId: string): string[] {
  const current = findCredentialTemplate(templateId)
  if (!current || current.raw) return []
  try {
    const parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return []
    const known = new Set(current.fields.map((item) => item.key))
    return Object.keys(parsed as Record<string, unknown>).filter((key) => !known.has(key))
  } catch {
    return []
  }
}

// ── 粘贴 JSON 识别 ───────────────────────────────────────────────────
/** 按 key 命中率猜模板：命中越多越像，全部落空返回 null */
function recognize(text: string): string | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const keys = Object.keys(parsed as Record<string, unknown>)
  let best: string | null = null
  let bestScore = 0
  for (const item of CREDENTIAL_TEMPLATES) {
    if (item.raw) continue
    const known = new Set(item.fields.map((f) => f.key))
    const hit = keys.filter((key) => known.has(key)).length
    const score = hit / Math.max(known.size, 1) + hit * 0.01
    if (score > bestScore) {
      bestScore = score
      best = item.id
    }
  }
  return best
}

function togglePaste() {
  pasteOpen.value = !pasteOpen.value
  pasteMsg.value = ''
  pasteError.value = false
}

function applyPasted() {
  const text = pasteText.value.trim()
  if (!text) {
    pasteError.value = true
    pasteMsg.value = '先粘一段 JSON 进来'
    return
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (err) {
    pasteError.value = true
    pasteMsg.value = `解析失败：${String((err as Error).message).slice(0, 70)}`
    return
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    pasteError.value = true
    pasteMsg.value = '顶层需要是对象（不要数组）'
    return
  }
  const source = parsed as Record<string, unknown>
  const matched = recognize(text)
  if (!matched) {
    pasteError.value = true
    pasteMsg.value = '没有匹配到内置模板——可以用「自定义 JSON」把这段结构直接存下来'
    return
  }
  fillTemplate(matched, source)
  const current = findCredentialTemplate(matched)
  const known = new Set((current?.fields || []).map((f) => f.key))
  const extra = Object.keys(source).filter((key) => !known.has(key))
  pasteError.value = extra.length > 0
  pasteMsg.value = extra.length
    ? `匹配到「${current?.name}」，但 ${extra.join('、')} 不在模板里，不会被保存；需要的话改用「自定义 JSON」`
    : `匹配到「${current?.name}」，已回填 ${Object.keys(source).length} 个键`
}

const jsonPreview = computed(() => {
  if (!isStructured.value) return ''
  if (isRaw.value) return value.value
  const picked = pickedValues()
  return Object.keys(picked).length ? JSON.stringify(picked, null, 2) : '// 还没有填写任何字段'
})

const jsonSize = computed(() => new Blob([jsonPreview.value]).size)
const jsonFieldCount = computed(() => (isRaw.value ? Object.keys(parseRawKeys()).length : Object.keys(pickedValues()).length))

function parseRawKeys(): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value.value || '{}')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

const errorCount = computed(() => Object.keys(errors.value).length)

function fieldPlaceholder(field: CredentialField): string {
  if (field.placeholder) return field.placeholder
  if (field.example === undefined) return ''
  return String(field.example)
}

defineExpose({ recognize })
</script>

<template>
  <div class="rounded-xl border border-blue-100 bg-[#f7fbff] p-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <span class="text-xs font-medium text-gray-700">
        凭据值 <span class="text-rose-500">*</span>
      </span>
      <div v-if="lockStructure" class="text-[11px] text-gray-500">
        {{ format === 'json' ? '结构化（模板）' : '自由输入' }}
        <span class="ml-1 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-600">轮换中不可改结构</span>
      </div>
      <div v-else class="inline-flex rounded-lg border border-gray-200 bg-gray-100 p-0.5">
        <button
          type="button"
          class="rounded-md px-2.5 py-1 text-[11px]"
          :class="format === 'text' ? 'bg-white font-semibold text-blue-700 shadow-sm' : 'text-gray-600'"
          @click="switchFormat('text')"
        >自由输入</button>
        <button
          type="button"
          class="rounded-md px-2.5 py-1 text-[11px]"
          :class="format === 'json' ? 'bg-white font-semibold text-blue-700 shadow-sm' : 'text-gray-600'"
          @click="switchFormat('json')"
        >结构化（模板）</button>
      </div>
    </div>

    <p
      v-if="notice"
      class="mt-1.5 rounded-md bg-amber-50 px-2 py-1 text-[11px] leading-snug text-amber-700"
    >{{ notice }}</p>

    <!-- 自由输入 -->
    <div v-if="format === 'text'" class="mt-2">
      <div class="mb-1 flex items-center justify-end">
        <button
          type="button"
          class="rounded px-1.5 py-0.5 text-[10.5px] text-gray-500 hover:bg-gray-100 hover:text-gray-700"
          @click="revealPlain = !revealPlain"
        >{{ revealPlain ? '隐藏' : '显示' }}</button>
      </div>
      <textarea
        v-model="value"
        rows="4"
        spellcheck="false"
        class="w-full resize-y rounded-lg border border-gray-300 bg-white px-3 py-2 font-mono text-xs leading-relaxed focus:border-blue-500 focus:outline-none"
        :class="{ 'masked-value': !revealPlain }"
        placeholder="短 token 直接粘贴；JSON / PEM 这类多行值可以整段贴进来"
      />
      <p class="mt-1 text-[11px] text-gray-500">
        原样保留，不会被 trim。只想存一个字符串（token、私钥 PEM）时用这档就够了。
      </p>
    </div>

    <!-- 结构化 -->
    <div v-else class="mt-2 space-y-2">
      <!-- 还没选模板：选择器 -->
      <div v-if="!tpl" class="overflow-hidden rounded-lg border border-gray-200 bg-white">
        <div class="flex items-center gap-2 border-b border-gray-100 bg-gray-50 px-2.5 py-2">
          <input
            v-model="pickerQuery"
            class="min-w-0 flex-1 rounded-md border border-gray-200 px-2 py-1 text-[11px] outline-none focus:border-blue-400"
            placeholder="搜模板：smtp / 邮件 / sftp / 数据库 / webhook …"
          />
          <span class="shrink-0 text-[10px] text-gray-400">{{ filteredTemplates.length }} 个</span>
        </div>
        <div class="flex flex-wrap gap-1 px-2.5 pt-2">
          <button
            v-for="group in CREDENTIAL_TEMPLATE_GROUPS"
            :key="group"
            type="button"
            class="rounded-full border px-2 py-0.5 text-[10.5px]"
            :class="pickerGroup === group
              ? 'border-blue-600 bg-blue-600 font-semibold text-white'
              : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'"
            @click="pickerGroup = group"
          >{{ group }}</button>
        </div>
        <div class="grid max-h-[236px] gap-1.5 overflow-auto p-2.5 sm:grid-cols-2">
          <button
            v-for="item in filteredTemplates"
            :key="item.id"
            type="button"
            class="rounded-lg border p-2 text-left transition hover:border-blue-200 hover:bg-blue-50/60"
            :class="template === item.id ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'"
            @click="chooseTemplate(item.id)"
          >
            <span class="flex items-center gap-1.5">
              <span class="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-gray-100 text-[10px]">{{ item.icon }}</span>
              <span class="truncate text-[11.5px] font-semibold text-gray-900">{{ item.name }}</span>
            </span>
            <span class="mt-0.5 block truncate text-[10.5px] text-gray-500">{{ item.desc }}</span>
            <span class="mt-0.5 block text-[10px] text-gray-400">{{ item.raw ? '自由结构' : `${item.fields.length} 个字段` }}</span>
          </button>
        </div>
      </div>

      <!-- 已选模板 -->
      <template v-else>
        <div class="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5">
          <span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-blue-50 text-xs text-blue-700">{{ tpl.icon }}</span>
          <span class="min-w-0 flex-1">
            <span class="block truncate text-[11.5px] font-semibold text-gray-900">{{ tpl.name }}</span>
            <span class="block truncate text-[10.5px] text-gray-500">{{ tpl.desc }}</span>
          </span>
          <template v-if="!lockStructure">
            <button type="button" class="shrink-0 rounded border border-gray-200 px-1.5 py-0.5 text-[10px] text-gray-600 hover:bg-gray-50" @click="clearTemplate">换模板</button>
            <button type="button" class="shrink-0 rounded border border-gray-200 px-1.5 py-0.5 text-[10px] text-gray-600 hover:bg-gray-50" @click="clearFields">清空</button>
          </template>
        </div>

        <!-- 自定义 JSON：直接手编 -->
        <div v-if="isRaw" class="rounded-lg border border-gray-200 bg-white p-2.5">
          <p class="mb-1 text-[11px] font-medium text-gray-700">直接写 JSON 对象 <span class="text-rose-500">*</span></p>
          <textarea
            v-model="value"
            rows="8"
            spellcheck="false"
            class="w-full resize-y rounded-md border px-2 py-1.5 font-mono text-xs leading-relaxed focus:outline-none"
            :class="errors.__raw ? 'border-rose-500' : 'border-gray-300 focus:border-blue-500'"
          />
          <p v-if="errors.__raw" class="mt-1 text-[11px] text-rose-600">{{ errors.__raw }}</p>
          <p v-else class="mt-1 text-[11px] text-gray-500">顶层必须是对象；库里只存这一段文本，不校验字段结构。</p>
        </div>

        <!-- 模板字段 -->
        <div v-else class="rounded-lg border border-gray-200 bg-white p-2.5">
          <div class="grid gap-2.5 sm:grid-cols-2">
            <div v-for="field in mainFields" :key="field.key" :class="field.full ? 'sm:col-span-2' : ''">
              <div class="mb-1 flex items-center justify-between gap-1">
                <span class="text-[11px] font-medium text-gray-700">
                  {{ field.label }}<span v-if="field.required" class="ml-0.5 text-rose-500">*</span>
                </span>
                <span v-if="field.secret" class="rounded bg-amber-50 px-1 py-0.5 text-[9.5px] font-medium text-amber-700">敏感</span>
              </div>

              <select
                v-if="field.type === 'select'"
                class="w-full rounded-md border px-2 py-1.5 text-xs focus:outline-none"
                :class="errors[field.key] ? 'border-rose-500' : 'border-gray-300 focus:border-blue-500'"
                :value="String(values[field.key] ?? '')"
                @change="setField(field, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="option in field.options || []" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>

              <div v-else-if="field.type === 'switch'" class="flex items-center gap-2 py-1">
                <button
                  type="button"
                  class="relative h-[19px] w-[34px] shrink-0 rounded-full transition-colors"
                  :class="values[field.key] ? 'bg-blue-600' : 'bg-gray-300'"
                  @click="toggleSwitch(field)"
                >
                  <span
                    class="absolute top-[2px] h-[15px] w-[15px] rounded-full bg-white transition-all"
                    :class="values[field.key] ? 'left-[17px]' : 'left-[2px]'"
                  />
                </button>
                <span class="text-[11px] text-gray-600">{{ values[field.key] ? '已开启' : '未开启' }}</span>
              </div>

              <textarea
                v-else-if="field.type === 'textarea'"
                rows="4"
                spellcheck="false"
                class="w-full resize-y rounded-md border px-2 py-1.5 font-mono text-xs leading-relaxed focus:outline-none"
                :class="errors[field.key] ? 'border-rose-500' : 'border-gray-300 focus:border-blue-500'"
                :value="String(values[field.key] ?? '')"
                :placeholder="field.secret ? '整段粘贴，包含首尾的 BEGIN / END 行' : ''"
                @input="setField(field, ($event.target as HTMLTextAreaElement).value)"
              />

              <div v-else-if="field.type === 'password'" class="relative">
                <input
                  :type="revealed[field.key] ? 'text' : 'password'"
                  class="w-full rounded-md border px-2 py-1.5 pr-11 font-mono text-xs focus:outline-none"
                  :class="errors[field.key] ? 'border-rose-500' : 'border-gray-300 focus:border-blue-500'"
                  autocomplete="off"
                  :value="String(values[field.key] ?? '')"
                  :placeholder="fieldPlaceholder(field)"
                  @input="setField(field, ($event.target as HTMLInputElement).value)"
                />
                <button
                  type="button"
                  class="absolute right-1 top-1/2 -translate-y-1/2 rounded px-1.5 py-0.5 text-[10.5px] text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                  @click="toggleReveal(field.key)"
                >{{ revealed[field.key] ? '隐藏' : '显示' }}</button>
              </div>

              <input
                v-else
                :type="field.type === 'number' ? 'number' : 'text'"
                class="w-full rounded-md border px-2 py-1.5 text-xs focus:outline-none"
                :class="[errors[field.key] ? 'border-rose-500' : 'border-gray-300 focus:border-blue-500', field.type === 'url' ? 'font-mono' : '']"
                :value="String(values[field.key] ?? '')"
                :placeholder="fieldPlaceholder(field)"
                @input="setField(field, ($event.target as HTMLInputElement).value)"
              />

              <p v-if="errors[field.key]" class="mt-1 text-[11px] text-rose-600">{{ errors[field.key] }}</p>
              <p v-else-if="field.hint" class="mt-1 text-[11px] leading-snug text-gray-500">{{ field.hint }}</p>
            </div>
          </div>

          <button
            v-if="advancedFields.length"
            type="button"
            class="mt-2 text-[11px] text-blue-700 hover:underline"
            @click="advancedOpen = !advancedOpen"
          >
            {{ advancedOpen ? '收起' : '展开' }} {{ advancedFields.length }} 个高级字段
            <span class="text-gray-400">（{{ advancedFields.map((f) => f.label).join('、') }}）</span>
          </button>

          <div v-if="advancedOpen" class="mt-2 grid gap-2.5 border-t border-dashed border-gray-200 pt-2.5 sm:grid-cols-2">
            <div v-for="field in advancedFields" :key="field.key" :class="field.full ? 'sm:col-span-2' : ''">
              <div class="mb-1 flex items-center justify-between gap-1">
                <span class="text-[11px] font-medium text-gray-700">{{ field.label }}</span>
                <span v-if="field.secret" class="rounded bg-amber-50 px-1 py-0.5 text-[9.5px] font-medium text-amber-700">敏感</span>
              </div>

              <select
                v-if="field.type === 'select'"
                class="w-full rounded-md border px-2 py-1.5 text-xs focus:outline-none"
                :class="errors[field.key] ? 'border-rose-500' : 'border-gray-300 focus:border-blue-500'"
                :value="String(values[field.key] ?? '')"
                @change="setField(field, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="option in field.options || []" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>

              <div v-else-if="field.type === 'switch'" class="flex items-center gap-2 py-1">
                <button
                  type="button"
                  class="relative h-[19px] w-[34px] shrink-0 rounded-full transition-colors"
                  :class="values[field.key] ? 'bg-blue-600' : 'bg-gray-300'"
                  @click="toggleSwitch(field)"
                >
                  <span
                    class="absolute top-[2px] h-[15px] w-[15px] rounded-full bg-white transition-all"
                    :class="values[field.key] ? 'left-[17px]' : 'left-[2px]'"
                  />
                </button>
                <span class="text-[11px] text-gray-600">{{ values[field.key] ? '已开启' : '未开启' }}</span>
              </div>

              <textarea
                v-else-if="field.type === 'textarea'"
                rows="3"
                spellcheck="false"
                class="w-full resize-y rounded-md border px-2 py-1.5 font-mono text-xs focus:outline-none"
                :class="errors[field.key] ? 'border-rose-500' : 'border-gray-300 focus:border-blue-500'"
                :value="String(values[field.key] ?? '')"
                @input="setField(field, ($event.target as HTMLTextAreaElement).value)"
              />

              <div v-else-if="field.type === 'password'" class="relative">
                <input
                  :type="revealed[field.key] ? 'text' : 'password'"
                  class="w-full rounded-md border px-2 py-1.5 pr-11 font-mono text-xs focus:outline-none"
                  :class="errors[field.key] ? 'border-rose-500' : 'border-gray-300 focus:border-blue-500'"
                  autocomplete="off"
                  :value="String(values[field.key] ?? '')"
                  :placeholder="fieldPlaceholder(field)"
                  @input="setField(field, ($event.target as HTMLInputElement).value)"
                />
                <button
                  type="button"
                  class="absolute right-1 top-1/2 -translate-y-1/2 rounded px-1.5 py-0.5 text-[10.5px] text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                  @click="toggleReveal(field.key)"
                >{{ revealed[field.key] ? '隐藏' : '显示' }}</button>
              </div>

              <input
                v-else
                :type="field.type === 'number' ? 'number' : 'text'"
                class="w-full rounded-md border px-2 py-1.5 text-xs focus:outline-none"
                :class="errors[field.key] ? 'border-rose-500' : 'border-gray-300 focus:border-blue-500'"
                :value="String(values[field.key] ?? '')"
                :placeholder="fieldPlaceholder(field)"
                @input="setField(field, ($event.target as HTMLInputElement).value)"
              />

              <p v-if="errors[field.key]" class="mt-1 text-[11px] text-rose-600">{{ errors[field.key] }}</p>
              <p v-else-if="field.hint" class="mt-1 text-[11px] leading-snug text-gray-500">{{ field.hint }}</p>
            </div>
          </div>
        </div>

        <!-- JSON 预览（收起状态也给字段数与体积，方便对账） -->
        <div class="rounded-lg border border-gray-200 bg-white">
          <button
            type="button"
            class="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[11px] text-gray-600 hover:bg-gray-50"
            @click="jsonOpen = !jsonOpen"
          >
            <span class="text-gray-400">{{ jsonOpen ? '▾' : '▸' }}</span>
            <span class="font-medium text-gray-700">入库内容</span>
            <span class="text-gray-400">{{ jsonFieldCount }} 个字段 · {{ jsonSize }} B</span>
            <span v-if="errorCount" class="ml-auto text-amber-600">{{ errorCount }} 项待补</span>
            <span v-else class="ml-auto text-emerald-600">可以提交</span>
          </button>
          <pre
            v-if="jsonOpen"
            class="max-h-52 overflow-auto border-t border-gray-100 bg-gray-900 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-gray-200"
          >{{ jsonPreview }}</pre>
        </div>
      </template>

      <!-- 粘贴识别 -->
      <div class="rounded-lg border border-dashed border-gray-300 bg-white">
        <button
          type="button"
          class="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[11px] text-gray-600 hover:bg-gray-50"
          @click="togglePaste"
        >
          <span class="text-gray-400">{{ pasteOpen ? '▾' : '▸' }}</span>
          粘贴已有 JSON，自动识别模板
        </button>
        <div v-if="pasteOpen" class="border-t border-gray-100 p-2.5">
          <textarea
            v-model="pasteText"
            rows="4"
            spellcheck="false"
            class="w-full resize-y rounded-md border border-gray-300 px-2 py-1.5 font-mono text-[11px] focus:border-blue-500 focus:outline-none"
            placeholder='{"host": "smtp.exmail.qq.com", "port": 465, "username": "…", "password": "…"}'
          />
          <div class="mt-1.5 flex items-center gap-2">
            <p class="min-w-0 flex-1 text-[11px]" :class="pasteError ? 'text-amber-600' : 'text-gray-500'">
              {{ pasteMsg || '按 key 命中率匹配内置模板，套用后可以直接改字段。' }}
            </p>
            <button
              type="button"
              class="shrink-0 rounded-md border border-gray-300 px-2 py-1 text-[11px] text-gray-700 hover:bg-gray-50"
              @click="applyPasted"
            >识别并套用</button>
          </div>
        </div>
      </div>
    </div>

    <p class="mt-2 text-[11px] leading-snug" :class="secretType === 'variable' ? 'text-sky-700' : 'text-amber-600'">
      <template v-if="secretType === 'variable'">
        变量类型可读可复制；结构化的变量在列表里显示非敏感字段拼成的摘要。
      </template>
      <template v-else>
        提交后不可再查看，只能轮换或停用。结构化内容按 JSON 原样保存，不会被 trim。
      </template>
    </p>
  </div>
</template>
