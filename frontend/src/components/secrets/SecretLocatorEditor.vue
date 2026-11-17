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
  /**
   * 结构约定：命中的凭据必须都是这个模板（`smtp` 等）；空 = 不限。
   * 有了它，字段集合在配置期就确定，取用字段能给成下拉；运行时挑到模板不符的
   * 凭据按未命中处理。
   */
  value_template?: string
  /**
   * ``target='field'`` 时匹配凭据里哪个字段的值（如 `username`）。
   *
   * 「调用方给了邮箱，找 username 字段等于这个邮箱的凭据」是最常见的诉求，而
   * secret_key 是标识、name 是给人看的中文描述、category 是分类，三个都接不住
   * 业务值。这一项会进规则 JSON（后端 parse_rule 认得）。
   */
  target_field?: string
  /**
   * 取用字段（点分隔路径）；空 = 整段。
   * **不在规则 JSON 里**，它落在绑定记录上——保存时由调用方拆出去单独提交，
   * 否则后端的 `parse_rule` 会因为未知字段拒收。
   */
  value_path?: string
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
  // 「让模型挑」没有取值来源，走下面会把空串拼成「（取）」——后端
  // `rule_summary` 和多值面板都判了这一支，只有这里漏了
  if (!rule.key_source && rule.on_miss === 'model') {
    return rule.value_template ? `由模型挑 · 模板 ${rule.value_template}` : '由模型在可见凭据里挑一条'
  }
  const target =
    rule.target === 'field' && rule.target_field
      ? `字段 ${rule.target_field}`
      : TARGET_LABEL[rule.target] || rule.target
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
 * 不发请求、不写流水。按标识 / 名称 / 分类匹配时不碰凭据值；选了「凭据里的字段值」
 * 时后端必须先解密才能比（只返回命中与否，不回传字段值）。这点必须在界面上说清——
 * 否则管理员会以为试算结果代表所有人。
 */
import { computed, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { CREDENTIAL_TEMPLATES, findCredentialTemplate } from '@/utils/credentialTemplates'
import { ChevronDownIcon, XMarkIcon } from '@heroicons/vue/24/outline'


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
    /**
     * 是否显示「取用字段」（那条「这条绑定取哪一段」的下拉）。
     *
     * 参数入口（单值凭据 tab）传 false：那里的语义是「这个参数拿一个整段值」，
     * 规则命中的凭据本来就该整段注入。摆一个「取用字段」出来等于问用户一个
     * 他没法回答的问题——命中哪条凭据运行时才知道，此刻下拉里根本没有字段可选，
     * 只有一个空框和一句「留空 = 整段」的说明。
     *
     * 多值凭据 tab 也传 false，但理由不同：那里的取用字段在**映射表每一行**
     * （同一份凭据的不同字段喂给不同参数），弹窗里再放一份保存时会被丢掉。
     */
    /**
   * 试算要带上消费者类型：后端据此推断凭据场景（mcp / api_tool / script_env）。
   * 不带的话场景过滤永远不生效——「只授权给 api_tool 的凭据」会在 mcp 工具的试算里
   * 显示命中，真实调用却被过滤掉，又是一次「试算说行、跑起来不行」。
   */
  consumerType?: string
  showValuePath?: boolean
    /**
     * 是否显示「模板约束」。
     *
     * 它和取用字段挤在同一节里，但完全是两件事：模板约束声明「命中的凭据都长这样」，
     * 运行时据此把结构不符的凭据判为未命中；取用字段是「取哪一段」。
     *
     * 用一个开关会连带关掉另一个——上一版就是这样，多值页关掉取用字段后，
     * 模板下拉也一起消失了，新配的规则再也没法限定结构，字段只能手填。
     */
    showValueTemplate?: boolean
    /**
     * 这个槽位能不能用 `on_miss=model`（让模型挑）。
     *
     * Header 级不能：模型回填编号用的 `secret_choice_<参数名>` 是工具参数，
     * 请求头里没有地方放它。后端 `bind_rule` 也会拒，但等弹窗提交才报错时，
     * 用户已经填完一整张规则表——选项在这里就不该出现。
     */
    allowModelChoice?: boolean
  }>(),
  {
    rule: null,
    title: '凭据匹配规则',
    slot: '',
    argOptions: () => [],
    removable: false,
    showValuePath: true,
    showValueTemplate: true,
    allowModelChoice: true,
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

const TEMPLATE_OPTIONS = CREDENTIAL_TEMPLATES.map((t) => ({ value: t.id, label: `${t.name} · ${t.id}` }))

/** 选定模板后字段集合就确定了——和「固定凭据」一样能下拉，不用手打字段名 */
const templateFields = computed(() => {
  const tpl = findCredentialTemplate(draft.value.value_template)
  if (!tpl) return null
  return tpl.fields.map((f) => ({ value: f.key, label: `${f.key} · ${f.label}`, secret: Boolean(f.secret) }))
})

const TARGETS = [
  { value: 'secret_key', label: 'secret_key（精确）' },
  { value: 'name', label: '名称（精确）' },
  { value: 'category', label: '分类（精确）' },
  // 前三个都是元数据；业务值（邮箱、租户号）只存在凭据的字段里
  { value: 'field', label: '凭据里的字段值' },
]

function blankRule(): LocatorRule {
  return {
    key_source: props.argOptions.length ? `arg:${props.argOptions[0]}` : 'user.email',
    transforms: ['trim'],
    templates: ['{value}'],
    target: 'secret_key',
    target_field: '',
    // 凭据 key 入库时被规范成小写，而入参常带大写；默认宽松匹配才不会「配了却不生效」
    match: 'exact_ci',
    on_miss: 'fail',
    value_template: '',
    value_path: '',
  }
}

const draft = ref<LocatorRule>(blankRule())
const busy = ref(false)
const errorText = ref('')

/**
 * 两种配法。
 *
 * `rule` 是「按字段精确匹配」：从某个值算出 key，再拿去和凭据比。
 * `model` 是「让模型挑」：没有可算的 key，运行时把可见凭据列给模型由它选。
 *
 * 后者原先只能靠 `on_miss=model`（藏在第⑥步「挑不到时」）实现，读起来像补救
 * 措施，于是所有人都默认「必须配一条匹配规则」，然后随手挑一个入参——取不到
 * 值就报「规则未命中」，而界面上完全看不出配错了。分成两个 tab 是为了让
 * 「我不知道该用哪个入参」这件事有地方可说。
 */
type LocatorMode = 'rule' | 'model'
/** 「让模型挑」的说明默认折叠：它只在选这个 tab 时需要读一遍，之后就是噪音 */
const modelHelpOpen = ref(false)

const MODES: { value: LocatorMode; label: string; hint: string }[] = [
  { value: 'rule', label: '按字段匹配', hint: '用一个值去精确匹配凭据的 key / 名称 / 字段值' },
  { value: 'model', label: '让模型挑', hint: '不配匹配规则，运行时把可见凭据列给模型由它选' },
]

/**
 * 当前槽位能选的配法。
 *
 * `allowModelChoice=false` 时必须把整个 tab 拿掉，不能只藏 ⑥ 里的那个 option：
 * 藏了 option 而 tab 还在，用户照样能切进来填完一整张表再被后端 400——比一开始
 * 就不给这个入口差得多。
 */
const visibleModes = computed(() =>
  props.allowModelChoice ? MODES : MODES.filter((m) => m.value !== 'model'),
)

/**
 * 当前是哪种配法。以草稿内容为准而不是另存一个开关：外部传进来的既有规则
 * （`on_miss=model` 且没有 key_source）打开时就该落在「让模型挑」上。
 */
const mode = computed<LocatorMode>(() =>
  !draft.value.key_source && draft.value.on_miss === 'model' ? 'model' : 'rule',
)

function switchMode(next: LocatorMode) {
  if (next === mode.value) return
  const fallback = blankRule()
  if (next === 'model') {
    // 清掉来源是这套配法的定义：后端要求 on_miss=model 才允许空的 key_source
    draft.value.key_source = ''
    draft.value.on_miss = 'model'
    // `target` 也要带回默认值：第④步（拿什么去比）在「让模型挑」下整块隐藏，
    // 如果它停在 `field`，`validate` 仍会要求填 `target_field`——而界面上根本
    // 没有那个输入框，用户填完了也永远存不下去。
    draft.value.target = fallback.target
    // ②③ 同理整块隐藏。残留的模板可能不含 `{value}`（用户在上一页清空或改坏过），
    // 而 `validate` 会拿它拦下保存，同样看不到可以改的地方。
    draft.value.templates = [...fallback.templates]
    // 这条只在旧规则里出现过，留着会被提交上去
    draft.value.target_field = ''
  } else {
    // 切回来要给出可用的默认值，否则 validate 会立刻拦住一个空来源
    draft.value.key_source = fallback.key_source
    draft.value.on_miss = 'fail'
  }
  errorText.value = ''
  preview.value = null
}

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
      return '匹配不上时，让模型自己挑一条。模型可以先用 list_my_credentials 查你有哪些凭据（只有编号、名称、备注，没有值），再在调用里把编号填进 secret_choice_<参数名>；规则命中时它填的编号会被忽略。只有「工具参数」这一级能用这个兜底（MCP 服务参数、API 工具参数）；Header 级不支持，配在 Header 上照样会失败。另外它依赖模型主动去查，确定性不如规则。'
    case 'fallback_fixed':
      return '匹配不上时，回退到同一层级的固定凭据。需要先在这个槽位另绑一条固定凭据，否则匹配不上就是失败。使用流水会标成 success_fallback，方便事后发现「规则其实一直没命中」。'
    default:
      return '匹配不上就直接失败，报错会写明规则要找什么、有多少条可见候选。最可预期的选择，适合命名规范已经稳定的场景。'
  }
})

// 换了模板约束，原来选的字段可能不在这个模板里 → 当场清掉，不留到运行时才失败
watch(
  () => draft.value.value_template,
  () => {
    const fields = templateFields.value
    if (fields && draft.value.value_path && !fields.some((f) => f.value === draft.value.value_path)) {
      draft.value.value_path = ''
    }
  },
)

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
  // 「让模型挑」没有取值来源：它压根不算 key，不该被这条拦住
  if (mode.value === 'rule' && !r.key_source) return '请选择用哪个值去匹配'
  if (r.key_source.startsWith('arg:') && !r.key_source.slice(4)) return '请选择一个入参'
  // ②③ 只在「按字段匹配」里出现；「让模型挑」没有取值来源，也就没有模板
  if (mode.value === 'rule') {
    if (!r.templates.length) return '至少要有一条模板'
    if (r.templates.some((t) => !t.includes('{value}'))) return '每条模板都必须包含 {value}'
  }
  if (r.templates.length > 3) return '模板最多 3 条'
  // 按字段值匹配必须说清是哪个字段。不拦的话后端会拒，但错误出现在一个已经填了
  // 一堆东西的表单下面，用户得自己找是哪一项没填。
  // 「让模型挑」没有第④步，target_field 无从填写；按配法跳过，而不是按残留值
  if (mode.value === 'rule' && r.target === 'field') {
    const field = (r.target_field || '').trim()
    if (!field) return '选了「凭据里的字段值」，还得填是哪个字段（例如 username）'
    // 与 parse_rule 的正则同口径；在这里拦一道，用户能立刻看到哪一项不对，
    // 而不是等外层整表提交时才收到一句后端报错
    if (!/^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/.test(field)) {
      return '字段名只能用字母、数字、下划线、点和短横线，并以字母或下划线开头'
    }
  }
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
    // value_path 落在绑定记录上，不属于规则 JSON；混进来会被 parse_rule 判为未知字段。
    // 归一化和保存走同一份逻辑：试算能过、保存却 400，会让人怀疑是后端随机出错。
    const { value_path: _path, ...ruleBody } = normalizeForSubmit()
    const res = await axios.post('/api/portal/secrets/locate-preview', {
      slot: props.slot,
      consumer_type: props.consumerType || '',
      rule: ruleBody,
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

/**
 * 提交前的归一化。
 *
 * 控件的显示状态和 draft 的内容必须一致：`target_field` 的输入框只在
 * `target === 'field'` 时渲染，但用户先填过再切走时，draft 里那个值还在。
 * 直接提交会被后端判「给了 target_field 但 target 不是 field」整条规则 400，
 * 而弹窗此时已经关闭——用户完全看不出是哪一项填错了。
 *
 * `value_path` / `value_template` 同理（它们由 props 决定显示与否）。
 */
function normalizeForSubmit() {
  const rule = { ...draft.value }
  if (rule.target !== 'field') rule.target_field = ''
  rule.templates = rule.templates.map((t) => t.trim()).filter(Boolean)
  rule.value_path = props.showValuePath ? rule.value_path : ''
  // `value_template` **不能**这么清。它决定字段集合，是规则本身的一部分；
  // 多值页把控件藏起来（模板改在映射表那里选），藏起来不等于要把值抹掉——
  // 抹掉之后保存规则会静默地把用户选的模板清空，字段下拉随即退回手输。
  return rule
}

function close() {
  emit('update:open', false)
}

function removeRule() {
  // 不在这里关窗：交给父组件在删除成功后关。失败时如果已经关了，用户只会看到
  // 一条横幅，重开才知道规则还在；留着窗更直观，也不会误以为删掉了。
  emit('remove')
}

function save() {
  const bad = validate()
  if (bad) {
    errorText.value = bad
    return
  }
  emit('save', normalizeForSubmit())
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
          <!-- 两种配法：有明确的取值来源就用规则精确匹配，没有就交给模型挑 -->
          <div class="flex gap-1 rounded-lg bg-gray-100 p-1">
            <button
              v-for="m in visibleModes"
              :key="m.value"
              type="button"
              class="flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
              :class="mode === m.value
                ? 'bg-white text-gray-900 shadow-sm'
                : 'text-gray-500 hover:text-gray-700'"
              :title="m.hint"
              @click="switchMode(m.value)"
            >{{ m.label }}</button>
          </div>

          <!-- 「让模型挑」：把边界讲清楚比省字重要，但整段常驻会把「模板约束」挤到
               折叠线以下。默认收起，标题说清里面有什么。 -->
          <div v-if="mode === 'model'" class="rounded-lg border border-amber-200 bg-amber-50 text-[11px] text-amber-900">
            <button
              type="button"
              class="flex w-full items-center gap-2 px-3 py-2 text-left font-medium"
              @click="modelHelpOpen = !modelHelpOpen"
            >
              <span>怎么挑 · 什么时候可能不准 · 边界</span>
              <ChevronDownIcon
                class="ml-auto h-3.5 w-3.5 shrink-0 transition-transform"
                :class="modelHelpOpen ? 'rotate-180' : ''"
              />
            </button>
            <div v-if="modelHelpOpen" class="space-y-1 border-t border-amber-200/70 px-3 py-2 leading-relaxed">
            <p class="font-semibold">运行时怎么挑</p>
            <p class="mt-1">
              把<strong>你能用的凭据</strong>列给模型（只有编号 <code>c1</code>/<code>c2</code>、名称、备注和模板，
              <strong>没有任何值</strong>），由模型读这次调用的上下文自己选一条，再把编号填进
              <code>secret_choice_&lt;参数名&gt;</code>。
            </p>
            <p class="mt-1.5 font-semibold">什么时候可能不准</p>
            <ul class="mt-1 list-disc space-y-0.5 pl-4">
              <li>候选里有多条看起来都像的（名称含糊、没有备注）——模型只能猜</li>
              <li>这次调用本身没有能判断身份的线索（比如只说「发一封邮件」）</li>
              <li>模型没主动去查候选、或忘了回填编号——这次调用会失败，不会静默用错凭据</li>
            </ul>
            <p class="mt-1.5 font-semibold">边界</p>
            <ul class="mt-1 list-disc space-y-0.5 pl-4">
              <li>只有「工具参数」这一级可用（MCP 服务参数、API 工具参数）；Header 级不支持</li>
              <li>模型填的编号不在可见集合内会被直接拒绝，越不了权</li>
              <li>确定性低于按字段匹配：命名规范稳定的场景，用左边那个 tab 更可靠</li>
            </ul>
            <p class="mt-1.5">
              {{ showValueTemplate ? '下面的' : '映射表那里的' }}<strong>凭证内容模板</strong>尤其重要：
              不限定的话，模型可能挑到一条结构完全不同的凭据，取字段时才失败。
            </p>
            </div>
          </div>

          <template v-if="mode === 'rule'">
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
              <!-- 按字段值匹配：得说清是哪个字段，否则没法比 -->
              <input
                v-if="draft.target === 'field'"
                v-model="draft.target_field"
                type="text"
                placeholder="凭据里的字段名，如 username"
                class="w-44 rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
              />
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

          </template>

          <!-- ⑤ 结构约束：模板与取用字段是两个开关，见 showValueTemplate / showValuePath。
               挤在一起用一个开关，关掉其中一个会连带关掉另一个。 -->
          <div
            v-if="showValueTemplate || showValuePath"
            class="grid grid-cols-[76px_minmax(0,1fr)] items-start gap-3"
          >
            <label class="pt-1.5 text-xs text-gray-500">
              {{ showValuePath ? '⑤ 取用字段' : '⑤ 凭证内容模板' }}
            </label>
            <div class="min-w-0 space-y-2">
              <select
                v-if="showValueTemplate"
                v-model="draft.value_template"
                class="w-full rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
              >
                <option value="">不限定（字段只能手填）</option>
                <option v-for="t in TEMPLATE_OPTIONS" :key="t.value" :value="t.value">{{ t.label }}</option>
              </select>
              <p
                v-if="showValueTemplate"
                class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-[11px] leading-relaxed text-gray-600"
              >
                挑的是「哪一条凭据」，运行时才知道。声明一个模板就是声明「命中的凭据都长这样」：
                字段集合随之确定，下面可以直接选；模板不符的凭据按<b>未命中</b>处理，不会拿另一种结构去取值。
              </p>

              <select
                v-if="showValuePath && templateFields"
                v-model="draft.value_path"
                class="w-full rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
              >
                <option value="">整段（结构化凭据会被拒绝）</option>
                <option v-for="f in templateFields" :key="f.value" :value="f.value">
                  {{ f.label }}{{ f.secret ? '（敏感）' : '' }}
                </option>
              </select>
              <input
                v-else-if="showValuePath"
                v-model="draft.value_path"
                class="w-full rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
                placeholder="字段名，如 password；留空 = 整段"
              />
              <p v-if="showValuePath" class="text-[11px] leading-relaxed text-gray-500">
                留空表示整段注入，只适用于单值凭据（一个 token）。命中<b>结构化</b>凭据时不会
                默认把整份 JSON 灌进去，而是拒绝并提示补字段。
              </p>
            </div>
          </div>

          <!-- ⑥ 匹配不上时。「让模型挑」没有匹配这一步，自然也没有「挑不到」 -->
          <div v-if="mode === 'rule'" class="grid grid-cols-[76px_minmax(0,1fr)] items-start gap-3">
            <label class="pt-1.5 text-xs text-gray-500">
              {{ (showValueTemplate || showValuePath) ? '⑥ 挑不到时' : '⑤ 挑不到时' }}
            </label>
            <div class="min-w-0">
              <select
                v-model="draft.on_miss"
                class="w-full rounded-md border-gray-300 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
              >
                <option value="fail">直接报错，这次调用失败</option>
                <option value="fallback_fixed">回退到同一层级的固定凭据</option>
                <option v-if="allowModelChoice" value="model">让模型在可见候选里挑一条</option>
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

          <!-- 「让模型挑」没法试算：选哪条凭据是模型在真实调用时决定的，
               这里能验的只有「模板约束下有几条候选」——写清楚比给个必定未命中的
               结果强。
               试算 -->
          <div
            v-if="mode === 'model'"
            class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-[11px] leading-relaxed text-gray-600"
          >
            这一页没法试算：选哪条凭据要在真实调用时才知道。想先确认候选范围，
            填上模板约束后去「凭据中心」看该模板下有几条凭据。
          </div>
          <div v-else class="rounded-lg border border-gray-200 bg-gray-50/60 px-3 py-3">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="text-xs font-semibold text-gray-700">试算（不发真实请求、不写流水；按字段值匹配需解密比较）</span>
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

          <!-- 报错必须放在两个分支**外面**：原先它在「按字段匹配」那一支里面，
               于是「让模型挑」时保存失败既不关窗、界面上也一个字都看不到，
               只剩「点了没反应」；也不能插在 v-if 与 v-else 中间，否则会拆散配对。 -->
          <p v-if="errorText" class="text-[11px] text-rose-600">{{ errorText }}</p>

          <!-- 安全边界说明，两种配法都要有，但主体不同：规则是「怎么找」，模型挑是「谁来挑」 -->
          <p class="rounded-lg border border-gray-200 bg-white px-3 py-2 text-[11px] leading-relaxed text-gray-500">
            <template v-if="mode === 'model'">
              模型只能从<strong>你能看到的凭据</strong>里挑，编号不在这个集合内会被直接拒绝。
              选中之后仍要过状态与有效期、解密与流水，所以让它挑也读不到别人的凭据。
            </template>
            <template v-else>
              规则里没有凭据 ID，它只描述「怎么找」。命中之后仍要过四道闸门（你能否看到 → 是否授权 → 状态与有效期 → 解密与流水），
              所以改规则永远不等于能读别人的凭据。
            </template>
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
