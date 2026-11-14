<script lang="ts">
/**
 * 规则的结构。放在普通 ``script`` 块里而不是 ``script setup``：后者不允许
 * ``export``，而调用方（McpServerRegistry 等）要能拿到这个类型回填草稿。
 */
export type LocatorRule = {
  key_source: string
  transforms: string[]
  templates: string[]
  target: string
  match: string
  on_miss: string
}

const TARGET_LABEL: Record<string, string> = {
  secret_key: 'secret_key',
  name: '名称',
  category: '分类',
}

const USER_KEY_LABEL: Record<string, string> = {
  'user.email': '我的邮箱',
  'user.user_name': '我的账号名',
  'user.real_name': '我的姓名',
  'user.dept_code': '我的部门',
  'user.user_id': '我的用户 ID',
}

/**
 * 一句话摘要，与后端 ``rule_summary`` 同一口径。
 *
 * 抽成导出函数而不是各界面各写一遍：措辞不一致会让人以为是两条不同的规则。
 */
export function ruleSummary(rule: LocatorRule | null | undefined): string {
  if (!rule) return ''
  const target = TARGET_LABEL[rule.target] || rule.target
  const source = rule.key_source.startsWith('arg:')
    ? `入参 ${rule.key_source.slice(4)}`
    : USER_KEY_LABEL[rule.key_source] || rule.key_source
  return `${target} = ${rule.templates[0] || '{value}'}（取${source}）`
}

</script>

<script setup lang="ts">
/**
 * 凭据定位规则编辑器。
 *
 * 一个槽位可以是三种来源之一：模型填值、绑死一条凭据、按规则动态定位。这个
 * 组件只管**第三种**——它不碰凭据值，只描述「拿哪个值、怎么变形、去匹配凭据的
 * 哪个字段、匹配不上怎么办」。
 *
 * 为什么单独成组件：MCP 工具参数、MCP 其他 Header、API 工具参数、API 工具 Header、
 * 个人侧槽位，五处入口要的是同一个东西。各写一份必然走形，而走形的地方恰恰是
 * 「规则匹配什么」这种要命语义。
 *
 * 试算走 `POST /api/portal/secrets/locate-preview`：身份固定为当前登录人，
 * 不解密、不写流水。这点必须在界面上说清——否则管理员会以为试算结果代表所有人。
 */
import { computed, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { XMarkIcon } from '@heroicons/vue/24/outline'


const props = withDefaults(
  defineProps<{
    open: boolean
    /** 初始规则；为空时用默认值 */
    rule?: LocatorRule | null
    /** 编辑对象，仅用于标题展示 */
    title?: string
    /** 槽位，仅用于标题展示（不给最终用户解释，只供排查） */
    slot?: string
    /** 可选作 key_source 的入参名（该工具模型可见的普通参数） */
    argOptions?: string[]
    /** 已有规则时是否提供「删除这条规则」。参数入口不需要——改回「模型填写」即是删除 */
    removable?: boolean
  }>(),
  {
    rule: null,
    title: '凭据匹配规则',
    slot: '',
    argOptions: () => [],
    removable: false,
  },
)

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'save', rule: LocatorRule): void
  (e: 'remove'): void
}>()

const TRANSFORMS: { value: string; label: string; hint: string }[] = [
  { value: 'trim', label: '去首尾空格', hint: '收件人常带空格' },
  { value: 'lower', label: '转小写', hint: '大小写不一致时仍能对上' },
  { value: 'strip_domain', label: '去掉 @ 及之后', hint: '用邮箱前缀当凭据 key' },
]

const USER_KEYS = [
  { value: 'user.email', label: '我的邮箱' },
  { value: 'user.user_name', label: '我的账号名' },
  { value: 'user.real_name', label: '我的姓名' },
  { value: 'user.dept_code', label: '我的部门' },
  { value: 'user.user_id', label: '我的用户 ID' },
]

const TARGETS = [
  { value: 'secret_key', label: 'secret_key（精确）' },
  { value: 'name', label: '名称（精确）' },
  { value: 'category', label: '分类（精确）' },
]

function blankRule(): LocatorRule {
  return {
    key_source: props.argOptions.length ? `arg:${props.argOptions[0]}` : 'user.email',
    transforms: ['trim'],
    templates: ['{value}'],
    target: 'secret_key',
    // 凭据 key 入库时被规范成小写，而入参常带大写；默认宽松匹配才不会「配了却不生效」
    match: 'exact_ci',
    on_miss: 'fail',
  }
}

const draft = ref<LocatorRule>(blankRule())
const busy = ref(false)
const errorText = ref('')

// 每次打开都按传入值重置：上一个槽位的规则不能漏到下一个槽位
watch(
  () => props.open,
  (open) => {
    if (!open) return
    errorText.value = ''
    preview.value = null
    draft.value = props.rule ? { ...props.rule, transforms: [...props.rule.transforms], templates: [...props.rule.templates] } : blankRule()
    sampleValue.value = ''
    const first = draft.value.key_source.startsWith('arg:') ? draft.value.key_source.slice(4) : ''
    sampleArg.value = first
  },
)

const isArgKey = computed(() => draft.value.key_source.startsWith('arg:'))
const argName = computed(() => (isArgKey.value ? draft.value.key_source.slice(4) : ''))

/** 身份字段下拉 + 该工具的参数列表，分组展示 */
const keyOptions = computed(() => [
  {
    label: '这次调用的入参',
    options: props.argOptions.map((name) => ({ value: `arg:${name}`, label: `入参 ${name}` })),
  },
  {
    label: '我的身份（补充项：入参里没有可匹配的值时用）',
    options: USER_KEYS,
  },
])

const sampleArg = ref('')
const sampleValue = ref('')

const preview = ref<{
  ok: boolean
  secret_key: string | null
  secret_name: string | null
  candidate_count: number
  steps: { step: string; detail: string }[]
  miss_reason: string | null
  miss_behaviour?: string
} | null>(null)

const onMissHint = computed(() => {
  switch (draft.value.on_miss) {
    case 'model':
      return '匹配不上时，让模型自己挑一条。模型可以先用 list_my_credentials 查看凭据数量及 legacy_choices 中的旧版候选编号（不含名称、备注或值），再在调用里把编号填进 secret_choice_<参数名>；规则命中时它填的编号会被忽略。只有「工具参数」这一级能用这个兜底（MCP 服务参数、API 工具参数）；Header 级不支持，配在 Header 上照样会失败。另外它依赖模型主动去查，确定性不如规则。'
    case 'fallback_fixed':
      return '匹配不上时，回退到同一层级的固定凭据。需要先在这个槽位另绑一条固定凭据，否则匹配不上就是失败。使用流水会标成 success_fallback，方便事后发现「规则其实一直没命中」。'
    default:
      return '匹配不上就直接失败，报错会写明规则要找什么、有多少条可见候选。最可预期的选择，适合命名规范已经稳定的场景。'
  }
})

function addTemplate() {
  if (draft.value.templates.length >= 3) return
  draft.value.templates.push('{value}')
}

function removeTemplate(index: number) {
  if (draft.value.templates.length <= 1) return
  draft.value.templates.splice(index, 1)
}

function toggleTransform(value: string, checked: boolean) {
  const list = draft.value.transforms
  const at = list.indexOf(value)
  if (checked && at < 0) list.push(value)
  if (!checked && at >= 0) list.splice(at, 1)
}

function validate(): string {
  const r = draft.value
  if (!r.key_source) return '请选择用哪个值去匹配'
  if (r.key_source.startsWith('arg:') && !r.key_source.slice(4)) return '请选择一个入参'
  if (!r.templates.length) return '至少要有一条模板'
  if (r.templates.some((t) => !t.includes('{value}'))) return '每条模板都必须包含 {value}'
  if (r.templates.length > 3) return '模板最多 3 条'
  return ''
}

async function runPreview() {
  const bad = validate()
  if (bad) {
    errorText.value = bad
    return
  }
  errorText.value = ''
  busy.value = true
  preview.value = null
  try {
    const sampleArgs: Record<string, string> = {}
    if (isArgKey.value && argName.value && sampleValue.value !== '') {
      sampleArgs[argName.value] = sampleValue.value
    }
    const res = await axios.post('/api/portal/secrets/locate-preview', {
      slot: props.slot,
      rule: {
        ...draft.value,
        // 结果里按模板顺序展示，空模板没必要发过去
        templates: draft.value.templates.filter((t) => t.trim()),
      },
      sample_args: sampleArgs,
    })
    const data = res.data?.data
    preview.value = data
      ? {
          ok: Boolean(data.ok),
          secret_key: data.secret_key ?? null,
          secret_name: data.secret_name ?? null,
          candidate_count: Number(data.candidate_count || 0),
          steps: Array.isArray(data.steps) ? data.steps : [],
          miss_reason: data.miss_reason ?? null,
          miss_behaviour: data.miss_behaviour,
        }
      : null
  } catch (e: any) {
    errorText.value = e?.response?.data?.detail || '试算失败'
  } finally {
    busy.value = false
  }
}

function close() {
  emit('update:open', false)
}

function removeRule() {
  emit('remove')
  emit('update:open', false)
}

function save() {
  const bad = validate()
  if (bad) {
    errorText.value = bad
    return
  }
  emit('save', {
    ...draft.value,
    templates: draft.value.templates.map((t) => t.trim()).filter(Boolean),
  })
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[120] flex items-start justify-center overflow-y-auto bg-gray-900/40 p-4 sm:items-center"
      @click.self="close"
    >
      <div class="w-full max-w-3xl rounded-xl bg-white shadow-2xl">
        <div class="flex items-start justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <div class="min-w-0">
            <h3 class="text-base font-semibold text-gray-900">{{ title }}</h3>
            <p v-if="slot" class="mt-0.5 truncate font-mono text-[11px] text-gray-400">{{ slot }}</p>
          </div>
          <button
            type="button"
            class="shrink-0 rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            @click="close"
          >
            <XMarkIcon class="h-4 w-4" />
          </button>
        </div>

        <div class="max-h-[70vh] space-y-3 overflow-y-auto px-5 py-4">
          <!-- ① 用哪个值 -->
          <div class="grid grid-cols-[76px_minmax(0,1fr)] items-start gap-3">
            <label class="pt-1.5 text-xs text-gray-500">① 用哪个值</label>
            <div class="min-w-0">
              <select
                v-model="draft.key_source"
                class="w-full rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
              >
                <optgroup v-for="group in keyOptions" :key="group.label" :label="group.label">
                  <option v-for="opt in group.options" :key="opt.value" :value="opt.value">
                    {{ opt.label }}
                  </option>
                </optgroup>
              </select>
              <p class="mt-1 text-[11px] leading-relaxed text-gray-400">
                只列得出模型可见的参数——凭据参数本身不会出现，否则就成了「用一条凭据决定另一条凭据」。
              </p>
            </div>
          </div>

          <!-- ② 怎么变形 -->
          <div class="grid grid-cols-[76px_minmax(0,1fr)] items-start gap-3">
            <label class="pt-1.5 text-xs text-gray-500">② 怎么变形</label>
            <div class="flex flex-wrap gap-x-4 gap-y-1">
              <label
                v-for="t in TRANSFORMS"
                :key="t.value"
                class="inline-flex cursor-pointer items-center gap-1.5 text-xs text-gray-700"
                :title="t.hint"
              >
                <input
                  type="checkbox"
                  class="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                  :checked="draft.transforms.includes(t.value)"
                  @change="toggleTransform(t.value, ($event.target as HTMLInputElement).checked)"
                />
                {{ t.label }}
              </label>
            </div>
          </div>

          <!-- ③ 套成什么 -->
          <div class="grid grid-cols-[76px_minmax(0,1fr)] items-start gap-3">
            <label class="pt-1.5 text-xs text-gray-500">③ 套成什么</label>
            <div class="space-y-1.5">
              <div v-for="index in draft.templates.length" :key="index" class="flex items-center gap-1.5">
                <span class="w-4 shrink-0 text-[11px] text-gray-400">{{ index }}</span>
                <input
                  v-model="draft.templates[index - 1]"
                  class="w-full rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
                  placeholder="{value}"
                />
                <button
                  v-if="draft.templates.length > 1"
                  type="button"
                  class="shrink-0 rounded px-1.5 py-1 text-[10px] text-gray-400 hover:bg-gray-100 hover:text-rose-600"
                  @click="removeTemplate(index - 1)"
                >
                  删除
                </button>
              </div>
              <button
                v-if="draft.templates.length < 3"
                type="button"
                class="text-[11px] font-medium text-indigo-600 hover:text-indigo-700"
                @click="addTemplate"
              >
                + 再加一条（最多 3 条，按顺序尝试，第一条命中的生效）
              </button>
              <p class="text-[11px] leading-relaxed text-gray-400">
                <code>{value}</code> 是上一步算出来的值。可以写 <code>{value}-smtp</code>、<code>mail:{value}</code>。
              </p>
            </div>
          </div>

          <!-- ④ 匹配凭据的什么 -->
          <div class="grid grid-cols-[76px_minmax(0,1fr)] items-center gap-3">
            <label class="text-xs text-gray-500">④ 匹配凭据的</label>
            <div class="flex flex-wrap items-center gap-2">
              <select
                v-model="draft.target"
                class="rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
              >
                <option v-for="t in TARGETS" :key="t.value" :value="t.value">{{ t.label }}</option>
              </select>
              <select
                v-model="draft.match"
                class="rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
              >
                <option value="exact_ci">不区分大小写（推荐）</option>
                <option value="exact">区分大小写</option>
              </select>
              <span class="text-[11px] text-gray-400">只在你能看到的凭据里找</span>
            </div>
          </div>

          <!-- ⑤ 匹配不上时 -->
          <div class="grid grid-cols-[76px_minmax(0,1fr)] items-start gap-3">
            <label class="pt-1.5 text-xs text-gray-500">⑤ 挑不到时</label>
            <div class="min-w-0">
              <select
                v-model="draft.on_miss"
                class="w-full rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
              >
                <option value="fail">直接报错，这次调用失败</option>
                <option value="fallback_fixed">回退到同一层级的固定凭据</option>
                <option value="model">让模型在可见候选里挑一条</option>
              </select>
              <p
                class="mt-1.5 rounded-lg border px-3 py-2 text-[11px] leading-relaxed"
                :class="draft.on_miss === 'model'
                  ? 'border-amber-200 bg-amber-50 text-amber-800'
                  : 'border-gray-200 bg-gray-50 text-gray-600'"
              >
                {{ onMissHint }}
              </p>
            </div>
          </div>

          <!-- 试算 -->
          <div class="rounded-lg border border-gray-200 bg-gray-50/60 px-3 py-3">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="text-xs font-semibold text-gray-700">试算（不发真实请求、不解密、不写流水）</span>
              <span class="text-[10px] text-gray-400">身份固定为当前登录人，不代表别人</span>
            </div>
            <div class="mt-2 flex flex-wrap items-center gap-2">
              <template v-if="isArgKey">
                <label class="text-[11px] text-gray-500">入参 {{ argName || '?' }} =</label>
                <input
                  v-model="sampleValue"
                  class="w-40 rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
                  placeholder="填一个样例值"
                  @keyup.enter="runPreview"
                />
              </template>
              <span v-else class="text-[11px] text-gray-400">用的是你的账号信息，不需要填样例</span>
              <button
                type="button"
                class="rounded-md bg-indigo-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                :disabled="busy"
                @click="runPreview"
              >
                {{ busy ? '试算中…' : '试算' }}
              </button>
            </div>

            <p v-if="errorText" class="mt-2 text-[11px] text-rose-600">{{ errorText }}</p>

            <div v-if="preview" class="mt-2 space-y-1.5">
              <div
                class="rounded-md border px-2.5 py-1.5 text-[11px]"
                :class="preview.ok
                  ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                  : 'border-amber-200 bg-amber-50 text-amber-800'"
              >
                <template v-if="preview.ok">
                  <strong>命中 {{ preview.secret_key }}</strong>
                  <span v-if="preview.secret_name">（{{ preview.secret_name }}）</span>
                  —— 直接注入，不会调用模型。
                </template>
                <template v-else>
                  未命中：{{ preview.miss_reason }}
                  <span v-if="preview.miss_behaviour">→ {{ preview.miss_behaviour }}</span>
                </template>
              </div>
              <div class="space-y-0.5">
                <div
                  v-for="(step, index) in preview.steps"
                  :key="index"
                  class="flex gap-2 text-[11px] text-gray-500"
                >
                  <span class="text-gray-300">{{ index + 1 }}.</span>
                  <span class="shrink-0 text-gray-400">{{ step.step }}</span>
                  <span class="min-w-0 break-all font-mono">{{ step.detail }}</span>
                </div>
              </div>
            </div>
          </div>

          <p class="rounded-lg border border-gray-200 bg-white px-3 py-2 text-[11px] leading-relaxed text-gray-500">
            规则里没有凭据 ID，它只描述「怎么找」。命中之后仍要过四道闸门（你能否看到 → 是否授权 → 状态与有效期 → 解密与流水），
            所以改规则永远不等于能读别人的凭据。
          </p>
        </div>

        <div class="flex items-center gap-2 border-t border-gray-100 px-5 py-3">
          <button
            v-if="removable && rule"
            type="button"
            class="rounded-md border border-rose-200 bg-white px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50"
            @click="removeRule"
          >
            删除这条规则
          </button>
          <div class="flex-1"></div>
          <button
            type="button"
            class="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
            @click="close"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700"
            @click="save"
          >
            保存规则
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
